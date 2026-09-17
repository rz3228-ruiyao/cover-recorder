from __future__ import annotations

import json
from pathlib import Path
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget

from .capture import Recorder, devices, input_args, write_json


class CaptureWindow(QWidget):
    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder
        self.recorder: Recorder | None = None
        self.phase = "setup"
        self.pending = ""
        self.error = ""
        self.serial = 0
        self.run_number = 0
        self.closing = False
        self.camera = QComboBox()
        self.microphone = QComboBox()
        self.preview = QLabel("选择摄像头和参考麦克风后，点击准备。")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(640, 360)
        self.message = QLabel("参考麦克风收现场声，用于对齐；Focusrite 继续由主窗口录音。")
        self.message.setWordWrap(True)
        self.prepare = QPushButton("预览并准备录制")
        layout = QVBoxLayout(self)
        for widget in (QLabel("摄像头"), self.camera, QLabel("视频参考麦克风（选择与乐器声卡不同的输入设备）"),
                       self.microphone, self.preview, self.message, self.prepare):
            layout.addWidget(widget)
        self.setWindowTitle("Cover Recorder — 摄像头与现场声音")
        self.prepare.clicked.connect(self.begin_preview)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.publish("setup")
        QTimer.singleShot(0, self.load_devices)

    def publish(self, phase: str, **extra) -> None:
        self.phase = phase
        write_json(self.folder / "status.json", dict(phase=phase, camera=self.camera.currentText(),
                   microphone=self.microphone.currentText(), **extra))

    def load_devices(self) -> None:
        try:
            found = devices()
            self.camera.addItems(found["video"])
            self.microphone.addItem("请选择参考麦克风", "")
            for name in found["audio"]:
                self.microphone.addItem(name, name)
            if not found["video"]:
                self.message.setText("没有发现摄像头。连接 USB 摄像头后重新打开此窗口。")
                self.prepare.setEnabled(False)
            elif not found["audio"]:
                self.message.setText("没有发现参考麦克风。连接摄像头麦克风或另一只麦克风后重新打开。")
                self.prepare.setEnabled(False)
        except Exception as exc:
            self.fail(str(exc))

    def begin_preview(self) -> None:
        try:
            if not self.microphone.currentData():
                self.message.setText("请先选择参考麦克风。视频必须带现场声音。")
                return
            self.camera.setEnabled(False)
            self.microphone.setEnabled(False)
            self.prepare.setEnabled(False)
            self.run_number += 1
            self.recorder = Recorder(self.folder / f"preview-{self.run_number}",
                                     input_args(self.camera.currentText(), None), recording=False)
            self.publish("preparing")
            self.message.setText("正在打开摄像头…")
        except Exception as exc:
            self.fail(str(exc))

    def fail(self, message: str) -> None:
        self.error = message
        self.message.setText(message + "\n可关闭此面板，检查设备后重新打开。")
        if self.recorder is not None and self.recorder.process.poll() is None:
            self.pending = "error"
            self.recorder.stop()
        self.publish("error", error=message)

    def tick(self) -> None:
        try:
            control = self.folder / "control.json"
            if control.is_file():
                try:
                    command = json.loads(control.read_text(encoding="utf-8-sig"))
                except (OSError, ValueError):
                    command = {}
                if command.get("serial", 0) > self.serial:
                    self.serial = command["serial"]
                    action = command.get("command")
                    if action == "record" and self.phase == "ready":
                        self.pending = "record"
                        self.recorder.stop()
                        self.publish("starting")
                        self.message.setText("正在启动视频和参考麦克风，请等主窗口显示正在录制后演奏…")
                    elif action in ("stop", "discard", "close"):
                        self.pending = action
                        if self.recorder is not None:
                            self.recorder.stop()
                            self.publish("finishing")
                            self.message.setText("正在保存视频和参考声音…")
                        else:
                            self.publish("closed")
                            self.closing = True
                            self.close()
            heartbeat = self.folder / "heartbeat"
            if heartbeat.exists() and time.time() - heartbeat.stat().st_mtime > 20:
                self.pending = "close"
                if self.recorder is not None:
                    self.recorder.stop()
                else:
                    self.publish("closed")
                    self.closing = True
                    self.close()
            if self.recorder is None:
                return
            code = self.recorder.poll()
            if code is None:
                if self.recorder.ready:
                    pixmap = QPixmap(str(self.recorder.preview))
                    if not pixmap.isNull():
                        self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    if self.phase == "preparing":
                        self.publish("ready")
                        self.message.setText("画面已就绪。回到主窗口点击 Record。")
                    elif self.phase == "starting" and self.recorder.recording:
                        self.publish("recording")
                        self.message.setText("正在录制画面和参考声音。请用主窗口 Stop 停止。")
                return
            if not self.recorder.stopping:
                raise RuntimeError("采集意外结束，请检查摄像头或麦克风；已有素材保留。")
            output = self.recorder.finish()
            self.recorder = None
            if self.pending == "record":
                self.recorder = Recorder(self.folder / "recording",
                                         input_args(self.camera.currentText(), self.microphone.currentData()), recording=True)
                self.pending = ""
            elif self.pending == "error":
                self.publish("error", error=self.error)
            else:
                phase = "complete" if self.pending == "stop" and output else "discarded"
                self.publish(phase, video=str(output) if output else "")
                self.closing = True
                self.close()
        except Exception as exc:
            self.fail(str(exc))
            if self.recorder is not None and self.recorder.process.poll() is not None:
                self.recorder.log.close()
                self.recorder = None

    def closeEvent(self, event) -> None:
        if self.closing:
            self.timer.stop()
            event.accept()
        elif self.phase in ("recording", "starting", "finishing"):
            self.message.setText("请先在主窗口点击 Stop 或 Discard Take，等待文件保存完毕。")
            event.ignore()
        elif self.recorder is not None:
            self.pending = "close"
            self.recorder.stop()
            self.publish("finishing")
            event.ignore()
        else:
            self.publish("closed")
            event.accept()

    def cleanup(self) -> None:
        self.timer.stop()
        if self.recorder is not None:
            self.recorder.stop()
            try:
                self.recorder.process.wait(timeout=16)
            except Exception:
                self.recorder.process.kill()
                self.recorder.process.wait(timeout=5)
            self.recorder.log.close()


def run_window(folder: Path) -> int:
    app = QApplication([])
    window = CaptureWindow(folder)
    window.show()
    try:
        return app.exec()
    finally:
        window.cleanup()
