"""Tests for the cross-project --rollup mode.

Aggregates plugin usage across many .logicx projects and reports which
plugins / vendors / fingerprints appear most often. Answers "which of my
installed plugins do I actually use?" — the migration / decluttering JTBD
from PM feedback.
"""
import plistlib
from pathlib import Path

import pytest

from lpx_inspect import rollup_projects


def _make_minimal_bundle(root: Path, name: str = "demo") -> Path:
    bundle = root / f"{name}.logicx"
    alt = bundle / "Alternatives" / "000"
    alt.mkdir(parents=True)
    md = {
        "SongKey": "C", "SongGenderKey": "major",
        "BeatsPerMinute": 120.0,
        "SongSignatureNumerator": 4, "SongSignatureDenominator": 4,
        "NumberOfTracks": 0,
    }
    (alt / "MetaData.plist").write_bytes(plistlib.dumps(md))
    (alt / "ProjectData").write_bytes(b"")
    return bundle


def test_rollup_returns_empty_for_no_projects():
    """No projects → empty rollup, no crash."""
    result = rollup_projects([], lookup={})
    assert result["projects"] == []
    assert result["fingerprints"] == {}
    assert result["vendors"] == {}


def test_rollup_lists_each_project_summary(tmp_path):
    """Per-project entries record name + path + counts."""
    p1 = _make_minimal_bundle(tmp_path, "song-a")
    p2 = _make_minimal_bundle(tmp_path, "song-b")
    result = rollup_projects([p1, p2], lookup={})
    assert len(result["projects"]) == 2
    names = {p["name"] for p in result["projects"]}
    assert names == {"song-a", "song-b"}


def test_rollup_skips_unparseable_projects_without_failing(tmp_path):
    """A bad project shouldn't crash the whole rollup. Skip it."""
    good = _make_minimal_bundle(tmp_path, "good")
    bad = tmp_path / "bad.logicx"
    bad.mkdir()  # missing Alternatives/* — will fail parse
    result = rollup_projects([good, bad], lookup={})
    # Only the good project shows up
    assert len(result["projects"]) == 1
    assert result["projects"][0]["name"] == "good"


def test_rollup_aggregates_fingerprints_and_vendors_with_counts():
    """The aggregator counts each fingerprint and vendor across projects.
    Test by injecting fake project summaries directly."""
    from lpx_inspect import aggregate_rollup
    project_jsons = [
        {"project": {"name": "a"}, "vendors": {"Toon": 2, "SToy": 1},
         "tracks": [
             {"instrument": {"fingerprint": "aumu/EZk2/Toon"}, "midi_fx": [], "audio_fx": []},
             {"instrument": None, "midi_fx": [], "audio_fx": [{"fingerprint": "aufx/EB  /SToy"}]},
         ]},
        {"project": {"name": "b"}, "vendors": {"Toon": 1, "Bgrn": 1},
         "tracks": [
             {"instrument": {"fingerprint": "aumu/EZk2/Toon"}, "midi_fx": [], "audio_fx": []},
             {"instrument": None, "midi_fx": [], "audio_fx": [{"fingerprint": "aufx/Akrc/Bgrn"}]},
         ]},
    ]
    result = aggregate_rollup(project_jsons)
    assert result["fingerprints"]["aumu/EZk2/Toon"] == 2  # in both projects
    assert result["fingerprints"]["aufx/EB  /SToy"] == 1
    assert result["fingerprints"]["aufx/Akrc/Bgrn"] == 1
    assert result["vendors"]["Toon"] == 3  # 2 + 1
    assert result["vendors"]["SToy"] == 1
    assert result["vendors"]["Bgrn"] == 1
