"""Tests for the auval lookup cache.

`auval -l` is slow (5-30s cold start) and macOS-only. We cache the parsed
table to disk and invalidate when the system Audio Unit components folder
changes (mtime advance). These tests pin that contract.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import lpx_inspect
from lpx_inspect import (
    AUVAL_CACHE_PATH,
    auval_lookup,
    auval_lookup_cached,
    save_auval_cache,
    load_auval_cache,
)


def test_save_then_load_round_trips_the_table(tmp_path):
    """Round-trip: save → load returns the same dict."""
    cache_file = tmp_path / "auval.json"
    table = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2", "aufx/EB  /SToy": "Soundtoys: EchoBoy"}
    save_auval_cache(table, components_mtime=12345.0, path=cache_file)
    loaded, mtime = load_auval_cache(path=cache_file)
    assert loaded == table
    assert mtime == 12345.0


def test_load_returns_empty_when_cache_file_missing(tmp_path):
    """Missing cache file is not an error — return empty dict."""
    loaded, mtime = load_auval_cache(path=tmp_path / "does-not-exist.json")
    assert loaded == {}
    assert mtime is None


def test_load_returns_empty_when_cache_file_corrupt(tmp_path):
    """Corrupt JSON shouldn't crash callers."""
    cache_file = tmp_path / "auval.json"
    cache_file.write_text("not json {")
    loaded, mtime = load_auval_cache(path=cache_file)
    assert loaded == {}
    assert mtime is None


def test_cached_lookup_returns_cache_when_components_mtime_unchanged(tmp_path, monkeypatch):
    """When the cache exists and components mtime matches, skip auval."""
    cache_file = tmp_path / "auval.json"
    cached_table = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    save_auval_cache(cached_table, components_mtime=999.0, path=cache_file)

    # Stub get_components_mtime → returns the same value as the cached one
    monkeypatch.setattr(lpx_inspect, "get_components_mtime", lambda: 999.0)

    # Stub subprocess.run to fail loudly if called — proves we used the cache
    def boom(*args, **kwargs):
        raise AssertionError("auval should NOT be called when cache is fresh")
    monkeypatch.setattr(lpx_inspect.subprocess, "run", boom)

    result = auval_lookup_cached(path=cache_file)
    assert result == cached_table


def test_cached_lookup_re_runs_auval_when_components_mtime_advances(tmp_path, monkeypatch):
    """When components folder is newer than the cache, re-run auval and refresh."""
    cache_file = tmp_path / "auval.json"
    save_auval_cache({"aumu/old/Toon": "Old Plugin"}, components_mtime=100.0, path=cache_file)

    # Components folder is now newer
    monkeypatch.setattr(lpx_inspect, "get_components_mtime", lambda: 200.0)

    new_auval_output = (
        "    AU Validation Tool\n"
        "aumu EZk2 Toon  -  Toontrack: EZkeys 2     (file:///plugin/) [AUv2]\n"
    )
    monkeypatch.setattr(
        lpx_inspect.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout=new_auval_output),
    )

    result = auval_lookup_cached(path=cache_file)
    assert result == {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}

    # Cache file should now hold the fresh data + new mtime
    refreshed, mtime = load_auval_cache(path=cache_file)
    assert refreshed == {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    assert mtime == 200.0


def test_cached_lookup_runs_auval_when_no_cache_exists(tmp_path, monkeypatch):
    """Cold start — no cache yet; run auval and write the cache."""
    cache_file = tmp_path / "auval.json"
    assert not cache_file.exists()

    monkeypatch.setattr(lpx_inspect, "get_components_mtime", lambda: 555.0)
    auval_output = "aumu EZk2 Toon  -  Toontrack: EZkeys 2     (file:///plugin/) [AUv2]\n"
    monkeypatch.setattr(
        lpx_inspect.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(stdout=auval_output),
    )

    result = auval_lookup_cached(path=cache_file)
    assert result == {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    assert cache_file.exists()


def test_cached_lookup_returns_empty_when_auval_unavailable(tmp_path, monkeypatch):
    """Non-macOS host or broken auval — degrade to empty dict, don't crash."""
    cache_file = tmp_path / "auval.json"
    monkeypatch.setattr(lpx_inspect, "get_components_mtime", lambda: 100.0)

    def missing(*a, **kw):
        raise FileNotFoundError("auval not installed")
    monkeypatch.setattr(lpx_inspect.subprocess, "run", missing)

    result = auval_lookup_cached(path=cache_file)
    assert result == {}


def test_default_cache_path_is_in_user_cache_dir():
    """The default cache lives at ~/.cache/lpx-toolkit/auval.json — outside
    the project bundle (read-only contract is preserved)."""
    assert str(AUVAL_CACHE_PATH).endswith(".cache/lpx-toolkit/auval.json")
