"""The track_PRNnn.npz round trip for the `seed_refined` flag (no tracking needed)."""
import numpy as np

from bassanite import tracking


def _save(path, seed):
    n = 40
    track = {"i_prompt": np.ones(n), "q_prompt": np.zeros(n), "doppler_hz": np.zeros(n),
             "code_phase_samp": np.arange(n, dtype=float)}
    result = {"symbols": np.ones(2), "bits": np.ones(2, dtype=np.int8),
              "cn0": np.full(2, 40.0), "lock_cos": np.ones(2), "bit_offset": 3}
    tracking.save_track(path, 7, 4e6, seed, track, result)
    return path


def test_seed_refined_round_trips(tmp_path):
    seed = {"prn": 7, "doppler_hz": -2100.0, "code_phase_samp": 10.0,
            "coarse_doppler_hz": -2000.0, "refined": True}
    assert tracking.load_track(_save(tmp_path / "a.npz", seed))["seed_refined"] is True
    plain = {"prn": 7, "doppler_hz": -2000.0, "code_phase_samp": 10.0}
    assert tracking.load_track(_save(tmp_path / "b.npz", plain))["seed_refined"] is False


def _without_key(path, key):
    with np.load(path) as d:
        kept = {k: d[k] for k in d.files if k != key}
    np.savez(path, **kept)


def test_seed_refined_is_inferred_for_older_files(tmp_path):
    refined = {"prn": 7, "doppler_hz": -2100.0, "code_phase_samp": 10.0,
               "coarse_doppler_hz": -2000.0, "refined": True}
    path = _save(tmp_path / "old_refined.npz", refined)
    _without_key(path, "seed_refined")
    assert tracking.load_track(path)["seed_refined"] is True      # the Dopplers differ

    path = _save(tmp_path / "old_plain.npz", {"prn": 7, "doppler_hz": -2000.0,
                                              "code_phase_samp": 10.0})
    _without_key(path, "seed_refined")
    assert tracking.load_track(path)["seed_refined"] is False     # they are equal

    _without_key(path, "doppler_coarse_hz")                       # oldest files
    assert tracking.load_track(path)["seed_refined"] is False
