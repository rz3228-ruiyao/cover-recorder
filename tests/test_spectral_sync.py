from __future__ import annotations

from dataclasses import asdict
import json
import numpy as np
import pytest
from scipy.signal import resample_poly
import soundfile as sf

from cover_syncer.spectral_sync import estimate_offset_hybrid
from cover_syncer.sync import SyncError, estimate_offset_from_files


def performance(*, same_note=False, seed=717, duration=40):
    rate = 16000
    rng = np.random.default_rng(seed)
    audio = np.zeros(rate*duration)
    t = np.arange(round(.3*rate))/rate
    envelope = np.sin(np.pi*np.arange(len(t))/len(t))**2
    for start in np.arange(.5, duration-.5, .5):
        midi = 57 if same_note else rng.choice([45, 48, 52, 55, 57, 60, 64, 67])
        hz = 440*2**((int(midi)-69)/12)
        note = sum(np.sin(2*np.pi*hz*h*t)/h for h in (1, 2, 3))*envelope
        note *= .15/np.sqrt(np.mean(note**2))
        first = round(start*rate)
        audio[first:first+len(t)] += note
    return audio


@pytest.mark.parametrize("offset_ms,rate", [(-1235, 16000), (0, 22050), (2375, 48000)])
def test_regular_equal_energy_notes_get_verified_spectral_offset(offset_ms, rate):
    source = performance()
    x = source[8*16000:24*16000]
    first = round((8+offset_ms/1000)*16000)
    y = source[first:first+16*16000]
    x = resample_poly(x, rate, 16000)
    y = resample_poly(y, rate, 16000)
    # Different stereo gains should not change the temporal result.
    y = np.column_stack((y*.3, y*.5))
    result = estimate_offset_hybrid(x, y, rate)
    assert abs(result.offset_ms-offset_ms) <= 10, result
    assert result.reliability == "high", result
    assert result.segment_count >= 2
    assert result.consistent_segment_count == result.segment_count
    assert json.loads(json.dumps(asdict(result)))["reliability"] == "high"


def test_repeated_note_is_not_automatically_exportable():
    source = performance(same_note=True)
    result = estimate_offset_hybrid(source[8*16000:24*16000], source[10*16000:26*16000], 16000)
    assert result.reliability == "low", result


def test_repeated_melodic_phrase_is_not_automatically_exportable():
    phrase = performance()[:4*16000]
    source = np.tile(phrase, 10)
    result = estimate_offset_hybrid(source[8*16000:24*16000], source[10*16000:26*16000], 16000)
    assert result.reliability != "high", result


def test_clock_drift_is_not_mistaken_for_one_reliable_offset():
    source = performance()
    x = source[8*16000:36*16000]
    y = resample_poly(x, 101, 100)
    result = estimate_offset_hybrid(x, y, 16000)
    assert result.reliability != "high", result


def test_different_melody_with_same_beat_is_rejected():
    a, b = performance(), performance(seed=1987)
    result = estimate_offset_hybrid(a[8*16000:24*16000], b[8*16000:24*16000], 16000)
    assert result.reliability != "high", result


def test_time_discontinuity_cannot_pass_segment_verification():
    source = performance()
    x = source[8*16000:30*16000]
    # The first 14 seconds align, but a cut changes the lag for the remainder.
    y = np.r_[source[8*16000:22*16000], source[23*16000:31*16000]]
    result = estimate_offset_hybrid(x, y, 16000)
    assert result.reliability != "high", result


def test_two_strong_but_conflicting_candidates_require_manual_review(monkeypatch):
    from cover_syncer import spectral_sync
    candidates = iter([spectral_sync.Candidate(100, .9, .3, 3.),
                       spectral_sync.Candidate(0, .9, .3, 3.)])
    monkeypatch.setattr(spectral_sync, "match_features", lambda *a, **kw: next(candidates))
    monkeypatch.setattr(spectral_sync, "check_segments", lambda *a: ([1000., 1000.], [True, True]))
    source = performance()[:12*16000]
    result = estimate_offset_hybrid(source, source, 16000)
    assert result.reliability == "low"
    assert "不同位置" in result.decision_reason
    assert result.spectral_offset_ms == 1000 and result.envelope_offset_ms == 0


def test_short_candidate_without_independent_segments_needs_manual_review():
    source = performance()
    x = source[8*16000:round(13.6*16000)]
    result = estimate_offset_hybrid(x, x*.7, 16000)
    assert abs(result.offset_ms) <= 10
    assert result.reliability == "medium", result
    assert result.segment_count == 0


@pytest.mark.parametrize("audio", [np.array([]), np.zeros(8*16000), np.full(8*16000, np.nan)])
def test_unusable_input_reports_analysis_error(audio):
    with pytest.raises(SyncError):
        estimate_offset_hybrid(audio, audio, 16000)


def test_file_entrypoint_uses_spectral_candidates_and_preserves_files(tmp_path):
    source = performance()
    x, y = source[8*16000:24*16000], source[9*16000:25*16000]
    a, b = tmp_path/"reference.wav", tmp_path/"external.wav"
    sf.write(a, x, 16000)
    sf.write(b, y, 16000)
    before = a.read_bytes(), b.read_bytes()
    result = estimate_offset_from_files(str(a), str(b), robust=True)
    assert result.method.startswith("spectral"), result
    assert result.offset_ms == 1000
    assert result.reliability == "high", result
    assert before == (a.read_bytes(), b.read_bytes())
