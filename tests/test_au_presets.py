"""Tests for #44 — `.aupreset` counter.

For each AU we know about (via the bundle scan from #43), walk
`~/Library/Audio/Presets/<manufacturer_name>/<plugin_name>/` and the
system equivalent, counting `.aupreset` files at any depth.

Sparse on most Macs — Kontakt (`.nki`), Omnisphere (internal library) and
many other vendors use their own preset formats. The column counts what
follows the AU `.aupreset` convention, not "every preset on disk".
"""
from pathlib import Path

import pytest

from lpx_inspect import (
    count_au_presets,
    count_au_presets_cached,
)


def _make_preset(root: Path, vendor: str, plugin: str, name: str = "P") -> Path:
    """Create a fake .aupreset file at the canonical AU preset path."""
    path = root / vendor / plugin / f"{name}.aupreset"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _bundles(*entries) -> dict[str, dict]:
    """Build a fingerprint→bundle-meta map from `(fp, vendor, plugin)` tuples."""
    return {
        fp: {"manufacturer_name": vendor, "plugin_name": plugin}
        for fp, vendor, plugin in entries
    }


# --- count_au_presets ---------------------------------------------------------


def test_returns_zero_for_every_fingerprint_when_dirs_are_empty(tmp_path):
    bundles = _bundles(
        ("aumu/EZk2/Toon", "Toontrack", "EZkeys 2"),
        ("aufx/CGTX/ksWV", "Waves", "CLA Guitars"),
    )
    counts = count_au_presets(bundles, presets_dirs=[tmp_path])
    assert counts == {"aumu/EZk2/Toon": 0, "aufx/CGTX/ksWV": 0}


def test_counts_one_preset_for_matching_plugin(tmp_path):
    _make_preset(tmp_path, "Toontrack", "EZkeys 2", "MyPreset")
    bundles = _bundles(("aumu/EZk2/Toon", "Toontrack", "EZkeys 2"))
    counts = count_au_presets(bundles, presets_dirs=[tmp_path])
    assert counts == {"aumu/EZk2/Toon": 1}


def test_counts_presets_at_any_depth(tmp_path):
    """Many users organise presets into sub-banks (`Bass/`, `Lead/`...) —
    must count files anywhere in the plugin's tree, not just the root."""
    base = tmp_path / "Toontrack" / "EZkeys 2"
    (base / "Bass").mkdir(parents=True)
    (base / "Lead" / "Synthwave").mkdir(parents=True)
    (base / "Bass" / "p1.aupreset").write_bytes(b"")
    (base / "Bass" / "p2.aupreset").write_bytes(b"")
    (base / "Lead" / "Synthwave" / "p3.aupreset").write_bytes(b"")
    (base / "p4.aupreset").write_bytes(b"")
    bundles = _bundles(("aumu/EZk2/Toon", "Toontrack", "EZkeys 2"))
    counts = count_au_presets(bundles, presets_dirs=[tmp_path])
    assert counts["aumu/EZk2/Toon"] == 4


def test_only_counts_aupreset_files(tmp_path):
    """`.fxp`, `.nki`, `.fxb` are vendor-specific — out of scope. We count
    only `.aupreset` so the number matches Logic's preset menu count."""
    base = tmp_path / "TestVendor" / "Plugin"
    base.mkdir(parents=True)
    (base / "real.aupreset").write_bytes(b"")
    (base / "vendor.fxp").write_bytes(b"")
    (base / "kontakt.nki").write_bytes(b"")
    (base / "readme.txt").write_bytes(b"")
    bundles = _bundles(("aufx/Plug/TstV", "TestVendor", "Plugin"))
    counts = count_au_presets(bundles, presets_dirs=[tmp_path])
    assert counts["aufx/Plug/TstV"] == 1


def test_combines_counts_from_multiple_preset_roots(tmp_path):
    """A plugin with presets in BOTH ~/Library and /Library should sum.
    System bank + user bank is a real use case (Kontakt instruments etc.)."""
    user_root = tmp_path / "user"
    sys_root = tmp_path / "system"
    _make_preset(user_root, "Vendor", "Plug", "user1")
    _make_preset(user_root, "Vendor", "Plug", "user2")
    _make_preset(sys_root, "Vendor", "Plug", "system1")
    bundles = _bundles(("aufx/Plug/Test", "Vendor", "Plug"))
    counts = count_au_presets(
        bundles, presets_dirs=[user_root, sys_root],
    )
    assert counts["aufx/Plug/Test"] == 3


def test_skips_bundle_entries_with_no_manufacturer_or_plugin(tmp_path):
    """Some bundles have an AudioComponents entry without a parseable
    'Vendor: Plugin' name. We can't form a preset path → count = 0."""
    bundles = {"aufx/Xyz1/UNKN": {
        "manufacturer_name": "",
        "plugin_name": "",
    }}
    counts = count_au_presets(bundles, presets_dirs=[tmp_path])
    assert counts == {"aufx/Xyz1/UNKN": 0}


def test_handles_missing_preset_directory(tmp_path):
    """A non-existent `~/Library/Audio/Presets/` doesn't crash — return 0
    for every fingerprint."""
    bundles = _bundles(("aumu/EZk2/Toon", "Toontrack", "EZkeys 2"))
    counts = count_au_presets(
        bundles, presets_dirs=[tmp_path / "missing"],
    )
    assert counts == {"aumu/EZk2/Toon": 0}


# --- count_au_presets_cached --------------------------------------------------


def test_cached_returns_same_counts_as_uncached(tmp_path):
    cache = tmp_path / "presets.json"
    presets_root = tmp_path / "presets"
    _make_preset(presets_root, "Toontrack", "EZkeys 2")
    bundles = _bundles(("aumu/EZk2/Toon", "Toontrack", "EZkeys 2"))
    counts = count_au_presets_cached(
        bundles, presets_dirs=[presets_root], cache_path=cache,
    )
    assert counts == {"aumu/EZk2/Toon": 1}
    assert cache.exists()


def test_cached_uses_warm_cache_without_rescanning(tmp_path, monkeypatch):
    """Warm cache + unchanged top-level mtime → no filesystem walk.

    Verified by patching the underlying scan to a spy: a warm hit must
    not invoke it.
    """
    import lpx_inspect
    cache = tmp_path / "presets.json"
    presets_root = tmp_path / "presets"
    presets_root.mkdir()
    bundles = _bundles(("aumu/EZk2/Toon", "Toontrack", "EZkeys 2"))

    # Cold scan populates the cache.
    first = count_au_presets_cached(
        bundles, presets_dirs=[presets_root], cache_path=cache,
    )
    assert first == {"aumu/EZk2/Toon": 0}

    # Patch the uncached scanner so any warm-path call is detectable.
    calls = {"n": 0}
    real = lpx_inspect.count_au_presets
    def spy(bundles, presets_dirs):
        calls["n"] += 1
        return real(bundles, presets_dirs=presets_dirs)
    monkeypatch.setattr(lpx_inspect, "count_au_presets", spy)

    # Warm hit — same mtime, same fingerprints → must return cache.
    second = count_au_presets_cached(
        bundles, presets_dirs=[presets_root], cache_path=cache,
    )
    assert second == {"aumu/EZk2/Toon": 0}
    assert calls["n"] == 0, "warm cache must not re-run the scanner"


def test_cached_refreshes_when_top_level_mtime_advances(tmp_path):
    cache = tmp_path / "presets.json"
    presets_root = tmp_path / "presets"
    presets_root.mkdir()
    bundles = _bundles(("aumu/EZk2/Toon", "Toontrack", "EZkeys 2"))

    count_au_presets_cached(
        bundles, presets_dirs=[presets_root], cache_path=cache,
    )

    # Adding a top-level vendor dir DOES bump the presets-root mtime,
    # which is the supported invalidation path.
    _make_preset(presets_root, "Toontrack", "EZkeys 2", "p")
    import os
    os.utime(presets_root, None)

    counts = count_au_presets_cached(
        bundles, presets_dirs=[presets_root], cache_path=cache,
    )
    assert counts == {"aumu/EZk2/Toon": 1}
