from __future__ import annotations

import numpy as np

from cover_syncer.sync import estimate_offset, estimate_offset_robust


def test_estimates_positive_offset_when_external_is_early() -> None:
    sample_rate = 48_000
    reference = _note_pattern(sample_rate)
    external = _apply_alignment_offset(reference, 0.5, sample_rate)

    result = estimate_offset(reference, external, sample_rate, feature_rate_hz=100)

    assert abs(result.offset_ms - 500) <= 40
    assert result.confidence > 0.2
    assert result.peak_ratio > 2.0


def test_estimates_negative_offset_when_external_is_late() -> None:
    sample_rate = 48_000
    reference = _note_pattern(sample_rate)
    external = _apply_alignment_offset(reference, -0.7, sample_rate)

    result = estimate_offset(reference, external, sample_rate, feature_rate_hz=100)

    assert abs(result.offset_ms + 700) <= 40
    assert result.confidence > 0.2


def test_estimates_zero_offset_with_noise_and_gain_difference() -> None:
    sample_rate = 48_000
    rng = np.random.default_rng(10)
    reference = _note_pattern(sample_rate)
    external = reference * 0.35 + rng.normal(0, 0.003, reference.shape)

    result = estimate_offset(reference, external, sample_rate, feature_rate_hz=100)

    assert abs(result.offset_ms) <= 40
    assert result.confidence > 0.2


def test_unrelated_audio_has_low_confidence() -> None:
    sample_rate = 48_000
    reference = _note_pattern(sample_rate, seed=1)
    external = _different_note_pattern(sample_rate)

    result = estimate_offset(reference, external, sample_rate, feature_rate_hz=100)

    assert result.confidence < 0.45


def test_robust_estimate_handles_random_noise_prefix() -> None:
    sample_rate = 48_000
    rng = np.random.default_rng(88)
    reference = _long_note_pattern(sample_rate)
    prefix_ms = 1230
    prefix_len = int(round(prefix_ms / 1000 * sample_rate))
    prefix = rng.normal(0, 0.02, prefix_len)
    continuous_noise = rng.normal(0, 0.003, reference.shape)
    external = np.concatenate((prefix, reference + continuous_noise))

    result = estimate_offset_robust(
        reference,
        external,
        sample_rate,
        feature_rate_hz=100,
        segment_duration_s=12,
        segment_count=3,
    )

    assert abs(result.offset_ms + prefix_ms) <= 50
    assert result.peak_ratio > 3.0
    assert result.segment_count >= 2
    assert result.consistent_segment_count >= 2
    assert result.segment_consistency_ms is not None
    assert result.segment_consistency_ms <= 50
    assert result.reliability == "high"


def test_robust_estimate_marks_unrelated_audio_low_reliability() -> None:
    sample_rate = 48_000
    reference = _long_note_pattern(sample_rate, seed=5)
    external = _different_long_note_pattern(sample_rate)

    result = estimate_offset_robust(
        reference,
        external,
        sample_rate,
        feature_rate_hz=100,
        segment_duration_s=12,
        segment_count=3,
    )

    assert result.reliability == "low"


def _note_pattern(sample_rate: int, *, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    duration = 8.0
    audio = rng.normal(0, 0.001, int(sample_rate * duration))
    starts = [0.9, 1.65, 2.4, 3.7, 5.25, 6.4]
    freqs = [110, 146.83, 196, 246.94, 329.63, 392]
    burst_len = int(sample_rate * 0.18)
    t = np.arange(burst_len) / sample_rate
    envelope = np.exp(-t * 18.0)
    for start, freq in zip(starts, freqs):
        index = int(start * sample_rate)
        burst = 0.8 * np.sin(2 * np.pi * freq * t) * envelope
        audio[index : index + burst_len] += burst
    return audio.astype(np.float64)


def _long_note_pattern(sample_rate: int, *, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    duration = 36.0
    audio = rng.normal(0, 0.001, int(sample_rate * duration))
    starts = [1.0, 2.2, 3.1, 5.4, 8.0, 11.2, 15.5, 18.0, 21.3, 25.2, 28.0, 33.1]
    freqs = [110, 146.83, 196, 246.94, 329.63, 392, 130.81, 220, 293.66, 174.61, 261.63, 349.23]
    burst_len = int(sample_rate * 0.18)
    t = np.arange(burst_len) / sample_rate
    envelope = np.exp(-t * 18.0)
    for start, freq in zip(starts, freqs):
        index = int(start * sample_rate)
        burst = 0.8 * np.sin(2 * np.pi * freq * t) * envelope
        audio[index : index + burst_len] += burst
    return audio.astype(np.float64)


def _different_note_pattern(sample_rate: int) -> np.ndarray:
    rng = np.random.default_rng(33)
    duration = 8.0
    audio = rng.normal(0, 0.001, int(sample_rate * duration))
    starts = [0.35, 2.15, 2.95, 4.85, 7.1]
    freqs = [523.25, 261.63, 587.33, 349.23, 220]
    burst_len = int(sample_rate * 0.11)
    t = np.arange(burst_len) / sample_rate
    envelope = np.exp(-t * 30.0)
    for start, freq in zip(starts, freqs):
        index = int(start * sample_rate)
        burst = 0.6 * np.sin(2 * np.pi * freq * t) * envelope
        audio[index : index + burst_len] += burst
    return audio.astype(np.float64)


def _different_long_note_pattern(sample_rate: int) -> np.ndarray:
    rng = np.random.default_rng(44)
    duration = 36.0
    audio = rng.normal(0, 0.001, int(sample_rate * duration))
    starts = [0.4, 4.8, 7.4, 12.8, 14.2, 19.7, 24.6, 30.4, 34.9]
    freqs = [523.25, 261.63, 587.33, 349.23, 220, 659.25, 196, 440, 311.13]
    burst_len = int(sample_rate * 0.11)
    t = np.arange(burst_len) / sample_rate
    envelope = np.exp(-t * 30.0)
    for start, freq in zip(starts, freqs):
        index = int(start * sample_rate)
        burst = 0.6 * np.sin(2 * np.pi * freq * t) * envelope
        audio[index : index + burst_len] += burst
    return audio.astype(np.float64)


def _apply_alignment_offset(reference: np.ndarray, offset_s: float, sample_rate: int) -> np.ndarray:
    samples = int(round(offset_s * sample_rate))
    if samples > 0:
        return np.concatenate((reference[samples:], np.zeros(samples)))
    if samples < 0:
        late = abs(samples)
        return np.concatenate((np.zeros(late), reference[:-late]))
    return reference.copy()
