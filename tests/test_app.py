from __future__ import annotations

import gc
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from cover_syncer import app as gui
from cover_syncer.media import MediaError, has_audio_stream
from cover_syncer.sync import SyncResult


@pytest.fixture(scope="module")
def qt_app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def wait_idle(window, qt_app):
    deadline = time.monotonic() + 15
    while window.busy and time.monotonic() < deadline:
        qt_app.processEvents()
        QTest.qWait(10)
    assert not window.busy, "Worker did not finish within 15 seconds"
    qt_app.processEvents()


@pytest.fixture
def window(qt_app, monkeypatch, marked_media, tmp_path):
    errors, successes = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[2]))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: successes.append(args[2]))
    widget = gui.MainWindow()
    widget.video_input.setText(str(marked_media[1]))
    widget.audio_input.setText(str(marked_media[2]))
    widget.output_input.setText(str(tmp_path / "gui.mp4"))
    widget.test_errors = errors
    widget.test_successes = successes
    yield widget
    wait_idle(widget, qt_app)
    widget.close()
    widget.deleteLater()
    qt_app.processEvents()


def test_manual_export_without_analysis(window, qt_app):
    assert window.auto_offset_ms is None
    assert window.final_offset_ms() == 0
    assert window.export_button.isEnabled()
    window.manual_adjust.setValue(300)
    window.start_export()
    assert window.busy
    gc.collect()  # The worker must survive after start_export returns.
    wait_idle(window, qt_app)
    assert not window.test_errors
    assert len(window.test_successes) == 1
    assert has_audio_stream(window.output_input.text())
    assert window.export_thread is None and window.export_worker is None
    assert window.export_button.isEnabled()


def test_repeated_worker_cleanup_stays_on_gui_thread(window, qt_app, monkeypatch):
    original_set_busy = window.set_busy

    def checked_set_busy(value):
        assert QThread.currentThread() == qt_app.thread()
        original_set_busy(value)

    monkeypatch.setattr(window, "set_busy", checked_set_busy)

    def fail(*args, **kwargs):
        raise MediaError("expected test failure")

    monkeypatch.setattr(gui, "analyze_media", fail)
    monkeypatch.setattr(gui, "export_synced_video", fail)
    for _ in range(20):
        window.start_analysis()
        gc.collect()
        wait_idle(window, qt_app)
        assert window.analysis_thread is None and window.analysis_worker is None
        window.start_export()
        gc.collect()
        wait_idle(window, qt_app)
        assert window.export_thread is None and window.export_worker is None


def test_no_reference_audio_analysis_recovers_to_manual_export(window, qt_app):
    window.manual_adjust.setValue(-300)
    window.start_analysis()
    wait_idle(window, qt_app)
    assert "没有参考音轨" in window.test_errors[0]
    assert window.auto_offset_ms is None
    assert window.final_offset_ms() == -300
    assert window.export_button.isEnabled()
    assert window.analysis_thread is None and window.analysis_worker is None
    window.start_export()
    wait_idle(window, qt_app)
    assert len(window.test_successes) == 1


@pytest.mark.parametrize("failure", [MediaError("analysis unavailable"), RuntimeError("unexpected")])
def test_analysis_failure_clears_old_estimate_and_restores_controls(window, qt_app, monkeypatch, failure):
    window.analysis_completed(SyncResult(500, 0.9, 100, 50))
    window.manual_adjust.setValue(25)

    def fail(*args):
        raise failure

    monkeypatch.setattr(gui, "analyze_media", fail)
    window.start_analysis()
    wait_idle(window, qt_app)
    assert window.auto_offset_ms is None
    assert window.final_offset_ms() == 25
    assert window.test_errors and not window.test_successes
    assert window.export_button.isEnabled()
    assert all(button.isEnabled() for button in window.file_buttons)


def test_auto_plus_manual_and_switching_inputs(window, qt_app, monkeypatch, marked_media):
    monkeypatch.setattr(gui, "analyze_media", lambda *args: SyncResult(250.4, 0.9, 100, 25))
    window.manual_adjust.setValue(-50)
    window.start_analysis()
    wait_idle(window, qt_app)
    assert window.final_offset_ms() == pytest.approx(200.4)
    window.use_manual_alignment()
    assert window.auto_offset_ms is None
    assert window.final_offset_ms() == 200
    window.video_input.setText(str(marked_media[0]))
    assert window.final_offset_ms() == 0
    assert window.auto_offset_ms is None
    window.analysis_completed(SyncResult(100, 0.9, 100, 10))
    window.manual_adjust.setValue(40)
    window.audio_input.setText(str(marked_media[0]))
    assert window.auto_offset_ms is None and window.final_offset_ms() == 0


def test_busy_lifetime_prevents_concurrent_tasks_and_close(window, qt_app, monkeypatch):
    release = threading.Event()
    calls = []

    def analyze(*args):
        calls.append(args)
        release.wait(5)
        return SyncResult(100, 0.9, 100, 10)

    monkeypatch.setattr(gui, "analyze_media", analyze)
    window.show()
    window.start_analysis()
    try:
        gc.collect()
        window.start_analysis()
        window.start_export()
        assert not window.export_button.isEnabled()
        assert not window.manual_adjust.isEnabled()
        assert all(not button.isEnabled() for button in window.file_buttons)
        assert not window.close()
        assert window.isVisible()
    finally:
        release.set()
        wait_idle(window, qt_app)
    assert len(calls) == 1
    assert window.final_offset_ms() == 100
    assert window.close()


def test_failed_export_does_not_report_success(window, qt_app, monkeypatch):
    def fail(*args, **kwargs):
        raise MediaError("encoding failed")

    monkeypatch.setattr(gui, "export_synced_video", fail)
    window.start_export()
    wait_idle(window, qt_app)
    assert window.test_errors == ["encoding failed"]
    assert not window.test_successes
    assert window.export_button.isEnabled()


def test_gui_overwrite_confirmation_and_input_protection(window, qt_app, monkeypatch):
    from pathlib import Path
    output = Path(window.output_input.text())
    output.write_bytes(b"existing")
    confirmations = []

    def decline(*args):
        confirmations.append(args)
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", decline)
    window.start_export()
    assert len(confirmations) == 1
    assert not window.busy and output.read_bytes() == b"existing"
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    window.start_export()
    wait_idle(window, qt_app)
    assert len(window.test_successes) == 1 and has_audio_stream(output)
    window.output_input.setText(window.video_input.text())
    window.start_export()
    assert "不能与" in window.test_errors[-1]
    assert not window.busy


def test_missing_input_rejected_before_worker_starts(window):
    window.audio_input.setText("missing-file.wav")
    window.start_export()
    assert not window.busy and window.export_thread is None
    assert "文件不存在" in window.test_errors[-1]
