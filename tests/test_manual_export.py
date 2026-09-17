from __future__ import annotations

import hashlib
import os
import subprocess

import numpy as np
import pytest
import soundfile as sf

from cover_syncer import media


def decode_audio(path):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
         "-ar", "48000", "-f", "f32le", "pipe:1"], capture_output=True, check=True,
    )
    return np.frombuffer(result.stdout, dtype="<f4")


def frame_checksums(path):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if not line.startswith("#")]


@pytest.mark.parametrize("offset", [-3000, -800, -300, 0, 300, 4000])
@pytest.mark.parametrize("reference_audio", [False, True])
def test_export_known_audio_positions_and_unchanged_video(marked_media, tmp_path, offset, reference_audio):
    video, silent_video, audio = marked_media
    source = video if reference_audio else silent_video
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, audio)]
    output = tmp_path / "成品.mp4"
    media.export_synced_video(source, audio, output, offset)
    assert frame_checksums(source) == frame_checksums(output)
    streams = media.probe_streams(output)
    assert abs(float(next(s["duration"] for s in streams if s["codec_type"] == "video")) - 3) < 0.04
    samples = decode_audio(output)
    # AAC may add at most one 1024-sample block; timing tolerance is fixed at 25 ms.
    assert abs(len(samples) / 48000 - 3) <= 0.025
    width = 480  # 10 ms RMS bins
    rms = np.sqrt(np.mean(samples[:len(samples) // width * width].reshape(-1, width) ** 2, axis=1))
    audible = np.flatnonzero(rms > 0.08)
    splits = np.split(audible, np.flatnonzero(np.diff(audible) > 1) + 1) if len(audible) else []
    actual = [(group[0] / 100, (group[-1] + 1) / 100) for group in splits]
    expected = [(max(0, start + offset / 1000), min(3, start + 0.18 + offset / 1000))
                for start in (0.6, 1.25) if start + 0.18 + offset / 1000 > 0 and start + offset / 1000 < 3]
    assert len(actual) == len(expected)
    for observed, wanted in zip(actual, expected):
        assert np.max(np.abs(np.array(observed) - wanted)) <= 0.025
        center = sum(wanted) / 2
        segment = samples[round((center - 0.04) * 48000):round((center + 0.04) * 48000)]
        peak = np.fft.rfftfreq(len(segment), 1 / 48000)[np.argmax(abs(np.fft.rfft(segment)))]
        assert abs(peak - 880) < 15  # External marker, not the video's original 440 Hz tone.
    quiet = np.ones(len(samples), dtype=bool)
    for start, end in expected:
        quiet[max(0, round((start - 0.04) * 48000)):round((end + 0.04) * 48000)] = False
    assert np.max(abs(samples[quiet])) < 0.004
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (source, audio)]


def test_long_audio_is_cut_to_video(marked_media, tmp_path):
    video, _, _ = marked_media
    audio = tmp_path / "long.wav"
    sf.write(audio, np.sin(2 * np.pi * 880 * np.arange(48000 * 5) / 48000) * 0.2, 48000)
    output = tmp_path / "cut.mp4"
    media.export_synced_video(video, audio, output, 0)
    assert abs(len(decode_audio(output)) / 48000 - 3) <= 0.025
    assert frame_checksums(video) == frame_checksums(output)


def test_originals_and_hardlink_alias_are_protected(marked_media, tmp_path):
    video, _, audio = marked_media
    for source in (video, audio):
        original = source.read_bytes()
        with pytest.raises(media.MediaError, match="不能与"):
            media.export_synced_video(video, audio, source, 0, overwrite=True)
        assert source.read_bytes() == original
    alias = tmp_path / "alias.mp4"
    os.link(video, alias)
    with pytest.raises(media.MediaError, match="不能与"):
        media.export_synced_video(video, audio, alias, 0, overwrite=True)


def test_existing_output_requires_opt_in_and_failure_preserves_it(marked_media, tmp_path, monkeypatch):
    video, _, audio = marked_media
    output = tmp_path / "existing.mp4"
    output.write_bytes(b"previous export")
    with pytest.raises(media.MediaError, match="已存在"):
        media.export_synced_video(video, audio, output, 0)
    real_run = media.run_command

    def fail_render(args):
        if "-filter_complex" in args:
            from pathlib import Path
            Path(args[-1]).write_bytes(b"partial")
            raise media.MediaError("simulated encoding failure")
        return real_run(args)

    with monkeypatch.context() as patch:
        patch.setattr(media, "run_command", fail_render)
        with pytest.raises(media.MediaError, match="simulated"):
            media.export_synced_video(video, audio, output, 0, overwrite=True)
    assert output.read_bytes() == b"previous export"
    assert not list(tmp_path.glob(".cover-export-*"))
    media.export_synced_video(video, audio, output, 0, overwrite=True)
    assert media.has_audio_stream(output)


def test_file_created_during_export_is_not_overwritten(marked_media, tmp_path, monkeypatch):
    video, _, audio = marked_media
    output = tmp_path / "race.mp4"
    real_run = media.run_command

    def concurrent_file(args):
        result = real_run(args)
        if "-filter_complex" in args:
            output.write_bytes(b"another file")
        return result

    monkeypatch.setattr(media, "run_command", concurrent_file)
    with pytest.raises(media.MediaError, match="已存在"):
        media.export_synced_video(video, audio, output, 0)
    assert output.read_bytes() == b"another file"


def test_invalid_files_and_streams_fail_without_output(marked_media, tmp_path):
    video, silent_video, audio = marked_media
    output = tmp_path / "bad.mp4"
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not media")
    for source, external in [(tmp_path / "missing.mp4", audio), (tmp_path, audio),
                             (broken, audio), (audio, audio), (video, silent_video)]:
        with pytest.raises(media.MediaError):
            media.export_synced_video(source, external, output, 0)
        assert not output.exists()
    for destination in ("", tmp_path, tmp_path / "out.wav"):
        with pytest.raises(media.MediaError):
            media.validate_export_paths(video, audio, destination)
    for offset in (float("nan"), float("inf")):
        with pytest.raises(media.MediaError):
            media.export_synced_video(video, audio, output, offset)
