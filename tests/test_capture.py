from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import pytest
import soundfile as sf

from cover_syncer.capture import Recorder, input_args, write_json
from cover_syncer.media import MediaError, probe_streams
from cover_syncer.native_export import export_job
from cover_syncer.sync import SyncResult


def test_camera_entrypoint_starts_and_closes_without_conda_activation(tmp_path: Path, desktop_environment):
    # Starts the real helper and widgets; no device is opened until Prepare.
    write_json(tmp_path / "control.json", dict(serial=1, command="close"))
    process = subprocess.run([sys.executable, "-m", "cover_syncer.capture", str(tmp_path)],
                             env=desktop_environment, capture_output=True, timeout=25)
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    assert json.loads((tmp_path / "status.json").read_text())["phase"] == "closed"


def wait_ready(recorder: Recorder) -> None:
    deadline = time.monotonic() + 10
    while not recorder.ready and time.monotonic() < deadline:
        code = recorder.poll()
        assert code is None, (recorder.folder / "ffmpeg.log").read_text(errors="replace")
        time.sleep(.05)
    assert recorder.ready


def cleanup(recorder: Recorder) -> None:
    if recorder.process.poll() is None:
        recorder.stop()
        recorder.process.wait(timeout=15)
    recorder.log.close()


def test_capture_records_reference_audio_preview_and_finalizes_before_export(tmp_path: Path):
    source = ["-re", "-f", "lavfi", "-i",
              "testsrc2=size=320x180:rate=30[out0];sine=frequency=701:sample_rate=48000[out1]"]
    recorder = Recorder(tmp_path / "camera", source, recording=True)
    try:
        wait_ready(recorder)
        assert recorder.preview.read_bytes().startswith(b"\xff\xd8")
        with pytest.raises(MediaError, match="还在写入"):
            recorder.finish()
        time.sleep(.6)
        recorder.stop()
        recorder.stop()  # A repeated Stop must not corrupt the file or pipe.
        recorder.process.wait(timeout=15)
        video = recorder.finish()
        assert video == recorder.output and video.is_file()
        streams = probe_streams(video)
        assert {s["codec_type"] for s in streams} == {"video", "audio"}
        assert next(s for s in streams if s["codec_type"] == "audio")["codec_name"] == "aac"
        assert not recorder.partial.exists()
        with pytest.raises(FileExistsError):
            Recorder(recorder.folder, source, recording=True)
    finally:
        cleanup(recorder)


def test_capture_without_reference_audio_fails_instead_of_silent_video(tmp_path: Path):
    recorder = Recorder(tmp_path / "bad", ["-f", "lavfi", "-i", "testsrc2=duration=1:size=160x90"], recording=True)
    try:
        recorder.process.wait(timeout=10)
        with pytest.raises(MediaError, match="采集失败"):
            recorder.finish()
        assert not recorder.output.exists()
    finally:
        cleanup(recorder)


def test_device_selectors_do_not_use_shell_and_require_microphone():
    args = input_args("icspring camera", "麦克风 (icspring camera)")
    assert args[-1] == "video=icspring camera:audio=麦克风 (icspring camera)"
    with pytest.raises(MediaError, match="冒号"):
        input_args("Camera: one", "Room mic")
    with pytest.raises(MediaError):
        input_args("Camera", "")


@pytest.mark.parametrize("reliability", ["low", "medium"])
def test_uncertain_camera_alignment_requires_manual_confirmation(tmp_path: Path, monkeypatch, reliability):
    monkeypatch.setattr("cover_syncer.native_export.analyze_media",
                        lambda *a: SyncResult(321, .3, 1.4, 50, reliability=reliability))
    output = tmp_path / "result.mp4"
    result = export_job(dict(version=1, video="video", audio="audio", output=str(output), offset_ms=0, auto_align=True))
    assert result["needs_manual"] and not result["success"]
    assert result["offset_ms"] == 321
    assert not output.exists()


def test_camera_analysis_failure_preserves_manual_route(tmp_path: Path, monkeypatch):
    def fail(*args):
        raise MediaError("No matching reference")
    monkeypatch.setattr("cover_syncer.native_export.analyze_media", fail)
    result = export_job(dict(version=1, video="video", audio="audio", output=str(tmp_path / "out.mp4"),
                            offset_ms=42, auto_align=True))
    assert result["needs_manual"] and result["offset_ms"] == 42


def test_reliable_camera_alignment_exports_using_estimated_offset(marked_media, tmp_path: Path, monkeypatch):
    monkeypatch.setattr("cover_syncer.native_export.analyze_media",
                        lambda *a: SyncResult(300, .99, 100, 50, reliability="high"))
    output = tmp_path / "cover.mp4"
    result = export_job(dict(version=1, video=str(marked_media[0]), audio=str(marked_media[2]),
                            output=str(output), offset_ms=0, auto_align=True))
    assert result["success"] and result["offset_ms"] == 300
    assert {s["codec_type"] for s in probe_streams(output)} == {"video", "audio"}


@pytest.mark.parametrize("action,expected", [("stop", "complete"), ("discard", "discarded")])
def test_camera_panel_waits_for_start_then_finishes_both_streams(tmp_path: Path, monkeypatch, action, expected):
    from PySide6.QtGui import QCloseEvent
    from PySide6.QtWidgets import QApplication
    from cover_syncer import capture_window

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(capture_window, "devices", lambda: {"video": ["Camera"], "audio": ["Room mic"]})

    class FakeProcess:
        code = None
        def poll(self):
            return self.code

    class FakeRecorder:
        def __init__(self, folder, args, *, recording):
            self.recording = recording
            self.ready = True
            self.stopping = False
            self.process = FakeProcess()
            self.preview = tmp_path / "none.jpg"
        def stop(self):
            self.stopping = True
            self.process.code = 0
        def poll(self):
            return self.process.poll()
        def finish(self):
            return tmp_path / "capture.mkv" if self.recording else None

    monkeypatch.setattr(capture_window, "Recorder", FakeRecorder)
    window = capture_window.CaptureWindow(tmp_path)
    try:
        app.processEvents()
        window.begin_preview()
        assert window.recorder is None  # Reference-microphone selection is mandatory.
        window.microphone.setCurrentIndex(1)
        window.begin_preview()
        window.tick()
        assert window.phase == "ready"
        write_json(tmp_path / "control.json", dict(serial=1, command="record"))
        window.tick()
        assert window.phase == "starting"
        window.tick()
        assert window.phase == "recording"
        close = QCloseEvent()
        window.closeEvent(close)
        assert not close.isAccepted()
        write_json(tmp_path / "control.json", dict(serial=2, command=action))
        window.tick()
        status = json.loads((tmp_path / "status.json").read_text())
        assert status["phase"] == expected and status["video"].endswith("capture.mkv")
    finally:
        window.closing = True
        window.close()
        window.deleteLater()
        app.processEvents()


def test_captured_reference_really_aligns_and_composes(tmp_path: Path):
    # A deterministic non-repeating performance with a known 300 ms camera lead-in.
    rate = 48000
    rng = np.random.default_rng(42)
    signal = np.zeros(rate * 8)
    for start in (.45, 1.03, 1.8, 2.15, 3.4, 4.05, 4.72, 5.9, 6.35, 7.2):
        count = int(rate * rng.uniform(.10, .27))
        t = np.arange(count) / rate
        signal[int(start * rate):int(start * rate) + count] += .3 * np.exp(-t * 12) * np.sin(2 * np.pi * rng.uniform(300, 900) * t)
    audio = tmp_path / "instrument.wav"
    reference = tmp_path / "microphone.wav"
    sf.write(audio, signal, rate)
    sf.write(reference, np.pad(signal, (int(.3 * rate), 0)), rate)
    source = tmp_path / "simulated-camera.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=8.3",
                    "-i", str(reference), "-c:v", "libx264", "-c:a", "pcm_s16le", str(source)],
                   capture_output=True, check=True, timeout=20)
    recorder = Recorder(tmp_path / "captured", ["-i", str(source)], recording=True)
    try:
        recorder.process.wait(timeout=20)
        recorded = recorder.finish()
        result = export_job(dict(version=1, video=str(recorded), audio=str(audio),
                                output=str(tmp_path / "cover.mp4"), offset_ms=0, auto_align=True))
        assert result["success"], result
        assert abs(result["offset_ms"] - 300) <= 40
        assert audio.exists() and recorded.exists()
        decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", result["output"], "-vn", "-ac", "1",
                                  "-ar", str(rate), "-f", "f32le", "pipe:1"], capture_output=True,
                                 check=True, timeout=15)
        data = np.frombuffer(decoded.stdout, dtype="<f4")
        audible = np.flatnonzero(abs(data) > .06)
        assert abs(audible[0] / rate - .75) < .04
    finally:
        cleanup(recorder)
