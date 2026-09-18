"""Offline feature comparison; not imported by the application.

Run in cover-syncer: python experiments/spectral_alignment.py --output tmp/spectral-alignment
Optional --real-pairs points to a local JSON list of video/audio/offset_ms/label.
All hypotheses, thresholds and seeds are fixed here before evaluating the suite.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cover_syncer.runtime import configure_runtime
configure_runtime()

import numpy as np
from scipy import signal
import soundfile as sf

from cover_syncer.media import extract_audio_to_wav
from cover_syncer.sync import estimate_offset_robust, make_envelope_feature, to_mono_float


SR = 16000
HOP = 160
FEATURE_HZ = SR / HOP
BANDS = 48
METHODS = ("rms", "logbands", "bandflux")
# These are experimental shared decision rules, NOT calibrated probabilities.
SCORE_MIN = .35
RATIO_MIN = 1.25
RIVAL_EXCLUSION_MS = 100
TOLERANCE_MS = 40


def spectral_features(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mono = to_mono_float(audio)
    if len(mono) < 1024 or np.max(np.abs(mono)) < 1e-7:
        raise ValueError("Insufficient signal")
    # 64 ms windows / 10 ms hops. Same framing for both recordings.
    frequencies, _, spectrum = signal.stft(mono, fs=SR, window="hann", nperseg=1024,
                                          noverlap=1024-HOP, boundary=None, padded=False)
    power = np.abs(spectrum) ** 2
    edges = np.geomspace(80, 6500, BANDS + 2)
    filters = np.maximum(0, np.minimum(
        (frequencies[None, :] - edges[:-2, None]) / (edges[1:-1] - edges[:-2])[:, None],
        (edges[2:, None] - frequencies[None, :]) / (edges[2:] - edges[1:-1])[:, None]))
    filters /= np.maximum(filters.sum(axis=1, keepdims=True), 1e-12)
    energy = filters @ power
    # A robust reference avoids one loud frame determining all log compression.
    scale = max(float(np.quantile(energy, .95)), 1e-12)
    logged = np.log1p(energy / (scale * .01))
    flux = np.maximum(np.diff(logged, axis=1, prepend=logged[:, :1]), 0)

    def normalize(features):
        centered = features - features.mean(axis=1, keepdims=True)
        std = centered.std(axis=1, keepdims=True)
        # Floor limits amplification of silent/near-constant bands.
        floor = max(float(np.max(std)) * .15, 1e-8)
        return centered / np.maximum(std, floor)

    return normalize(logged), normalize(flux)


def feature_bank(audio):
    logbands, bandflux = spectral_features(audio)
    return {"rms": make_envelope_feature(audio, SR, FEATURE_HZ)[None, :],
            "logbands": logbands, "bandflux": bandflux}


def match_features(x, y):
    """Shared matcher isolates feature quality from differences in search logic."""
    nx, ny = x.shape[1], y.shape[1]
    lags = signal.correlation_lags(nx, ny)
    corr = sum(signal.correlate(a, b, method="fft") for a, b in zip(x, y))
    # Normalize by energy in the actual overlap; reject tiny accidental matches.
    start_x = np.maximum(0, lags)
    end_x = np.minimum(nx, ny + lags)
    start_y, end_y = start_x - lags, end_x - lags
    px = np.r_[0, np.cumsum(np.sum(x*x, axis=0))]
    py = np.r_[0, np.cumsum(np.sum(y*y, axis=0))]
    norm = np.sqrt(np.maximum((px[end_x]-px[start_x]) * (py[end_y]-py[start_y]), 0))
    minimum_overlap = max(500, math.ceil(.5 * min(nx, ny)))
    valid = ((end_x-start_x) >= minimum_overlap) & (norm > 1e-9)
    if not np.any(valid):
        raise ValueError("Not enough overlapping signal (need at least 5 seconds)")
    scores = np.full(len(lags), -np.inf)
    scores[valid] = corr[valid] / norm[valid]
    best = int(np.argmax(scores))
    rivals = valid & (np.abs(lags-lags[best]) > RIVAL_EXCLUSION_MS / 10)
    runner_up = max(0., float(np.max(scores[rivals]))) if rivals.any() else 0.
    score = float(scores[best])
    ratio = score / max(runner_up, 1e-9)
    return dict(offset_ms=float(lags[best] * 1000 / FEATURE_HZ), score=score,
                rival_score=runner_up, peak_ratio=ratio,
                accepted=bool(score >= SCORE_MIN and ratio >= RATIO_MIN))


def instrument(seed, *, regular=True, fixed_note=False, duration=60):
    rng = np.random.default_rng(seed)
    output = np.zeros(int(duration * SR))
    starts = np.arange(.5, duration-.5, .5)
    if not regular:
        starts = np.cumsum(rng.uniform(.3, .85, 100))
        starts = starts[starts < duration-.5]
    midis = np.full(len(starts), 57) if fixed_note else rng.choice([45, 48, 50, 52, 55, 57, 60, 62, 64, 67], len(starts))
    length = int(.32 * SR)
    t = np.arange(length) / SR
    envelope = (1-np.exp(-t/.004)) * np.exp(-t/ .09)
    envelope[-320:] *= np.linspace(1, 0, 320)
    for start, midi in zip(starts, midis):
        frequency = 440 * 2 ** ((int(midi)-69)/12)
        note = sum(np.sin(2*np.pi*frequency*h*t) / h**1.2 for h in range(1, 7)) * envelope
        # Equal-energy notes intentionally deprive RMS of pitch-dependent energy cues.
        note *= .12 / np.sqrt(np.mean(note*note))
        index = round(start*SR)
        output[index:index+length] += note
    return output


def room(audio, seed, *, snr=18):
    filtered = signal.sosfiltfilt(signal.butter(3, [180, 2800], btype="bandpass", fs=SR, output="sos"), audio)
    result = filtered.copy()
    for delay, gain in ((.017, .35), (.041, .22), (.073, .12)):
        n = round(delay*SR)
        result[n:] += filtered[:-n] * gain
    rng = np.random.default_rng(seed)
    result += rng.normal(0, np.sqrt(np.mean(result*result))*10**(-snr/20), len(result))
    return result * .4


def cases(seeds):
    offsets = (.37, 1.23, -1.77, 2.83, -2.71)
    for seed in seeds:
        melody = instrument(seed)
        backing = instrument(seed+1000, regular=False) * 1.3
        for group in ("irregular_clean", "regular_pitch_clean", "regular_pitch_room",
                      "regular_pitch_mix", "regular_pitch_distorted", "repeated_note", "unrelated_same_rhythm"):
            base = instrument(seed, regular=False) if group == "irregular_clean" else melody
            reference, external = base, base
            kind = "positive"
            if group == "regular_pitch_room":
                reference = room(base, seed)
            elif group == "regular_pitch_mix":
                reference = room(base + .15*backing, seed)
                external = base + backing
            elif group == "regular_pitch_distorted":
                reference = room(base, seed, snr=12)
                external = np.tanh(base*7)
            elif group == "repeated_note":
                reference = instrument(seed, fixed_note=True)
                external = reference
                kind = "ambiguous"
            elif group == "unrelated_same_rhythm":
                external = instrument(seed+5000)
                kind = "negative"
            offset = offsets[seed % len(offsets)]
            start_reference = 12.
            start_external = start_reference + offset
            # Both are independent continuous crops: no shared onset/silence/end cue.
            x = reference[round(start_reference*SR):round((start_reference+26)*SR)]
            y = external[round(start_external*SR):round((start_external+22)*SR)]
            yield dict(group=group, seed=seed, kind=kind,
                       truth_ms=offset*1000 if kind == "positive" else None), x, y


def evaluate_pair(meta, x, y):
    rows = []
    started = perf_counter()
    bank_x, bank_y = feature_bank(x), feature_bank(y)
    extraction_ms = (perf_counter()-started)*1000
    for method in METHODS:
        started = perf_counter()
        match = match_features(bank_x[method], bank_y[method])
        error = abs(match["offset_ms"]-meta["truth_ms"]) if meta["truth_ms"] is not None else None
        rows.append(dict(**meta, method=method, **match, error_ms=error,
                         correct=error <= TOLERANCE_MS if error is not None else None,
                         match_ms=(perf_counter()-started)*1000, bank_extraction_ms=extraction_ms))
    started = perf_counter()
    result = estimate_offset_robust(x, y, SR)
    error = abs(result.offset_ms-meta["truth_ms"]) if meta["truth_ms"] is not None else None
    rows.append(dict(**meta, method="production_rms", offset_ms=result.offset_ms,
                     score=result.confidence, rival_score=result.second_confidence,
                     peak_ratio=result.peak_ratio, accepted=result.reliability == "high",
                     error_ms=error, correct=error <= TOLERANCE_MS if error is not None else None,
                     match_ms=(perf_counter()-started)*1000, bank_extraction_ms=0.,
                     production_reliability=result.reliability))
    return rows


def summarize(rows):
    results = []
    for group in dict.fromkeys(row["group"] for row in rows):
        for method in (*METHODS, "production_rms"):
            subset = [r for r in rows if r["group"] == group and r["method"] == method]
            positive = [r for r in subset if r["truth_ms"] is not None]
            accepted = [r for r in subset if r["accepted"]]
            results.append(dict(group=group, method=method, n=len(subset),
                correct=sum(r["correct"] for r in positive) if positive else None,
                accepted=len(accepted),
                wrong_accepted=sum(not r["correct"] if r["truth_ms"] is not None else True for r in accepted),
                median_error_ms=float(np.median([r["error_ms"] for r in positive])) if positive else None,
                max_error_ms=max((r["error_ms"] for r in positive), default=None)))
    return results


def load_real_pairs(manifest):
    for pair in json.loads(manifest.read_text(encoding="utf-8-sig")):
        with TemporaryDirectory(prefix="cover-spectral-") as temporary:
            files = []
            for key in ("video", "audio"):
                wave = extract_audio_to_wav(pair[key], Path(temporary)/f"{key}.wav")
                audio, rate = sf.read(wave)
                divisor = math.gcd(rate, SR)
                files.append(signal.resample_poly(to_mono_float(audio), SR//divisor, rate//divisor))
            yield dict(group=pair["label"], seed=None, kind="real_user_accepted",
                       truth_ms=pair["offset_ms"]), *files


def write_outputs(output, rows):
    output.mkdir(parents=True, exist_ok=True)
    summary = summarize(rows)
    payload = dict(config=dict(sr=SR, feature_hz=FEATURE_HZ, bands=BANDS, window_ms=64,
                    score_min=SCORE_MIN, ratio_min=RATIO_MIN, rival_exclusion_ms=RIVAL_EXCLUSION_MS,
                    error_tolerance_ms=TOLERANCE_MS), summary=summary, rows=rows)
    (output/"results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with (output/"results.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for row in rows for k in row)))
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# Offline alignment experiment", "", "Fixed rules; no production changes. Accepted means experimental gate, not calibrated confidence.",
             "Real-pair truth is a user-accepted offset, not a laboratory timestamp.", "",
             "| Group | Method | N | Within 40ms | Accepted | Wrong/ambiguous accepted | Median error ms | Max error ms |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in summary:
        lines.append(f"| {r['group']} | {r['method']} | {r['n']} | {r['correct']} | {r['accepted']} | {r['wrong_accepted']} | {r['median_error_ms']} | {r['max_error_ms']} |")
    (output/"summary.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print("\n".join(lines), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real-pairs", type=Path)
    parser.add_argument("--seed-start", type=int, default=100)
    parser.add_argument("--seeds", type=int, default=10)
    args = parser.parse_args()
    rows = []
    for meta, x, y in cases(range(args.seed_start, args.seed_start+args.seeds)):
        rows.extend(evaluate_pair(meta, x, y))
        print(f"done {meta['seed']} {meta['group']}", flush=True)
    if args.real_pairs:
        for meta, x, y in load_real_pairs(args.real_pairs):
            rows.extend(evaluate_pair(meta, x, y))
    write_outputs(args.output, rows)


if __name__ == "__main__":
    main()
