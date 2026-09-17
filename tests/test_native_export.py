from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf

from cover_syncer.media import probe_streams
from cover_syncer.native_export import export_job


def test_bridge_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="version"):
        export_job({"version": 2})


def test_bridge_reports_invalid_job_as_json(tmp_path: Path) -> None:
    job = tmp_path / "bad.json"
    job.write_text("{}", encoding="utf-8")
    process = subprocess.run([sys.executable, "-m", "cover_syncer.native_export", str(job)],
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 1
    assert json.loads(process.stdout)["success"] is False


def test_native_auto_alignment_failure_does_not_claim_export_success(tmp_path: Path, marked_media, desktop_environment) -> None:
    exe = os.environ.get("COVER_RECORDER_TEST_EXE")
    if not exe:
        pytest.skip("Set COVER_RECORDER_TEST_EXE for native process integration")
    output = tmp_path / "cover.mp4"
    job = tmp_path / "automatic.json"
    job.write_text(json.dumps(dict(version=1, video=str(marked_media[1]), audio=str(marked_media[2]),
                                   output=str(output), offset_ms=0, auto_align=True)), encoding="utf-8")
    process = subprocess.run([exe, "--check-video-job", str(job)], capture_output=True, timeout=30,
                             env=desktop_environment)
    assert process.returncode != 0
    assert "Manual alignment required" in (tmp_path / "native-video-result.txt").read_text(encoding="utf-8")
    assert not output.exists()


@pytest.mark.parametrize("offset", [-250, 0, 250])
def test_native_process_exports_manual_offset_and_preserves_files(tmp_path: Path, offset: int, desktop_environment) -> None:
    exe = os.environ.get("COVER_RECORDER_TEST_EXE")
    if not exe:
        pytest.skip("Set COVER_RECORDER_TEST_EXE to run the native process integration test")
    folder = tmp_path / "中文 路径"
    folder.mkdir()
    video, audio, output = (folder / name for name in ("视频.mp4", "录音.wav", "成品.mp4"))
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True, timeout=15)
    rate = 48000
    samples = np.zeros(rate * 2)
    samples[rate // 2:int(rate * .8)] = .2 * np.sin(2 * np.pi * 701 * np.arange(int(rate * .3)) / rate)
    sf.write(audio, samples, rate, subtype="PCM_24")
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (video, audio)]
    job = folder / "job.json"
    job.write_text(json.dumps(dict(version=1, video=str(video), audio=str(audio), output=str(output),
                                   offset_ms=offset), ensure_ascii=False), encoding="utf-8")

    def run_native() -> subprocess.CompletedProcess:
        return subprocess.run([exe, "--check-video-job", str(job)], capture_output=True, timeout=30,
                              env=desktop_environment)

    process = run_native()
    report = folder / "native-video-result.txt"
    assert process.returncode == 0, report.read_text(encoding="utf-8")
    assert report.read_text(encoding="utf-8") == "OK"
    streams = probe_streams(output)
    assert [s["codec_type"] for s in streams] == ["video", "audio"]
    assert streams[0]["nb_frames"] == "50"
    assert abs(float(streams[0]["duration"]) - 2) < .01
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(output), "-vn", "-ac", "1",
                              "-ar", str(rate), "-f", "f32le", "pipe:1"], capture_output=True,
                             check=True, timeout=15)
    signal = np.frombuffer(decoded.stdout, dtype="<f4")
    active = np.flatnonzero(abs(signal) > .05)
    assert abs(active[0] / rate - (.5 + offset / 1000)) < .02
    assert abs(active[-1] / rate - (.8 + offset / 1000)) < .02
    output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
    assert run_native().returncode == 1  # Native bridge never grants overwrite permission.
    assert hashlib.sha256(output.read_bytes()).hexdigest() == output_hash
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in (video, audio)] == hashes
