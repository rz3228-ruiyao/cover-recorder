from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .media import VIDEO_FILE_FILTER, MediaError, export_synced_video, validate_export_paths, validate_input_paths
from .sync import SyncError, SyncResult
from .workflow import analyze_media


class AnalyzeWorker(QObject):
    finished = Signal()
    failed = Signal(str)
    completed = Signal(object)

    def __init__(self, video_path: str, audio_path: str):
        super().__init__()
        self.video_path = video_path
        self.audio_path = audio_path

    @Slot()
    def run(self) -> None:
        try:
            result = analyze_media(self.video_path, self.audio_path)
        except (MediaError, SyncError, OSError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"分析失败（{type(exc).__name__}）：{exc}")
        else:
            self.completed.emit(result)
        finally:
            self.finished.emit()


class ExportWorker(QObject):
    finished = Signal()
    failed = Signal(str)
    completed = Signal(str)

    def __init__(self, video_path: str, audio_path: str, output_path: str, offset_ms: float,
                 *, overwrite: bool = False):
        super().__init__()
        self.video_path = video_path
        self.audio_path = audio_path
        self.output_path = output_path
        self.offset_ms = offset_ms
        self.overwrite = overwrite

    @Slot()
    def run(self) -> None:
        try:
            result = export_synced_video(
                self.video_path,
                self.audio_path,
                self.output_path,
                self.offset_ms,
                overwrite=self.overwrite,
            )
        except (MediaError, OSError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"导出失败（{type(exc).__name__}）：{exc}")
        else:
            self.completed.emit(str(result.output_path))
        finally:
            self.finished.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Cover 对轨器")
        self.resize(820, 560)

        self.video_input = QLineEdit()
        self.audio_input = QLineEdit()
        self.output_input = QLineEdit()
        for field in (self.video_input, self.audio_input, self.output_input):
            field.setReadOnly(True)
            field.setMinimumHeight(34)

        self.auto_offset_label = QLabel("-- ms")
        self.confidence_label = QLabel("--")
        self.peak_ratio_label = QLabel("--")
        self.segment_consistency_label = QLabel("--")
        self.reliability_label = QLabel("--")
        self.final_offset_label = QLabel("-- ms")

        self.manual_adjust = QSpinBox()
        self.manual_adjust.setRange(-600000, 600000)
        self.manual_adjust.setSuffix(" ms")
        self.manual_adjust.setSingleStep(10)
        self.manual_adjust.valueChanged.connect(self.update_final_offset)

        self.analyze_button = QPushButton("自动分析偏移")
        self.export_button = QPushButton("导出 MP4")
        self.export_button.setEnabled(False)
        self.analyze_button.clicked.connect(self.start_analysis)
        self.export_button.clicked.connect(self.start_export)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(150)

        self.auto_offset_ms: float | None = None
        self.busy = False
        self.file_buttons: list[QPushButton] = []
        self.analysis_thread: QThread | None = None
        self.export_thread: QThread | None = None
        self.analysis_worker: AnalyzeWorker | None = None
        self.export_worker: ExportWorker | None = None

        self.setCentralWidget(self.build_ui())
        self.video_input.textChanged.connect(self.inputs_changed)
        self.audio_input.textChanged.connect(self.inputs_changed)
        self.apply_style()
        self.reset_analysis()
        self.write_status("选择视频和外部音频后，可直接手动设置偏移并导出；自动分析可选。")

    def build_ui(self) -> QWidget:
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(16)

        title = QLabel("Cover 对轨器")
        title.setObjectName("Title")
        subtitle = QLabel("导入视频和外部音频，手动对齐或自动估计偏移，导出 MP4。")
        subtitle.setObjectName("Subtitle")

        file_box = QFrame()
        file_box.setObjectName("Panel")
        file_layout = QGridLayout(file_box)
        file_layout.setColumnStretch(1, 1)
        file_layout.setHorizontalSpacing(10)
        file_layout.setVerticalSpacing(12)
        self.add_file_row(file_layout, 0, "视频文件", self.video_input, self.choose_video)
        self.add_file_row(file_layout, 1, "高质量音频", self.audio_input, self.choose_audio)
        self.add_file_row(file_layout, 2, "导出位置", self.output_input, self.choose_output)

        result_box = QFrame()
        result_box.setObjectName("Panel")
        result_layout = QFormLayout(result_box)
        result_layout.setLabelAlignment(Qt.AlignRight)
        result_layout.setFormAlignment(Qt.AlignLeft)
        result_layout.setHorizontalSpacing(14)
        result_layout.setVerticalSpacing(12)
        result_layout.addRow("自动估计值", self.auto_offset_label)
        result_layout.addRow("置信度", self.confidence_label)
        result_layout.addRow("峰值比", self.peak_ratio_label)
        result_layout.addRow("切片一致性", self.segment_consistency_label)
        result_layout.addRow("可靠性", self.reliability_label)
        result_layout.addRow("手动修正值", self.manual_adjust)
        result_layout.addRow("最终偏移", self.final_offset_label)
        offset_help = QLabel("最终偏移 = 自动估计（未分析按 0）+ 手动修正。\n"
                             "正值延后外部音频；负值裁去音频开头；0 表示起点不变。\n"
                             "保留视频时长；音频不足补静音，超出截断。")
        offset_help.setWordWrap(True)
        result_layout.addRow(offset_help)

        self.manual_button = QPushButton("清除自动估计，改为手动")
        self.manual_button.clicked.connect(self.use_manual_alignment)

        action_layout = QHBoxLayout()
        action_layout.addWidget(self.analyze_button)
        action_layout.addWidget(self.export_button)
        action_layout.addWidget(self.manual_button)
        action_layout.addStretch()

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(file_box)
        root.addWidget(result_box)
        root.addLayout(action_layout)
        root.addWidget(QLabel("状态"))
        root.addWidget(self.log_output, 1)
        return container

    def add_file_row(self, layout: QGridLayout, row: int, label: str, field: QLineEdit, handler) -> None:
        button = QPushButton("选择")
        self.file_buttons.append(button)
        button.clicked.connect(handler)
        layout.addWidget(QLabel(label), row, 0)
        layout.addWidget(field, row, 1)
        layout.addWidget(button, row, 2)

    def apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QMainWindow {
                background: #f6f7f8;
            }
            QLabel#Title {
                font-size: 24px;
                font-weight: 700;
                color: #172026;
            }
            QLabel#Subtitle {
                color: #50616d;
            }
            QFrame#Panel {
                background: white;
                border: 1px solid #d8dee4;
                border-radius: 8px;
                padding: 12px;
            }
            QLineEdit, QSpinBox, QPlainTextEdit {
                background: #fbfcfd;
                border: 1px solid #cfd7df;
                border-radius: 6px;
                padding: 6px 8px;
            }
            QPushButton {
                background: #1f6feb;
                color: white;
                border: 0;
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:disabled {
                background: #9aa8b4;
            }
            QPushButton:hover:!disabled {
                background: #185abc;
            }
            """
        )

    def choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            "",
            VIDEO_FILE_FILTER,
        )
        if path:
            self.video_input.setText(path)
            self.ensure_default_output()

    def choose_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择高质量音频",
            "",
            "Audio Files (*.wav *.flac *.mp3 *.m4a *.aac);;All Files (*.*)",
        )
        if path:
            self.audio_input.setText(path)

    def choose_output(self) -> None:
        default = self.output_input.text() or str(Path.cwd() / "synced_cover.mp4")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "选择导出位置",
            default,
            "MP4 Video (*.mp4)",
        )
        if path:
            if not path.lower().endswith(".mp4"):
                path += ".mp4"
            self.output_input.setText(path)

    def ensure_default_output(self) -> None:
        if self.output_input.text() or not self.video_input.text():
            return
        video = Path(self.video_input.text())
        self.output_input.setText(str(video.with_name(f"{video.stem}_synced.mp4")))

    @Slot()
    def inputs_changed(self) -> None:
        self.reset_analysis()
        self.write_status("素材已切换，自动估计和手动修正已清零。")

    def reset_analysis(self, *, reset_manual: bool = True) -> None:
        self.auto_offset_ms = None
        self.auto_offset_label.setText("未使用（按 0 ms）")
        self.confidence_label.setText("--")
        self.peak_ratio_label.setText("--")
        self.segment_consistency_label.setText("--")
        self.reliability_label.setText("--")
        if reset_manual:
            self.manual_adjust.setValue(0)
        self.update_final_offset()
        self.set_busy(self.busy)

    @Slot()
    def use_manual_alignment(self) -> None:
        if self.busy:
            return
        final_offset = self.final_offset_ms()
        self.reset_analysis()
        self.manual_adjust.setValue(round(final_offset))
        self.write_status("已清除自动估计，当前最终偏移已转为手动值（取整到毫秒）。")

    def start_analysis(self) -> None:
        if self.busy or not self.require_inputs(require_output=False):
            return
        self.reset_analysis(reset_manual=False)
        self.set_busy(True)
        self.write_status("正在提取音频并分析波形，请稍候...")
        worker = AnalyzeWorker(self.video_input.text(), self.audio_input.text())
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self.analysis_completed, Qt.QueuedConnection)
        worker.failed.connect(self.operation_failed, Qt.QueuedConnection)
        worker.finished.connect(thread.quit, Qt.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self.analysis_finished, Qt.QueuedConnection)
        self.analysis_thread = thread
        self.analysis_worker = worker
        thread.start()

    @Slot()
    def analysis_finished(self) -> None:
        thread = self.analysis_thread
        if thread is not None:
            thread.wait()  # finished is emitted before native thread-local cleanup completes.
            thread.deleteLater()
        self.analysis_worker = None
        self.analysis_thread = None
        self.set_busy(False)

    @Slot(object)
    def analysis_completed(self, result: SyncResult) -> None:
        self.auto_offset_ms = result.offset_ms
        self.auto_offset_label.setText(f"{result.offset_ms:.1f} ms")
        self.confidence_label.setText(f"{result.confidence:.3f}")
        self.peak_ratio_label.setText(format_peak_ratio(result.peak_ratio))
        self.segment_consistency_label.setText(format_segment_consistency(result))
        self.reliability_label.setText(format_reliability(result.reliability))
        self.update_final_offset()
        if result.reliability == "low":
            self.write_status(
                "分析完成，但可靠性较低。建议使用更长片段、确保视频原声能听到演奏，或导出后手动微调。"
            )
        elif result.reliability == "medium":
            self.write_status("分析完成，可靠性中等。建议导出后听一下同步感，必要时微调几十毫秒。")
        else:
            self.write_status("分析完成，峰值和切片一致性都比较稳，可以导出 MP4。")

    def start_export(self) -> None:
        if self.busy or not self.require_inputs(require_output=True):
            return
        try:
            output = validate_export_paths(self.video_input.text(), self.audio_input.text(),
                                           self.output_input.text(), overwrite=True)
        except (MediaError, OSError, ValueError) as exc:
            self.show_error(str(exc))
            return
        overwrite = output.exists()
        if overwrite and QMessageBox.question(
            self, "确认覆盖", f"文件已存在：\n{output}\n\n导出成功后替换它？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        offset = self.final_offset_ms()
        self.set_busy(True)
        self.write_status(f"正在导出，最终偏移 {offset:.1f} ms...")
        worker = ExportWorker(
            self.video_input.text(),
            self.audio_input.text(),
            self.output_input.text(),
            offset,
            overwrite=overwrite,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self.export_completed, Qt.QueuedConnection)
        worker.failed.connect(self.operation_failed, Qt.QueuedConnection)
        worker.finished.connect(thread.quit, Qt.DirectConnection)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self.export_finished, Qt.QueuedConnection)
        self.export_thread = thread
        self.export_worker = worker
        thread.start()

    @Slot()
    def export_finished(self) -> None:
        thread = self.export_thread
        if thread is not None:
            thread.wait()
            thread.deleteLater()
        self.export_worker = None
        self.export_thread = None
        self.set_busy(False)

    @Slot(str)
    def export_completed(self, output_path: str) -> None:
        self.write_status(f"导出完成：{output_path}")
        QMessageBox.information(self, "导出完成", f"已导出：\n{output_path}")

    @Slot(str)
    def operation_failed(self, message: str) -> None:
        if self.analysis_thread is not None:
            message += "\n可以手动设置偏移并导出，无需自动分析成功。"
        self.write_status(f"失败：{message}")
        self.show_error(message)

    def require_inputs(self, *, require_output: bool) -> bool:
        if not self.video_input.text():
            self.show_error("请选择视频文件。")
            return False
        if not self.audio_input.text():
            self.show_error("请选择高质量音频文件。")
            return False
        if require_output and not self.output_input.text():
            self.show_error("请选择导出位置。")
            return False
        try:
            validate_input_paths(self.video_input.text(), self.audio_input.text())
        except (MediaError, OSError, ValueError) as exc:
            self.show_error(str(exc))
            return False
        return True

    def update_final_offset(self) -> None:
        self.final_offset_label.setText(f"{self.final_offset_ms():.1f} ms")

    def final_offset_ms(self) -> float:
        return float(self.auto_offset_ms or 0.0) + float(self.manual_adjust.value())

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.analyze_button.setEnabled(not busy)
        inputs_present = bool(self.video_input.text() and self.audio_input.text())
        self.export_button.setEnabled(not busy and inputs_present)
        self.manual_adjust.setEnabled(not busy)
        self.manual_button.setEnabled(not busy)
        for button in self.file_buttons:
            button.setEnabled(not busy)

    def closeEvent(self, event) -> None:
        if self.busy:
            self.write_status("任务进行中，请等待完成后关闭窗口。")
            event.ignore()
        else:
            super().closeEvent(event)

    def write_status(self, message: str) -> None:
        self.log_output.appendPlainText(message)

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, "操作失败", message)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


def format_peak_ratio(value: float) -> str:
    if value == float("inf"):
        return "∞"
    return f"{value:.1f}x"


def format_segment_consistency(result: SyncResult) -> str:
    if result.segment_count <= 0 or result.segment_consistency_ms is None:
        return "--"
    return (
        f"{result.consistent_segment_count}/{result.segment_count} 段, "
        f"中位差 {result.segment_consistency_ms:.1f} ms"
    )


def format_reliability(value: str) -> str:
    labels = {
        "high": "高",
        "medium": "中",
        "low": "低",
        "single": "单次",
    }
    return labels.get(value, value)
