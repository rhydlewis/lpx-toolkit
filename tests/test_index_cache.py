"""Tests for `load_index_metadata_cached()` (#46).

Each project on the rollup index is now decorated with a chip row pulled
from `parse_project()`. Parsing 111 projects on every page render would
be wasteful — cache by `(bundle_path, ProjectData mtime)` so warm
renders skip the parse.
"""
from __future__ import annotations

import json
import plistlib
from datetime import datetime
from pathlib import Path

import pytest

import lpx_inspect
from lpx_inspect import load_index_metadata_cached


def _make_bundle(
    root: Path,
    name: str,
    *,
    bpm: float = 120.0,
    key: str = "C",
    gender: str = "major",
    track_count: int = 8,
) -> Path:
    bundle = root / f"{name}.logicx"
    alt = bundle / "Alternatives" / "000"
    alt.mkdir(parents=True)
    md = {
        "SongKey": key, "SongGenderKey": gender,
        "BeatsPerMinute": bpm,
        "SongSignatureNumerator": 4, "SongSignatureDenominator": 4,
        "NumberOfTracks": track_count, "SampleRate": 44100,
    }
    (alt / "MetaData.plist").write_bytes(plistlib.dumps(md))
    (alt / "ProjectData").write_bytes(b"")
    return bundle


# --- shape ---


def test_returns_one_entry_per_project_path(tmp_path):
    p1 = _make_bundle(tmp_path, "alpha")
    p2 = _make_bundle(tmp_path, "beta", bpm=90.0)
    cache = tmp_path / "cache" / "index.json"
    out = load_index_metadata_cached([p1, p2], cache_path=cache)
    assert set(out.keys()) == {str(p1), str(p2)}


def test_entry_carries_all_chip_relevant_fields(tmp_path):
    bundle = _make_bundle(tmp_path, "song", bpm=128.0, key="F#",
                          gender="minor", track_count=42)
    cache = tmp_path / "index.json"
    out = load_index_metadata_cached([bundle], cache_path=cache)
    entry = out[str(bundle)]
    assert entry["name"] == "song"
    assert entry["key"] == "F#"
    assert entry["gender"] == "minor"
    assert entry["bpm"] == 128.0
    assert entry["track_count"] == 42
    # Bundle size + dates must be present for the chip row.
    assert "bundle_size_bytes" in entry
    assert "created_at" in entry
    assert "modified_at" in entry


# --- caching behaviour ---


def test_warm_cache_skips_reparse(tmp_path, monkeypatch):
    """Second call with unchanged ProjectData mtime must not re-parse."""
    bundle = _make_bundle(tmp_path, "song")
    cache = tmp_path / "index.json"

    # Cold pass populates the cache.
    load_index_metadata_cached([bundle], cache_path=cache)

    # Patch parse_project so any second-pass call is loud.
    real = lpx_inspect.parse_project
    calls = {"n": 0}
    def spy(p, *a, **kw):
        calls["n"] += 1
        return real(p, *a, **kw)
    monkeypatch.setattr(lpx_inspect, "parse_project", spy)

    out = load_index_metadata_cached([bundle], cache_path=cache)
    assert calls["n"] == 0, "warm cache must not re-parse"
    assert str(bundle) in out


def test_mtime_advance_invalidates_single_entry(tmp_path):
    """When one project's ProjectData mtime advances, only that entry is
    re-parsed; the rest stay warm."""
    p1 = _make_bundle(tmp_path, "alpha")
    p2 = _make_bundle(tmp_path, "beta")
    cache = tmp_path / "index.json"
    load_index_metadata_cached([p1, p2], cache_path=cache)

    # Bump only p2's ProjectData mtime.
    pdata2 = p2 / "Alternatives" / "000" / "ProjectData"
    pdata2.write_bytes(b"\x00")  # rewrite advances mtime

    # Re-write the cache with a known sentinel for p1; if we re-parse it
    # the sentinel is overwritten, which proves a regression.
    payload = json.loads(cache.read_text())
    payload[str(p1)]["name"] = "SENTINEL_DO_NOT_OVERWRITE"
    cache.write_text(json.dumps(payload))

    out = load_index_metadata_cached([p1, p2], cache_path=cache)
    # p1 stayed warm — sentinel preserved.
    assert out[str(p1)]["name"] == "SENTINEL_DO_NOT_OVERWRITE"
    # p2 was re-parsed — name is the actual stem.
    assert out[str(p2)]["name"] == "beta"


def test_corrupt_cache_falls_back_to_fresh_parse(tmp_path):
    bundle = _make_bundle(tmp_path, "song")
    cache = tmp_path / "index.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{not valid json")

    out = load_index_metadata_cached([bundle], cache_path=cache)
    assert out[str(bundle)]["name"] == "song"


def test_cache_omits_paths_no_longer_present(tmp_path):
    """Stale entries (project deleted off disk) get garbage-collected on
    the next pass — the cache shouldn't grow unbounded."""
    p1 = _make_bundle(tmp_path, "alpha")
    p2 = _make_bundle(tmp_path, "beta")
    cache = tmp_path / "index.json"
    load_index_metadata_cached([p1, p2], cache_path=cache)

    # Now request only p1 (e.g. the user removed beta from their library).
    load_index_metadata_cached([p1], cache_path=cache)
    payload = json.loads(cache.read_text())
    assert str(p1) in payload
    assert str(p2) not in payload


def test_failing_parse_does_not_crash_whole_index(tmp_path, monkeypatch):
    """A malformed bundle must not break the whole index render — log
    silently, skip that entry, return what we can."""
    p1 = _make_bundle(tmp_path, "good")
    p2 = _make_bundle(tmp_path, "broken")

    real = lpx_inspect.parse_project
    def maybe_explode(path, *a, **kw):
        if "broken" in str(path):
            raise ValueError("simulated parse failure")
        return real(path, *a, **kw)
    monkeypatch.setattr(lpx_inspect, "parse_project", maybe_explode)

    cache = tmp_path / "index.json"
    out = load_index_metadata_cached([p1, p2], cache_path=cache)
    assert str(p1) in out
    # The failing entry is *omitted* rather than carrying half-parsed data.
    assert str(p2) not in out
