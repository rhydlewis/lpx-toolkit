"""Tests for project-level metadata extraction.

The .logicx bundle's filesystem timestamps are the only reliable source for
project created/modified dates — neither MetaData.plist nor
ProjectInformation.plist carries them. Tests build a synthetic bundle in a
tmp_path so they don't depend on a real Logic project on disk.
"""
import plistlib
from datetime import datetime
from pathlib import Path

import pytest

from lpx_inspect import parse_project


def _make_minimal_bundle(root: Path, name: str = "demo") -> Path:
    """Synthesise a .logicx bundle with the smallest plist + ProjectData
    pair our parser will accept."""
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


def test_parse_project_exposes_modified_at_from_bundle(tmp_path):
    """`info.modified_at` should be a datetime from the bundle's mtime."""
    bundle = _make_minimal_bundle(tmp_path)
    info = parse_project(bundle)
    assert isinstance(info.modified_at, datetime)


def test_parse_project_exposes_created_at_from_bundle(tmp_path):
    """`info.created_at` should be a datetime from the bundle's birthtime
    (macOS) or mtime as a fallback on filesystems without birthtime."""
    bundle = _make_minimal_bundle(tmp_path)
    info = parse_project(bundle)
    assert isinstance(info.created_at, datetime)


def test_parse_project_modified_at_reflects_filesystem_mtime(tmp_path):
    """Touch the bundle to a known mtime, then assert parse_project surfaces
    that timestamp. Confirms we're reading the right field, not just any
    arbitrary datetime."""
    import os
    bundle = _make_minimal_bundle(tmp_path)
    target = datetime(2024, 6, 15, 10, 30, 0).timestamp()
    os.utime(bundle, (target, target))

    info = parse_project(bundle)
    assert info.modified_at.timestamp() == pytest.approx(target, abs=1)
