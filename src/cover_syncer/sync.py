from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import soundfile as sf
from scipy.signal import correlate, correlation_lags


class SyncError(RuntimeError):
    """Raised when audio cannot be analyzed for synchronization."""


@dataclass(frozen=True)
class SyncResult:
    offset_ms: float
    confidence: float
    feature_rate_hz: float
    lag_frames: int
    peak_ratio: float = 1.0
    second_confidence: float = 0.0
    segment_count: int = 0
    consistent_segment_count: int = 0
    segment_consistency_ms: float | None = None
    segment_offsets_ms: tuple[float, ...] = ()
    reliability: str = "single"
    method: str = "envelope"
    decision_reason: str = ""
    envelope_offset_ms: float | None = None
    spectral_offset_ms: float | None = None


def estimate_offset_from_files(
    reference_wav: str,
    external_wav: str,
    *,
    feature_rate_hz: float = 100.0,
    robust: bool = False,
) -> SyncResult:
    reference, reference_rate = sf.read(reference_wav, always_2d=False)
    external, external_rate = sf.read(external_wav, always_2d=False)
    if reference_rate != external_rate:
        raise SyncError(
            f"Expected matching sample rates after conversion, got "
            f"{reference_rate} Hz and {external_rate} Hz."
        )
    if robust:
        # Keep the original RMS APIs available as a reproducible baseline.
        from .spectral_sync import estimate_offset_hybrid
        return estimate_offset_hybrid(
            reference,
            external,
            reference_rate,
            feature_rate_hz=feature_rate_hz,
        )
    return estimate_offset(reference, external, reference_rate, feature_rate_hz=feature_rate_hz)


def estimate_offset(
    reference_audio: np.ndarray,
    external_audio: np.ndarray,
    sample_rate: int,
    *,
    feature_rate_hz: float = 100.0,
) -> SyncResult:
    """Estimate how much the external audio should move to align with reference.

    Positive offset means delay the external audio. Negative offset means trim the
    beginning of the external audio.
    """

    reference_feature = make_envelope_feature(reference_audio, sample_rate, feature_rate_hz)
    external_feature = make_envelope_feature(external_audio, sample_rate, feature_rate_hz)

    if len(reference_feature) < 3 or len(external_feature) < 3:
        raise SyncError("Audio is too short for automatic alignment.")

    correlation = correlate(reference_feature, external_feature, mode="full", method="fft")
    lags = correlation_lags(len(reference_feature), len(external_feature), mode="full")

    norm = float(np.linalg.norm(reference_feature) * np.linalg.norm(external_feature))
    if norm <= 1e-12:
        raise SyncError("Audio does not contain enough usable signal for alignment.")

    peak = find_peak(correlation, lags, norm, feature_rate_hz)
    lag_frames = peak["lag_frames"]
    confidence = peak["confidence"]
    offset_ms = lag_frames / feature_rate_hz * 1000.0
    return SyncResult(
        offset_ms=offset_ms,
        confidence=confidence,
        feature_rate_hz=feature_rate_hz,
        lag_frames=lag_frames,
        peak_ratio=peak["peak_ratio"],
        second_confidence=peak["second_confidence"],
    )


def estimate_offset_robust(
    reference_audio: np.ndarray,
    external_audio: np.ndarray,
    sample_rate: int,
    *,
    feature_rate_hz: float = 100.0,
    segment_duration_s: float = 40.0,
    segment_count: int = 5,
    consistency_tolerance_ms: float = 80.0,
) -> SyncResult:
    """Estimate offset and score whether multiple parts of the file agree."""

    full = estimate_offset(
        reference_audio,
        external_audio,
        sample_rate,
        feature_rate_hz=feature_rate_hz,
    )
    reference = to_mono_float(reference_audio)
    external = to_mono_float(external_audio)
    windows = make_segment_windows(
        min(len(reference), len(external)) / sample_rate,
        segment_duration_s,
        segment_count,
    )

    segment_offsets: list[float] = []
    for start_s, duration_s in windows:
        start = int(round(start_s * sample_rate))
        end = int(round((start_s + duration_s) * sample_rate))
        try:
            segment = estimate_offset(
                reference[start:end],
                external[start:end],
                sample_rate,
                feature_rate_hz=feature_rate_hz,
            )
        except SyncError:
            continue
        segment_offsets.append(segment.offset_ms)

    if not segment_offsets:
        reliability = score_reliability(
            full.confidence,
            full.peak_ratio,
            None,
            0,
            0,
        )
        return SyncResult(
            offset_ms=full.offset_ms,
            confidence=full.confidence,
            feature_rate_hz=full.feature_rate_hz,
            lag_frames=full.lag_frames,
            peak_ratio=full.peak_ratio,
            second_confidence=full.second_confidence,
            reliability=reliability,
        )

    differences = [abs(offset - full.offset_ms) for offset in segment_offsets]
    consistency_ms = float(np.median(differences))
    consistent_count = sum(diff <= consistency_tolerance_ms for diff in differences)
    reliability = score_reliability(
        full.confidence,
        full.peak_ratio,
        consistency_ms,
        consistent_count,
        len(segment_offsets),
    )
    return SyncResult(
        offset_ms=full.offset_ms,
        confidence=full.confidence,
        feature_rate_hz=full.feature_rate_hz,
        lag_frames=full.lag_frames,
        peak_ratio=full.peak_ratio,
        second_confidence=full.second_confidence,
        segment_count=len(segment_offsets),
        consistent_segment_count=consistent_count,
        segment_consistency_ms=consistency_ms,
        segment_offsets_ms=tuple(float(offset) for offset in segment_offsets),
        reliability=reliability,
    )


def find_peak(
    correlation: np.ndarray,
    lags: np.ndarray,
    norm: float,
    feature_rate_hz: float,
) -> dict[str, float | int]:
    peak_index = int(np.argmax(correlation))
    peak_lag = int(lags[peak_index])
    confidence = _clamp_confidence(float(correlation[peak_index] / norm))

    exclusion_radius = max(1, int(round(feature_rate_hz * 1.0)))
    far_enough = np.abs(lags - peak_lag) > exclusion_radius
    if np.any(far_enough):
        candidate_scores = correlation[far_enough] / norm
        second_confidence = _clamp_confidence(float(np.max(candidate_scores)))
    else:
        second_confidence = 0.0

    if second_confidence <= 1e-9:
        peak_ratio = float("inf") if confidence > 0 else 1.0
    else:
        peak_ratio = confidence / second_confidence

    return {
        "lag_frames": peak_lag,
        "confidence": confidence,
        "second_confidence": second_confidence,
        "peak_ratio": peak_ratio,
    }


def make_segment_windows(
    duration_s: float,
    segment_duration_s: float,
    segment_count: int,
    *,
    min_segment_s: float = 10.0,
) -> list[tuple[float, float]]:
    if duration_s < min_segment_s:
        return []

    duration = min(segment_duration_s, duration_s * 0.8)
    if duration < min_segment_s:
        return []

    if duration_s <= duration + 1.0:
        return [(0.0, duration)]

    count = max(1, min(segment_count, int(duration_s // min_segment_s)))
    starts = np.linspace(0.0, duration_s - duration, count)
    return [(float(start), float(duration)) for start in starts]


def score_reliability(
    confidence: float,
    peak_ratio: float,
    consistency_ms: float | None,
    consistent_count: int,
    segment_count: int,
) -> str:
    enough_consistency = (
        segment_count == 0
        or (
            consistent_count >= max(1, int(np.ceil(segment_count * 0.6)))
            and (consistency_ms is None or consistency_ms <= 50.0)
        )
    )
    if enough_consistency and (
        (confidence >= 0.40 and peak_ratio >= 4.0)
        or (confidence >= 0.70 and peak_ratio >= 3.0)
    ):
        return "high"
    if confidence >= 0.30 and peak_ratio >= 2.0 and enough_consistency:
        return "medium"
    return "low"


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, value))


def make_envelope_feature(
    audio: np.ndarray,
    sample_rate: int,
    feature_rate_hz: float = 100.0,
) -> np.ndarray:
    mono = to_mono_float(audio)
    if mono.size == 0:
        raise SyncError("Audio file is empty.")

    peak = float(np.max(np.abs(mono)))
    if peak <= 1e-7:
        raise SyncError("Audio is silent or too quiet for automatic alignment.")
    mono = mono / peak

    hop = max(1, int(round(sample_rate / feature_rate_hz)))
    frame = max(hop, int(round(sample_rate * 0.04)))
    if mono.size < frame:
        raise SyncError("Audio is too short for automatic alignment.")

    squared = np.square(mono, dtype=np.float64)
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    starts = np.arange(0, mono.size - frame + 1, hop)
    energies = (cumulative[starts + frame] - cumulative[starts]) / frame
    envelope = np.sqrt(np.maximum(energies, 0.0))

    envelope = np.log1p(envelope * 10.0)
    smooth_window = max(3, int(round(feature_rate_hz * 1.5)))
    if smooth_window % 2 == 0:
        smooth_window += 1
    if envelope.size > smooth_window:
        kernel = np.ones(smooth_window, dtype=np.float64) / smooth_window
        envelope = envelope - np.convolve(envelope, kernel, mode="same")
    else:
        envelope = envelope - float(np.mean(envelope))

    std = float(np.std(envelope))
    if std <= 1e-8:
        raise SyncError("Audio does not contain enough timing variation for alignment.")
    return ((envelope - float(np.mean(envelope))) / std).astype(np.float64, copy=False)


def to_mono_float(audio: np.ndarray) -> np.ndarray:
    data = np.asarray(audio)
    if data.ndim == 2:
        data = np.mean(data, axis=1)
    elif data.ndim != 1:
        raise SyncError(f"Unsupported audio shape: {data.shape}")
    data = np.nan_to_num(data.astype(np.float64, copy=False), copy=False)
    return data
