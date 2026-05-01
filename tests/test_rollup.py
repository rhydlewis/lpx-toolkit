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


# --- /rollup HTML — chip parity with the serve index (#46 follow-up) ---


def test_rollup_html_renders_chip_row_when_metadata_provided(tmp_path):
    """The /rollup HTML view shows the same chip set as the serve index
    on each project card — no visual divergence between the two surfaces."""
    from lpx_inspect import _render_rollup_html
    p1 = _make_minimal_bundle(tmp_path, "alpha")
    rollup = {
        "projects": [{"name": "alpha", "plugin_count": 3,
                      "unique_fingerprints": 3}],
        "fingerprints": {}, "vendors": {},
    }
    metadata = {str(p1): {
        "mtime": 1.0,
        "name": "alpha",
        "key": "F#", "gender": "minor",
        "bpm": 92.0,
        "track_count": 14,
        "bundle_size_bytes": 64 * 1024 * 1024,
        "created_at": "2024-01-01T00:00:00",
        "modified_at": "2026-04-25T00:00:00",
    }}
    out = _render_rollup_html(rollup, [p1], metadata=metadata)
    assert 'class="proj-chips"' in out
    # All five chip values present in the card row.
    assert "F# minor" in out
    assert "92" in out
    assert "14" in out
    assert "MB" in out


def test_rollup_html_omits_chips_when_metadata_absent(tmp_path):
    """Backwards compat: callers that don't supply metadata get the
    pre-#46 layout — name + summary line only, no chip row."""
    from lpx_inspect import _render_rollup_html
    p1 = _make_minimal_bundle(tmp_path, "alpha")
    rollup = {
        "projects": [{"name": "alpha", "plugin_count": 0,
                      "unique_fingerprints": 0}],
        "fingerprints": {}, "vendors": {},
    }
    out = _render_rollup_html(rollup, [p1])
    assert 'class="proj-chips"' not in out


def test_rollup_html_card_has_reveal_in_finder_link(tmp_path):
    """The rollup card matches the serve index — both surfaces ship a
    reveal-in-Finder anchor pointing at the /reveal server endpoint."""
    from lpx_inspect import _render_rollup_html
    p1 = _make_minimal_bundle(tmp_path, "alpha")
    rollup = {
        "projects": [{"name": "alpha", "plugin_count": 0,
                      "unique_fingerprints": 0}],
        "fingerprints": {}, "vendors": {},
    }
    out = _render_rollup_html(rollup, [p1])
    assert 'class="proj-reveal"' in out
    assert 'href="/reveal?' in out
    import urllib.parse
    assert urllib.parse.quote(str(p1), safe="") in out
    assert 'title="Reveal in Finder"' in out
