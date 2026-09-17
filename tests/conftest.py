from __future__ import annotations

import sys
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def desktop_environment():
    """Windows Explorer launch, without inherited Conda dependency search paths."""
    environment = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("CONDA", "_CE_", "QT_"))}
    if os.name == "nt":
        windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        environment["PATH"] = os.pathsep.join(map(str, (windows / "System32", windows)))
    environment["QT_QPA_PLATFORM"] = "offscreen"
    return environment


@pytest.fixture(scope="session")
def marked_media(tmp_path_factory):
    """Three-second video and two-second external audio with known audible markers."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg/ffprobe are required for media integration checks")
    folder = tmp_path_factory.mktemp("marked_media")
    video = folder / "reference.mp4"
    silent_video = folder / "silent.mp4"
    audio = folder / "markers.wav"
    sr = 48000
    signal = np.zeros(sr * 2)
    for start in (0.6, 1.25):
        t = np.arange(round(sr * 0.18)) / sr
        signal[round(start * sr):round(start * sr) + len(t)] = 0.4 * np.sin(2 * np.pi * 880 * t)
    sf.write(audio, signal, sr, subtype="PCM_16")
    commands = [
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x90:rate=25:duration=3", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=3", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
        ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", "0:v:0",
         "-c:v", "copy", "-an", str(silent_video)],
    ]
    for command in commands:
        subprocess.run(command, capture_output=True, check=True)
    return video, silent_video, audio
