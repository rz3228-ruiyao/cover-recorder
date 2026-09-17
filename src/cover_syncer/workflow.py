from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from .media import MediaError, extract_audio_to_wav, has_audio_stream
from .sync import SyncResult, estimate_offset_from_files


def analyze_media(video_path: str | Path, external_audio_path: str | Path) -> SyncResult:
    video = Path(video_path)
    audio = Path(external_audio_path)
    if not video.exists():
        raise MediaError(f"Video file does not exist: {video}")
    if not audio.exists():
        raise MediaError(f"Audio file does not exist: {audio}")
    if not has_audio_stream(video):
        raise MediaError("视频没有参考音轨，无法自动分析；请手动设置偏移后导出。")

    with TemporaryDirectory(prefix="cover_syncer_") as temp_dir:
        temp = Path(temp_dir)
        reference_wav = temp / "video_reference.wav"
        external_wav = temp / "external_audio.wav"
        extract_audio_to_wav(video, reference_wav)
        extract_audio_to_wav(audio, external_wav)
        return estimate_offset_from_files(str(reference_wav), str(external_wav), robust=True)
