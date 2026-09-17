from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest

from cover_syncer.media import MediaError, export_synced_video, probe_streams


@pytest.mark.parametrize("extension,codec", [
    ("mp4", "libx264"), ("mov", "libx264"), ("mov", "libx265"),
    ("m4v", "libx264"), ("mkv", "ffv1"), ("avi", "mpeg4"),
    ("wmv", "wmv2"), ("webm", "libaom-av1"), ("mpg", "mpeg2video"),
    ("mpeg", "mpeg2video"), ("mts", "libx264"), ("m2ts", "libx264"),
    ("ts", "libx264"), ("flv", "flv"), ("3gp", "mpeg4"),
])
def test_mainstream_video_formats_export_compatible_mp4(tmp_path: Path, marked_media, extension: str, codec: str) -> None:
    source = tmp_path / f"input.{extension}"
    args = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x96:rate=25:duration=1",
            "-c:v", codec, "-pix_fmt", "yuv420p"]
    if codec == "libx265":
        args += ["-x265-params", "log-level=error", "-tag:v", "hvc1"]
    if codec == "libaom-av1":
        args += ["-cpu-used", "8", "-threads", "2"]
    if extension == "m4v":
        args += ["-f", "mp4"]  # .m4v here is the common MP4 variant, not raw MPEG-4 video.
    subprocess.run([*args, str(source)], capture_output=True, check=True, timeout=30)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "result.mp4"
    export_synced_video(source, marked_media[2], output, 125)
    streams = probe_streams(output)
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264" and video["pix_fmt"] == "yuv420p"
    assert audio["codec_name"] == "aac"
    assert int(video["nb_frames"]) == 25
    assert abs(float(video["duration"]) - 1) < .05
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(output), "-f", "null", "-"],
                   capture_output=True, check=True, timeout=15)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


def test_hdr_is_rejected_explicitly_before_creating_output(tmp_path: Path, marked_media) -> None:
    source = tmp_path / "hdr.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(marked_media[1]), "-c:v", "libx264",
                    "-vf", "setparams=color_trc=smpte2084:color_primaries=bt2020:colorspace=bt2020nc",
                    str(source)], capture_output=True, check=True, timeout=15)
    output = tmp_path / "result.mp4"
    with pytest.raises(MediaError, match="HDR"):
        export_synced_video(source, marked_media[2], output, 0)
    assert not output.exists()
