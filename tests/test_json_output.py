"""Tests for the JSON output mode (`--json` flag).

The schema is the inspector's lingua franca — the HTML dashboard, the
cross-project rollup, and any third-party tooling consume it. Lock it down
here so the wire format is stable, and so any change is an explicit edit
of these tests rather than an accidental drift.
"""
import json
import plistlib
from pathlib import Path

import pytest

from lpx_inspect import parse_project, project_to_json


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
        "SampleRate": 44100,
    }
    (alt / "MetaData.plist").write_bytes(plistlib.dumps(md))
    (alt / "ProjectData").write_bytes(b"")
    return bundle


def test_project_to_json_returns_valid_json_string(tmp_path):
    """The function returns a string; the string must be valid JSON."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    result = project_to_json(info, lookup={})
    assert isinstance(result, str)
    json.loads(result)  # raises if invalid


def test_project_to_json_top_level_keys(tmp_path):
    """The schema has a stable set of top-level keys callers can rely on."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    result = json.loads(project_to_json(info, lookup={}))
    assert set(result.keys()) >= {
        "schema_version", "project", "tracks", "vendors",
    }


def test_project_to_json_schema_version_is_present(tmp_path):
    """Schema version pins the wire format. Every consumer should check it."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    result = json.loads(project_to_json(info, lookup={}))
    assert result["schema_version"] == 1


def test_project_to_json_project_metadata_block(tmp_path):
    """The project block carries name, key/gender, signature, tempo, dates."""
    info = parse_project(_make_minimal_bundle(tmp_path, name="my-song"))
    result = json.loads(project_to_json(info, lookup={}))
    p = result["project"]
    assert p["name"] == "my-song"
    assert p["key"] == "C"
    assert p["gender"] == "major"
    assert p["bpm"] == 120.0
    assert p["time_signature"] == "4/4"
    assert p["track_count"] == 0
    assert "created_at" in p
    assert "modified_at" in p


def test_project_to_json_dates_are_iso_strings(tmp_path):
    """Datetimes serialise as ISO 8601 — JSON-native, parser-friendly."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    result = json.loads(project_to_json(info, lookup={}))
    p = result["project"]
    # Must round-trip via fromisoformat
    from datetime import datetime
    datetime.fromisoformat(p["created_at"])
    datetime.fromisoformat(p["modified_at"])


def test_project_to_json_tracks_is_a_list(tmp_path):
    """Empty project → empty tracks list (not null)."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    result = json.loads(project_to_json(info, lookup={}))
    assert result["tracks"] == []


def test_project_to_json_vendors_is_a_dict_keyed_by_4cc(tmp_path):
    """Vendor rollup: manufacturer 4CC → count of plugins from that vendor.
    Empty when no plugins."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    result = json.loads(project_to_json(info, lookup={}))
    assert result["vendors"] == {}
