from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cover_syncer.media import build_audio_filter, export_synced_video, probe_streams


def test_stalled_media_command_reports_timeout(monkeypatch):
    from cover_syncer.media import MediaError, run_command

    def stalled(args, **kwargs):
        assert kwargs["timeout"] == 60
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", stalled)
    with pytest.raises(MediaError, match="60 秒"):
        run_command(["ffprobe.exe", "input.mkv"])


def test_build_audio_filter_for_positive_negative_and_zero_offsets() -> None:
    assert build_audio_filter(125.4) == "[1:a:0]asetpts=PTS-STARTPTS,adelay=125:all=1,apad[aligned_audio]"
    assert (
        build_audio_filter(-250)
        == "[1:a:0]asetpts=PTS-STARTPTS,atrim=start=0.250000,asetpts=PTS-STARTPTS,apad[aligned_audio]"
    )
    assert build_audio_filter(0) == "[1:a:0]asetpts=PTS-STARTPTS,apad[aligned_audio]"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is not available")
def test_export_synced_video_replaces_original_audio(tmp_path: Path) -> None:
    video = tmp_path / "input.mp4"
    audio = tmp_path / "external.wav"
    output = tmp_path / "output.mp4"

    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=24:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ]
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=2",
            "-c:a",
            "pcm_s16le",
            str(audio),
        ]
    )

    export_synced_video(video, audio, output, 250)

    streams = probe_streams(output)
    assert sum(stream.get("codec_type") == "video" for stream in streams) == 1
    assert sum(stream.get("codec_type") == "audio" for stream in streams) == 1


def _run(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
