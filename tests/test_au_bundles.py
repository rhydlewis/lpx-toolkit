"""Tests for #43 — AU bundle metadata scanner.

Reads CFBundleShortVersionString + AudioComponents from each
*.component/Contents/Info.plist and pairs each fingerprint with the
codesign authority. Adds the version + signed-by data the inventory tab
shows alongside auval's installed/used cross-reference.

`auval -l` answers *what fingerprint is installed*; the bundle scan
answers *which build is on disk* and *who signed it*.
"""
from pathlib import Path
from types import SimpleNamespace

import plistlib
import pytest

from lpx_inspect import (
    parse_codesign_authority,
    scan_au_bundle,
    scan_au_bundles,
    scan_au_bundles_cached,
)


def _make_au_bundle(
    root: Path,
    name: str = "DemoFX",
    *,
    short_version: str | None = "1.2.3",
    bundle_version: str | None = "1.2.3.45",
    components: list[dict] | None = None,
) -> Path:
    """Synthesise a minimal AU bundle layout with a crafted Info.plist."""
    bundle = root / f"{name}.component"
    contents = bundle / "Contents"
    contents.mkdir(parents=True)
    if components is None:
        components = [{
            "type": "aufx",
            "subtype": "Demo",
            "manufacturer": "Test",
            "name": "TestVendor: DemoFX",
            "version": 65536,
        }]
    plist: dict = {
        "CFBundleIdentifier": f"com.test.{name.lower()}",
        "AudioComponents": components,
    }
    if short_version is not None:
        plist["CFBundleShortVersionString"] = short_version
    if bundle_version is not None:
        plist["CFBundleVersion"] = bundle_version
    (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
    return bundle


# --- parse_codesign_authority -------------------------------------------------


def test_parse_codesign_extracts_developer_id_authority():
    """The leaf 'Developer ID Application: <Vendor> (<TeamID>)' line is the
    canonical signal for who signed the bundle."""
    output = (
        "Executable=/path/to/x.component/Contents/MacOS/x\n"
        "Format=bundle with Mach-O universal\n"
        "Authority=Developer ID Application: Native Instruments GmbH (783P5RS31U)\n"
        "Authority=Developer ID Certification Authority\n"
        "Authority=Apple Root CA\n"
        "TeamIdentifier=783P5RS31U\n"
    )
    assert parse_codesign_authority(output) == "Native Instruments GmbH"


def test_parse_codesign_handles_apple_system_signing():
    """Apple's built-in AUs are signed via 'Software Signing', not Developer ID."""
    output = (
        "Authority=Software Signing\n"
        "Authority=Apple Code Signing Certification Authority\n"
        "Authority=Apple Root CA\n"
    )
    assert parse_codesign_authority(output) == "Apple"


def test_parse_codesign_returns_none_for_unsigned_output():
    """Unsigned bundles produce 'code object is not signed at all' — no
    Authority lines at all. Must not crash."""
    output = "code object is not signed at all\n"
    assert parse_codesign_authority(output) is None


def test_parse_codesign_handles_empty_string():
    assert parse_codesign_authority("") is None


# --- scan_au_bundle (single bundle) -------------------------------------------


def test_scan_au_bundle_decodes_integer_encoded_version(tmp_path):
    """Some vendors (e.g. Arturia) store the AU API integer version
    `(major<<16)|(minor<<8)|patch` directly in CFBundleShortVersionString,
    which renders as gibberish like '458752'. Decode it back to '7.0.0'."""
    bundle = _make_au_bundle(tmp_path, short_version="458752")
    [entry] = scan_au_bundle(bundle, codesign_runner=lambda p: "")
    assert entry["version"] == "7.0.0"


def test_scan_au_bundle_passes_through_dotted_version(tmp_path):
    """A normal version string with dots is left untouched."""
    bundle = _make_au_bundle(tmp_path, short_version="2.1.5")
    [entry] = scan_au_bundle(bundle, codesign_runner=lambda p: "")
    assert entry["version"] == "2.1.5"


def test_scan_au_bundle_extracts_short_version(tmp_path):
    bundle = _make_au_bundle(tmp_path, short_version="2.5.1")
    [entry] = scan_au_bundle(bundle, codesign_runner=lambda p: "")
    assert entry["version"] == "2.5.1"


def test_scan_au_bundle_falls_back_to_bundle_version_when_short_missing(tmp_path):
    bundle = _make_au_bundle(
        tmp_path,
        short_version=None,
        bundle_version="0.9.0.beta",
    )
    [entry] = scan_au_bundle(bundle, codesign_runner=lambda p: "")
    assert entry["version"] == "0.9.0.beta"


def test_scan_au_bundle_returns_none_version_when_both_missing(tmp_path):
    bundle = _make_au_bundle(tmp_path, short_version=None, bundle_version=None)
    [entry] = scan_au_bundle(bundle, codesign_runner=lambda p: "")
    assert entry["version"] is None


def test_scan_au_bundle_emits_one_entry_per_audiocomponent(tmp_path):
    """A single .component bundle can register multiple AUs (e.g. effect +
    MIDI processor variant). Each gets its own fingerprint entry."""
    bundle = _make_au_bundle(tmp_path, components=[
        {"type": "aufx", "subtype": "ABCD", "manufacturer": "Test",
         "name": "Test: A", "version": 1},
        {"type": "aumf", "subtype": "EFGH", "manufacturer": "Test",
         "name": "Test: B", "version": 1},
    ])
    entries = scan_au_bundle(bundle, codesign_runner=lambda p: "")
    fps = {e["fingerprint"] for e in entries}
    assert fps == {"aufx/ABCD/Test", "aumf/EFGH/Test"}


def test_scan_au_bundle_carries_bundle_path_and_signed_by(tmp_path):
    bundle = _make_au_bundle(tmp_path)
    runner = lambda p: (
        f"Authority=Developer ID Application: TestVendor LLC (XYZ123)\n"
    )
    [entry] = scan_au_bundle(bundle, codesign_runner=runner)
    assert entry["bundle_path"] == str(bundle)
    assert entry["signed_by"] == "TestVendor LLC"


def test_scan_au_bundle_signed_by_is_none_when_codesign_runner_raises(tmp_path):
    """Fail-soft: a missing codesign tool or non-zero exit must not crash
    the whole inventory render. Unknown signing → None, render '—'."""
    bundle = _make_au_bundle(tmp_path)
    def bomb(p):
        raise FileNotFoundError("codesign")
    [entry] = scan_au_bundle(bundle, codesign_runner=bomb)
    assert entry["signed_by"] is None


def test_scan_au_bundle_skips_when_info_plist_missing(tmp_path):
    """An incomplete bundle (no Info.plist) must produce no entries."""
    bundle = tmp_path / "Bad.component" / "Contents"
    bundle.mkdir(parents=True)
    # no Info.plist
    assert scan_au_bundle(bundle.parent, codesign_runner=lambda p: "") == []


# --- scan_au_bundles (whole directories) --------------------------------------


def test_scan_au_bundles_walks_each_directory(tmp_path):
    sys_dir = tmp_path / "system"
    user_dir = tmp_path / "user"
    sys_dir.mkdir()
    user_dir.mkdir()
    _make_au_bundle(sys_dir, "Sys", components=[
        {"type": "aufx", "subtype": "SyTm", "manufacturer": "Test",
         "name": "Test: Sys", "version": 1},
    ])
    _make_au_bundle(user_dir, "Usr", components=[
        {"type": "aufx", "subtype": "UsTm", "manufacturer": "Test",
         "name": "Test: Usr", "version": 1},
    ])
    table = scan_au_bundles(
        [sys_dir, user_dir],
        codesign_runner=lambda p: "",
    )
    assert "aufx/SyTm/Test" in table
    assert "aufx/UsTm/Test" in table


def test_scan_au_bundles_user_dir_overrides_system_for_same_fingerprint(tmp_path):
    """If both directories register the same fingerprint, the user's copy
    (later in iteration order) wins — that's the one Logic loads."""
    sys_dir = tmp_path / "system"
    user_dir = tmp_path / "user"
    sys_dir.mkdir()
    user_dir.mkdir()
    _make_au_bundle(sys_dir, "Demo", short_version="1.0.0")
    _make_au_bundle(user_dir, "Demo", short_version="9.9.9")
    table = scan_au_bundles(
        [sys_dir, user_dir],
        codesign_runner=lambda p: "",
    )
    assert table["aufx/Demo/Test"]["version"] == "9.9.9"


def test_scan_au_bundles_handles_missing_directories(tmp_path):
    """A non-existent directory (e.g. ~/Library/Audio/Plug-Ins/Components on
    a fresh user account) is silently skipped — return what we have."""
    table = scan_au_bundles(
        [tmp_path / "does-not-exist"],
        codesign_runner=lambda p: "",
    )
    assert table == {}


# --- scan_au_bundles_cached ---------------------------------------------------


def test_scan_au_bundles_cached_reads_from_disk_on_warm_path(tmp_path):
    """On a warm cache (dir mtime unchanged), return the cache without
    re-scanning bundles. Verified by spying on the codesign runner — a
    warm hit must not invoke it."""
    cache = tmp_path / "au-bundles.json"
    sys_dir = tmp_path / "components"
    sys_dir.mkdir()
    _make_au_bundle(sys_dir, "Demo")

    calls: list[Path] = []
    def spy(p):
        calls.append(p)
        return ""

    # Cold scan populates the cache and calls codesign once.
    scan_au_bundles_cached(
        components_dirs=[sys_dir], codesign_runner=spy, cache_path=cache,
    )
    assert calls, "cold scan should have run codesign"
    cold_calls = len(calls)

    # Warm scan must NOT hit the filesystem / codesign — pure cache read.
    table = scan_au_bundles_cached(
        components_dirs=[sys_dir], codesign_runner=spy, cache_path=cache,
    )
    assert "aufx/Demo/Test" in table
    assert len(calls) == cold_calls, "warm cache must not re-run codesign"


def test_scan_au_bundles_cached_refreshes_when_mtime_advances(tmp_path):
    """When any tracked components dir's mtime advances, the cache is stale
    and we re-scan. Same convention as auval cache."""
    import os
    cache = tmp_path / "au-bundles.json"
    sys_dir = tmp_path / "components"
    sys_dir.mkdir()
    _make_au_bundle(sys_dir, "OrigName")
    # Cold scan
    table1 = scan_au_bundles_cached(
        components_dirs=[sys_dir],
        codesign_runner=lambda p: "",
        cache_path=cache,
    )
    assert "aufx/Demo/Test" in table1

    # Add a second bundle with a new fingerprint → bump mtime
    _make_au_bundle(sys_dir, "NewName", components=[
        {"type": "aufx", "subtype": "NewT", "manufacturer": "Test",
         "name": "Test: New", "version": 1},
    ])
    # Force mtime advance even on fast filesystems
    os.utime(sys_dir, None)

    table2 = scan_au_bundles_cached(
        components_dirs=[sys_dir],
        codesign_runner=lambda p: "",
        cache_path=cache,
    )
    assert "aufx/NewT/Test" in table2


def test_scan_au_bundles_cached_handles_corrupt_cache(tmp_path):
    cache = tmp_path / "au-bundles.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("{not json")
    sys_dir = tmp_path / "components"
    sys_dir.mkdir()
    _make_au_bundle(sys_dir, "Demo")
    table = scan_au_bundles_cached(
        components_dirs=[sys_dir],
        codesign_runner=lambda p: "",
        cache_path=cache,
    )
    assert "aufx/Demo/Test" in table
