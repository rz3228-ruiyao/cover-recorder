from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory


VIDEO_EXTENSIONS = ("mp4", "mov", "m4v", "mkv", "avi", "wmv", "webm", "mpg", "mpeg", "mts", "m2ts", "ts", "flv", "3gp")
VIDEO_FILE_FILTER = "Video Files (" + " ".join(f"*.{ext}" for ext in VIDEO_EXTENSIONS) + ");;All Files (*.*)"


class MediaError(RuntimeError):
    """Raised when FFmpeg or media probing fails."""


@dataclass(frozen=True)
class ExportResult:
    output_path: Path
    ffmpeg_stderr: str


def ffmpeg_executable() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    raise MediaError("FFmpeg was not found. Run inside the cover-syncer conda environment.")


def ffprobe_executable() -> str:
    executable = shutil.which("ffprobe")
    if executable:
        return executable
    raise MediaError("ffprobe was not found. Run inside the cover-syncer conda environment.")


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    timeout = 60 if Path(args[0]).stem.lower() == "ffprobe" else 1200
    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                                timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{Path(args[0]).stem} 超过 {timeout} 秒未完成，已停止；原始素材保留，请重试。") from exc
    except OSError as exc:
        raise MediaError(str(exc)) from exc
    if result.returncode != 0:
        raise MediaError(_summarize_stderr(result.stderr))
    return result


def probe_streams(path: str | Path) -> list[dict]:
    args = [
        ffprobe_executable(),
        "-v",
        "error",
        "-show_streams",
        "-of",
        "json",
        str(path),
    ]
    result = run_command(args)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe returned invalid JSON.") from exc
    return list(payload.get("streams", []))


def has_audio_stream(path: str | Path) -> bool:
    return any(stream.get("codec_type") == "audio" for stream in probe_streams(path))


def extract_audio_to_wav(input_path: str | Path, output_wav: str | Path) -> Path:
    output = Path(output_wav)
    args = [
        ffmpeg_executable(),
        "-y",
        "-hide_banner",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
        "-sample_fmt",
        "s16",
        str(output),
    ]
    run_command(args)
    return output


def build_audio_filter(offset_ms: float) -> str:
    if not math.isfinite(offset_ms):
        raise MediaError("偏移必须是有限数值。")
    if offset_ms > 0.5:
        delay = int(round(offset_ms))
        return f"[1:a:0]asetpts=PTS-STARTPTS,adelay={delay}:all=1,apad[aligned_audio]"
    if offset_ms < -0.5:
        start = abs(offset_ms) / 1000.0
        return f"[1:a:0]asetpts=PTS-STARTPTS,atrim=start={start:.6f},asetpts=PTS-STARTPTS,apad[aligned_audio]"
    return "[1:a:0]asetpts=PTS-STARTPTS,apad[aligned_audio]"


def validate_input_paths(video_path: str | Path, audio_path: str | Path) -> None:
    for label, path in (("视频", video_path), ("外部音频", audio_path)):
        if not str(path).strip() or not Path(path).is_file():
            raise MediaError(f"{label}文件不存在或不是文件：{path}")


def validate_export_paths(
    video_path: str | Path, audio_path: str | Path, output_path: str | Path,
    *, overwrite: bool = False,
) -> Path:
    validate_input_paths(video_path, audio_path)
    if not str(output_path).strip():
        raise MediaError("请选择导出位置。")
    output = Path(output_path)
    for source in (Path(video_path), Path(audio_path)):
        if output.resolve() == source.resolve() or (output.exists() and output.samefile(source)):
            raise MediaError("导出位置不能与视频或外部音频相同，请另选文件名。")
    if output.is_symlink():
        raise MediaError("导出位置不能是符号链接，请另选文件名。")
    if output.exists() and not output.is_file():
        raise MediaError("导出位置不是普通文件。")
    if output.suffix.lower() != ".mp4":
        raise MediaError("导出文件必须使用 .mp4 扩展名。")
    if output.exists() and not overwrite:
        raise MediaError("导出文件已存在，请确认覆盖或另选文件名。")
    return output


def video_duration(video_path: str | Path) -> float:
    result = run_command([
        ffprobe_executable(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=duration:format=duration,format_name", "-of", "json", str(video_path),
    ])
    try:
        payload = json.loads(result.stdout)
        streams = payload.get("streams", [])
        if not streams:
            raise MediaError("选择的文件没有视频画面。")
        if payload.get("format", {}).get("format_name") == "mpeg":
            # MPEG-PS can omit the last frame's timestamp and underreport duration.
            # Decode frame timing, extrapolating absent timestamps in display order.
            frames = json.loads(run_command([
                ffprobe_executable(), "-v", "error", "-select_streams", "v:0", "-show_frames",
                "-show_entries", "frame=best_effort_timestamp_time,duration_time", "-of", "json", str(video_path),
            ]).stdout).get("frames", [])
            timed = []
            next_start = 0.0
            for frame in frames:
                timestamp = frame.get("best_effort_timestamp_time")
                start = float(timestamp) if timestamp not in (None, "N/A") else next_start
                length = float(frame.get("duration_time", 0))
                timed.append((start, length))
                next_start = start + length
            if timed:
                span = max(start + length for start, length in timed) - min(start for start, _ in timed)
                if math.isfinite(span) and span > 0:
                    return span
        for value in (streams[0].get("duration"), payload.get("format", {}).get("duration")):
            try:
                duration = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(duration) and duration > 0:
                return duration
    except json.JSONDecodeError as exc:
        raise MediaError("无法读取视频时长。") from exc
    raise MediaError("无法确定有效的视频时长，不能导出。")


def export_synced_video(
    video_path: str | Path,
    external_audio_path: str | Path,
    output_path: str | Path,
    offset_ms: float,
    *,
    overwrite: bool = False,
) -> ExportResult:
    output = validate_export_paths(video_path, external_audio_path, output_path, overwrite=overwrite)
    audio_filter = build_audio_filter(offset_ms)
    duration = video_duration(video_path)
    video_stream = next(stream for stream in probe_streams(video_path) if stream.get("codec_type") == "video")
    # MP4 is a container: WebM/AVI/WMV/MOV inputs may carry codecs that common
    # MP4 players cannot decode. Preserve compatible H.264, otherwise encode SDR H.264.
    if video_stream.get("color_transfer") in ("smpte2084", "arib-std-b67"):
        raise MediaError("当前导出支持 SDR 视频；HDR 视频请先转换为 SDR，以免导出后亮度或颜色异常。")
    if video_stream.get("codec_name") == "h264" and video_stream.get("pix_fmt") in ("yuv420p", "yuvj420p"):
        video_args = ["-c:v", "copy"]
    else:
        video_args = ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p",
                      "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
    if not has_audio_stream(external_audio_path):
        raise MediaError("选择的外部音频文件没有音轨。")
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [
        ffmpeg_executable(),
        "-y",
        "-hide_banner",
        "-i",
        str(video_path),
        "-i",
        str(external_audio_path),
        "-filter_complex",
        audio_filter,
        "-map",
        "0:v:0",
        "-map",
        "[aligned_audio]",
        *video_args,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-t",
        f"{duration:.6f}",
    ]
    # Encode beside the destination so a failed FFmpeg run cannot damage an existing file.
    with TemporaryDirectory(prefix=".cover-export-", dir=output.parent) as temp_dir:
        temporary = Path(temp_dir) / "render.mp4"
        result = run_command([*args, str(temporary)])
        streams = probe_streams(temporary)
        if not temporary.is_file() or temporary.stat().st_size == 0 or not all(
            any(stream.get("codec_type") == kind for stream in streams) for kind in ("video", "audio")
        ):
            raise MediaError("导出未生成完整的音视频文件。")
        validate_export_paths(video_path, external_audio_path, output, overwrite=overwrite)
        if overwrite:
            os.replace(temporary, output)
        else:
            # Atomic create-if-absent: a file created during rendering must not be overwritten.
            try:
                if os.name == "nt":
                    os.rename(temporary, output)  # Windows rename refuses an existing destination.
                else:
                    os.link(temporary, output)
            except FileExistsError as exc:
                raise MediaError("导出位置已出现同名文件，请另选文件名。") from exc
    return ExportResult(output_path=output, ffmpeg_stderr=result.stderr)


def _summarize_stderr(stderr: str) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "External command failed without an error message."
    return "\n".join(lines[-12:])
