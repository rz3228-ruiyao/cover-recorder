"""Video + reference-microphone capture, using a single FFmpeg input.

The recorder is independent of Qt; the small companion window uses QtWidgets only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from .media import MediaError, ffmpeg_executable, probe_streams
from .runtime import configure_runtime


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
    os.replace(temporary, path)


def devices() -> dict[str, list[str]]:
    result = subprocess.run([ffmpeg_executable(), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                            capture_output=True, encoding="utf-8", errors="replace", timeout=15,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    found: dict[str, list[str]] = {"video": [], "audio": []}
    for line in result.stderr.splitlines():
        match = re.search(r'"(.*)" \((video|audio|none)\)', line)
        if match:
            name, kind = match.groups()
            kind = "video" if kind == "none" else kind
            if name not in found[kind]:
                found[kind].append(name)
    return found


def input_args(camera: str, microphone: str | None) -> list[str]:
    if not camera or (microphone is not None and not microphone):
        raise MediaError("请选择摄像头和独立的参考麦克风。")
    # Popen handles Windows argument quoting. DirectShow treats extra quotes as
    # part of the device name, so pass the raw selector as one argument.
    if ":" in camera or (microphone is not None and ":" in microphone):
        raise MediaError("设备名称包含 DirectShow 分隔符冒号；请在 Windows 中重命名设备后重试。")
    selector = "video=" + camera
    if microphone is not None:
        selector += ":audio=" + microphone
    return ["-thread_queue_size", "512", "-rtbufsize", "256M", "-f", "dshow", "-i", selector]


class Recorder:
    def __init__(self, folder: Path, source_args: list[str], *, recording: bool):
        # Every run gets a fresh directory, so -n can protect all source/output files.
        folder.mkdir(parents=True, exist_ok=False)
        self.folder = folder
        self.preview = folder / "preview.jpg"
        self.partial = folder / "capture.partial.mkv"
        self.output = folder / "capture.mkv"
        self.recording = recording
        self.stopping = False
        self.started_at = time.monotonic()
        self.stop_deadline = 0.0
        self.log = (folder / "ffmpeg.log").open("wb")
        args = [ffmpeg_executable(), "-hide_banner", "-loglevel", "warning", "-n", *source_args]
        if recording:
            args += ["-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-preset", "veryfast",
                     "-tune", "zerolatency", "-crf", "20", "-pix_fmt", "yuv420p",
                     "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2",
                     "-c:a", "aac", "-b:a", "192k", "-ar", "48000", str(self.partial)]
        args += ["-map", "0:v:0", "-an", "-vf", "fps=8,scale=640:-2", "-c:v", "mjpeg", "-q:v", "5",
                 "-f", "image2", "-update", "1", "-atomic_writing", "1", str(self.preview)]
        try:
            self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log,
                                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            self.log.close()
            raise

    @property
    def ready(self) -> bool:
        return self.process.poll() is None and self.preview.is_file() and self.preview.stat().st_size > 0

    def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        self.stop_deadline = time.monotonic() + 15
        if self.process.poll() is None:
            try:
                self.process.stdin.write(b"q\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def poll(self) -> int | None:
        result = self.process.poll()
        if result is None and not self.stopping and self.ready and time.time() - self.preview.stat().st_mtime > 10:
            self.stop()
            raise MediaError("摄像头超过 10 秒没有新画面；已停止采集，请检查连接。")
        if result is None and self.stopping and time.monotonic() > self.stop_deadline:
            self.process.kill()
            self.process.wait(timeout=5)
            raise MediaError("采集停止超时；已保留未完成的视频，音频不会删除。")
        if result is None and not self.stopping and not self.ready and time.monotonic() - self.started_at > 20:
            self.stop()
            raise MediaError("摄像头未能在 20 秒内提供画面，请检查设备占用和连接。")
        return result

    def finish(self) -> Path | None:
        code = self.process.poll()
        if code is None:
            raise MediaError("视频还在写入，尚不能合成。")
        self.log.close()
        self.process.stdin.close()
        if code != 0:
            detail = (self.folder / "ffmpeg.log").read_text(encoding="utf-8", errors="replace")[-1500:]
            raise MediaError("采集失败，已有素材保留。\n" + detail)
        if not self.recording:
            return None
        streams = probe_streams(self.partial)
        if not all(any(s.get("codec_type") == kind for s in streams) for kind in ("video", "audio")):
            raise MediaError("采集文件缺少画面或参考音轨，不能自动合成。")
        # create-if-absent, preserving any partial recording on failure.
        if os.name == "nt":
            os.rename(self.partial, self.output)
        else:
            os.link(self.partial, self.output)
            self.partial.unlink()
        return self.output


def main() -> int:
    configure_runtime()
    if len(sys.argv) == 2 and sys.argv[1] == "--devices":
        print(json.dumps(devices(), ensure_ascii=True))
        return 0
    if len(sys.argv) != 2:
        return 2
    folder = Path(sys.argv[1]).resolve()
    from .capture_window import run_window
    return run_window(folder)


if __name__ == "__main__":
    raise SystemExit(main())
