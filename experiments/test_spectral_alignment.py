"""Checks for the offline experiment; not a claim of production integration."""
import numpy as np
import pytest

from spectral_alignment import SR, feature_bank, instrument, match_features


@pytest.mark.parametrize("offset_ms", [-1235, 0, 1235])
def test_spectral_match_recovers_signed_non_grid_crop_offset(offset_ms):
    source = instrument(711)
    a = source[10*SR:26*SR]
    start = round((10+offset_ms/1000)*SR)
    b = source[start:start+16*SR]
    bank_a, bank_b = feature_bank(a), feature_bank(b)
    for method in ("logbands", "bandflux"):
        result = match_features(bank_a[method], bank_b[method])
        assert abs(result["offset_ms"]-offset_ms) <= 10, (method, result)
        assert result["accepted"], (method, result)


def test_periodic_same_note_is_not_treated_as_unique_match():
    source = instrument(711, fixed_note=True)
    a = source[10*SR:26*SR]
    b = source[12*SR:28*SR]
    bank_a, bank_b = feature_bank(a), feature_bank(b)
    for method in ("rms", "logbands", "bandflux"):
        result = match_features(bank_a[method], bank_b[method])
        assert not result["accepted"], (method, result)


def test_silence_cannot_generate_a_confident_match():
    with pytest.raises(ValueError, match="signal"):
        feature_bank(np.zeros(8*SR))
    with pytest.raises(ValueError, match="signal"):
        match_features(np.zeros((3, 800)), np.zeros((3, 800)))
