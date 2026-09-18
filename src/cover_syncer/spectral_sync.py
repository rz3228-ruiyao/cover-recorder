"""Spectral offset candidates with conservative cross-checks for auto export.

Reliability labels are engineering rules, not calibrated probabilities. The
legacy RMS estimators remain in sync.py for comparison and reproducibility.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from scipy import signal

from .sync import SyncError, SyncResult, make_envelope_feature, to_mono_float


ANALYSIS_RATE = 16000
WINDOW = 1024
BANDS = 48
AGREEMENT_MS = 40.0


@dataclass(frozen=True)
class Candidate:
    lag: int
    score: float
    runner_up: float
    ratio: float

    @property
    def supported(self) -> bool:
        return self.score >= .35 and self.ratio >= 1.25


def _resample(audio, sample_rate):
    mono = to_mono_float(audio)
    if mono.size == 0 or np.max(np.abs(mono)) <= 1e-7:
        raise SyncError("Audio is empty, silent or too quiet for automatic alignment.")
    if sample_rate == ANALYSIS_RATE:
        return mono
    divisor = math.gcd(sample_rate, ANALYSIS_RATE)
    return signal.resample_poly(mono, ANALYSIS_RATE//divisor, sample_rate//divisor)


def make_spectral_features(audio: np.ndarray, hop: int = 160) -> np.ndarray:
    """16 kHz mono -> standardized log-band power, without STFT phase matching."""
    if len(audio) < WINDOW:
        raise SyncError("Audio is too short for spectral analysis.")
    frequencies = np.fft.rfftfreq(WINDOW, 1/ANALYSIS_RATE)
    edges = np.geomspace(80, 6500, BANDS+2)
    filters = np.maximum(0, np.minimum(
        (frequencies[None, :] - edges[:-2, None]) / (edges[1:-1]-edges[:-2])[:, None],
        (edges[2:, None] - frequencies[None, :]) / (edges[2:]-edges[1:-1])[:, None]))
    filters /= np.maximum(filters.sum(axis=1, keepdims=True), 1e-12)
    frame_count = 1+(len(audio)-WINDOW)//hop
    energy = np.empty((BANDS, frame_count))
    # Bound the temporary complex spectrogram for multi-minute recordings.
    for first in range(0, frame_count, 2048):
        count = min(2048, frame_count-first)
        chunk = audio[first*hop:(first+count-1)*hop+WINDOW]
        _, _, spectrum = signal.stft(chunk, fs=ANALYSIS_RATE, window="hann", nperseg=WINDOW,
                                    noverlap=WINDOW-hop, boundary=None, padded=False)
        energy[:, first:first+count] = filters @ (np.abs(spectrum)**2)
    scale = max(float(np.quantile(energy, .95)), 1e-12)
    features = np.log1p(energy / (scale*.01))
    features -= features.mean(axis=1, keepdims=True)
    std = features.std(axis=1, keepdims=True)
    if np.max(std) < 1e-7:
        raise SyncError("Audio does not contain enough spectral variation for alignment.")
    return features / np.maximum(std, max(float(np.max(std))*.15, 1e-8))


def match_features(x: np.ndarray, y: np.ndarray, rate: float, *,
                   min_seconds: float = 5., min_fraction: float = .5) -> Candidate:
    nx, ny = x.shape[1], y.shape[1]
    lags = signal.correlation_lags(nx, ny)
    corr = sum(signal.correlate(a, b, method="fft") for a, b in zip(x, y))
    start_x, end_x = np.maximum(0, lags), np.minimum(nx, ny+lags)
    start_y, end_y = start_x-lags, end_x-lags
    px = np.r_[0, np.cumsum(np.sum(x*x, axis=0))]
    py = np.r_[0, np.cumsum(np.sum(y*y, axis=0))]
    norm = np.sqrt(np.maximum((px[end_x]-px[start_x])*(py[end_y]-py[start_y]), 0))
    overlap = end_x-start_x
    valid = (overlap >= max(math.ceil(min_seconds*rate), math.ceil(min_fraction*min(nx, ny)))) & (norm > 1e-9)
    if not np.any(valid):
        raise SyncError("可用重叠音频太短或缺少有效信号；请手动设置偏移。")
    scores = np.full(len(lags), -np.inf)
    scores[valid] = np.clip(corr[valid]/norm[valid], -1, 1)
    best = int(np.argmax(scores))
    rivals = valid & (np.abs(lags-lags[best]) > max(1, round(.1*rate)))
    second = max(0., float(np.max(scores[rivals]))) if rivals.any() else 0.
    score = max(0., float(scores[best]))
    return Candidate(int(lags[best]), score, second, score/max(second, 1e-9))


def check_segments(x, y, candidate: Candidate, rate):
    """Disjoint reference windows search the entire external track independently.

    The proposed lag chooses only which reference part has overlap, never the
    search interval. Thus this can detect alternative/repeated sections or drift.
    """
    start = max(0, candidate.lag)
    end = min(x.shape[1], y.shape[1]+candidate.lag)
    count = min(3, int((end-start)/(3*rate)))
    offsets = []
    supported = []
    if count < 2:
        return offsets, supported
    edges = np.linspace(start, end, count+1, dtype=int)
    for left, right in zip(edges[:-1], edges[1:]):
        try:
            result = match_features(x[:, left:right], y, rate, min_seconds=2.5, min_fraction=.9)
            offsets.append(float((result.lag+left)/rate*1000))
            supported.append(result.supported)
        except SyncError:
            # Failed verification must not disappear from the high-reliability gate.
            offsets.append(None)
            supported.append(False)
    return offsets, supported


def estimate_offset_hybrid(reference_audio, external_audio, sample_rate: int, *,
                           feature_rate_hz: float = 100.) -> SyncResult:
    if not isinstance(sample_rate, (int, np.integer)) or sample_rate <= 0:
        raise SyncError("Sample rate must be a positive integer.")
    if not math.isfinite(feature_rate_hz) or not 25 <= feature_rate_hz <= 200:
        raise SyncError("Feature rate must be between 25 and 200 Hz.")
    hop = round(ANALYSIS_RATE/feature_rate_hz)
    rate = ANALYSIS_RATE/hop
    reference = _resample(reference_audio, int(sample_rate))
    external = _resample(external_audio, int(sample_rate))
    candidates = {}
    features = {}
    failures = []
    for name in ("spectral", "envelope"):
        try:
            if name == "spectral":
                x, y = make_spectral_features(reference, hop), make_spectral_features(external, hop)
            else:
                x = make_envelope_feature(reference, ANALYSIS_RATE, rate)[None, :]
                y = make_envelope_feature(external, ANALYSIS_RATE, rate)[None, :]
            candidates[name] = match_features(x, y, rate)
            features[name] = (x, y)
        except SyncError as exc:
            failures.append(str(exc))
    if not candidates:
        raise SyncError("无法得到可靠的时间候选；请手动设置偏移。 " + " ".join(failures))

    spectral, envelope = candidates.get("spectral"), candidates.get("envelope")
    if spectral is not None and spectral.supported:
        chosen_name = "spectral"
    elif envelope is not None and envelope.supported:
        chosen_name = "envelope"
    else:
        # Even an uncertain spectral candidate can be useful for manual review.
        chosen_name = "spectral" if spectral is not None and spectral.score >= .25 else next(
            (name for name in ("envelope", "spectral") if name in candidates))
    chosen = candidates[chosen_name]
    offset = chosen.lag/rate*1000
    offsets, supported = check_segments(*features[chosen_name], chosen, rate)
    available = [value for value in offsets if value is not None]
    differences = [abs(value-offset) for value in available]
    consistency = float(np.median(differences)) if differences else None
    consistent = sum(ok and value is not None and abs(value-offset) <= AGREEMENT_MS
                     for value, ok in zip(offsets, supported))
    verified = len(offsets) >= 2 and consistent == len(offsets)
    conflict = bool(spectral is not None and envelope is not None and
                    spectral.supported and envelope.supported and
                    abs(spectral.lag-envelope.lag)/rate*1000 > AGREEMENT_MS)
    reliability = "medium" if chosen.supported else "low"
    reason = "候选偏移需要试听确认。"
    if not chosen.supported:
        reason = "匹配分数不足或存在多个相似位置，请手动确认偏移。"
    elif conflict:
        reliability = "low"
        reason = "声音强弱与频谱给出不同位置，请手动确认偏移。"
    elif offsets and not verified:
        reliability = "low"
        reason = "部分片段匹配不够明确或偏移不一致，请手动确认偏移。"
    elif chosen_name == "spectral" and verified and chosen.score >= .60 and chosen.ratio >= 1.50:
        reliability = "high"
        reason = "频谱匹配清晰，多个独立片段确认了同一偏移。"
    elif not offsets:
        reason = "有效重叠不足以分段复核，请试听或手动确认偏移。"
    else:
        reason = "已得到候选偏移，但证据不足以直接自动导出，请试听确认。"

    agreement = bool(spectral is not None and envelope is not None and spectral.supported
                     and envelope.supported and abs(spectral.lag-envelope.lag)/rate*1000 <= AGREEMENT_MS)
    return SyncResult(offset_ms=offset, confidence=chosen.score, feature_rate_hz=rate,
                      lag_frames=chosen.lag, peak_ratio=chosen.ratio, second_confidence=chosen.runner_up,
                      segment_count=len(offsets), consistent_segment_count=consistent,
                      segment_consistency_ms=consistency, segment_offsets_ms=tuple(available),
                      reliability=reliability, method="spectral+envelope" if agreement else chosen_name,
                      decision_reason=reason,
                      envelope_offset_ms=envelope.lag/rate*1000 if envelope else None,
                      spectral_offset_ms=spectral.lag/rate*1000 if spectral else None)
