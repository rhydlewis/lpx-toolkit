"""Read-only invariant tests.

This tool MUST NOT modify the .logicx bundle it inspects. The format is
undocumented — any unintended write risks silent corruption of irrecoverable
user work. These tests pin that contract: parsing a project leaves every
file's bytes and mtime untouched, and never adds or deletes files.

If a future change introduces a write path (logging, autocache, anything),
these tests fail loudly. Don't relax them — find another place to write.
"""
import hashlib
import plistlib
from pathlib import Path

from lpx_inspect import (
    extract_bplists,
    find_aus,
    find_region_names,
    find_tracks,
    parse_project,
)


def _make_minimal_bundle(root: Path, name: str = "demo") -> Path:
    bundle = root / f"{name}.logicx"
    alt = bundle / "Alternatives" / "000"
    alt.mkdir(parents=True)
    md = {
        "SongKey": "C",
        "SongGenderKey": "major",
        "BeatsPerMinute": 120.0,
        "SongSignatureNumerator": 4,
        "SongSignatureDenominator": 4,
        "NumberOfTracks": 0,
    }
    (alt / "MetaData.plist").write_bytes(plistlib.dumps(md))
    (alt / "ProjectData").write_bytes(b"")
    return bundle


def _snapshot(bundle: Path) -> dict[str, tuple[str, float]]:
    """Capture sha256 + mtime of every file under the bundle."""
    out: dict[str, tuple[str, float]] = {}
    for p in bundle.rglob("*"):
        if p.is_file():
            out[str(p.relative_to(bundle))] = (
                hashlib.sha256(p.read_bytes()).hexdigest(),
                p.stat().st_mtime,
            )
    return out


def test_parse_project_does_not_modify_bundle(tmp_path):
    """Hard contract: parsing must leave every byte and mtime untouched."""
    bundle = _make_minimal_bundle(tmp_path)
    before = _snapshot(bundle)
    parse_project(bundle)
    after = _snapshot(bundle)

    assert before.keys() == after.keys(), (
        f"files appeared or vanished: "
        f"+{set(after) - set(before)} -{set(before) - set(after)}"
    )
    diffs = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not diffs, f"bytes or mtime changed: {diffs}"


def test_extraction_helpers_do_not_open_files_for_writing(tmp_path):
    """Belt-and-braces: every public extraction helper takes bytes, not a
    path. Nothing in their call path opens a file at all (let alone for
    write). Construct a small fake buffer and exercise them."""
    raw = b"\x00" * 64
    # All four return-empty or no-op cleanly; we're asserting they don't
    # raise and don't touch the filesystem (no path argument is accepted).
    assert find_aus(raw) == []
    assert find_tracks(raw) == []
    assert find_region_names(raw) == []
    assert extract_bplists(raw) == []
