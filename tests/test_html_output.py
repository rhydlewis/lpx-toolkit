"""Tests for the HTML dashboard renderer (#20).

Renders project state to a self-contained HTML file styled to match
inspector-mockup.html. Auto-opens in the macOS default browser via the
`open` shell command.

These tests assert structure (key sections, escaping, valid HTML
fragments) rather than pixel layout — the look-and-feel is the
mockup's CSS, copied verbatim.
"""
import html
import json
import plistlib
from pathlib import Path

import pytest

from lpx_inspect import render_project_html, parse_project, project_to_json


def _make_minimal_bundle(root: Path, name: str = "demo") -> Path:
    bundle = root / f"{name}.logicx"
    alt = bundle / "Alternatives" / "000"
    alt.mkdir(parents=True)
    md = {
        "SongKey": "C", "SongGenderKey": "major",
        "BeatsPerMinute": 120.0,
        "SongSignatureNumerator": 4, "SongSignatureDenominator": 4,
        "NumberOfTracks": 0, "SampleRate": 48000,
        "FrameRateIndex": 1, "AudioFiles": [],
        "ImpulsResponsesFiles": [],
    }
    (alt / "MetaData.plist").write_bytes(plistlib.dumps(md))
    (alt / "ProjectData").write_bytes(b"")
    return bundle


def _render(tmp_path, **overrides) -> str:
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    payload.update(overrides)
    return render_project_html(payload)


def test_render_returns_a_string(tmp_path):
    out = _render(tmp_path)
    assert isinstance(out, str)
    assert len(out) > 0


def test_render_starts_with_html_doctype(tmp_path):
    """Self-contained HTML5 document — must begin with <!doctype html>."""
    out = _render(tmp_path)
    assert out.lstrip().lower().startswith("<!doctype html>")


def test_render_includes_inline_style_block(tmp_path):
    """Pixel-faithful styling is embedded — no external stylesheet."""
    out = _render(tmp_path)
    assert "<style>" in out
    assert "</style>" in out


def test_render_loads_google_fonts(tmp_path):
    """Pixel-faithful match to mockup uses Fraunces + IBM Plex Mono."""
    out = _render(tmp_path)
    assert "Fraunces" in out
    assert "IBM+Plex+Mono" in out or "IBM Plex Mono" in out


def test_render_includes_project_name_in_title(tmp_path):
    info = parse_project(_make_minimal_bundle(tmp_path, name="my-song"))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload)
    # Project name appears at least in the <title> and somewhere in the body
    assert "<title>" in out
    assert "my-song" in out


def test_render_includes_metadata_block(tmp_path):
    """Sample rate, BPM, key, signature and dates appear in the rendered HTML."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload)
    assert "120" in out  # BPM
    assert "48" in out and ("48 000" in out or "48000" in out)  # sample rate
    assert "4 / 4" in out or "4/4" in out  # time signature
    assert "C" in out  # key


def test_render_html_escapes_track_names(tmp_path):
    """Track names with HTML-meta chars must not break the document.
    Critical for plugin display names like 'CLA Guitars (m->s)' that
    contain '>' which must be escaped to &gt;."""
    payload = {
        "schema_version": 1,
        "project": {
            "name": "<bad>",
            "key": "C", "gender": "major", "bpm": 120.0,
            "time_signature": "4/4", "track_count": 0,
            "created_at": "2024-01-01T00:00:00",
            "modified_at": "2024-01-01T00:00:00",
            "sample_rate": 44100, "bundle_size_bytes": 0,
            "audio_file_count": 0, "impulse_response_count": 0,
            "frame_rate_index": 1, "frame_rate": 25.0,
        },
        "tracks": [{
            "kind": "audio", "strip_name": "Audio 1",
            "display_name": "Lead <Vocal>",
            "is_active": True,
            "instrument": None,
            "midi_fx": [],
            "audio_fx": [{
                "type_code": "aufx", "subtype": "CGTX", "manufacturer": "ksWV",
                "fingerprint": "aufx/CGTX/ksWV",
                "display_name": "CLA Guitars (m->s)",
                "resolved_name": "Waves: CLA Guitars (m->s)",
            }],
        }],
        "track_list": [], "vendors": {}, "diagnostics": [],
        "phantom_plugins": [],
    }
    out = render_project_html(payload)
    # Raw '<bad>' or 'Lead <Vocal>' or '(m->s)' must not appear unescaped
    assert "<bad>" not in out
    assert "Lead <Vocal>" not in out
    # The escaped forms must be there instead
    assert "&lt;bad&gt;" in out
    assert "&lt;Vocal&gt;" in out
    assert "(m-&gt;s)" in out


def test_render_includes_tracks_table_when_tracks_present():
    """A non-empty tracks list produces a <table> (or equivalent grid)."""
    payload = {
        "schema_version": 1,
        "project": {
            "name": "x", "key": "C", "gender": "major", "bpm": 120.0,
            "time_signature": "4/4", "track_count": 1,
            "created_at": "2024-01-01T00:00:00",
            "modified_at": "2024-01-01T00:00:00",
            "sample_rate": 44100, "bundle_size_bytes": 0,
            "audio_file_count": 0, "impulse_response_count": 0,
            "frame_rate_index": 1, "frame_rate": 25.0,
        },
        "tracks": [{
            "kind": "instrument", "strip_name": "Inst 1",
            "display_name": "EZkeys 2", "is_active": True,
            "instrument": {
                "type_code": "aumu", "subtype": "EZk2", "manufacturer": "Toon",
                "fingerprint": "aumu/EZk2/Toon",
                "display_name": "EZkeys 2",
                "resolved_name": "Toontrack: EZkeys 2",
            },
            "midi_fx": [], "audio_fx": [],
        }],
        "track_list": [], "vendors": {"Toon": 1}, "diagnostics": [],
        "phantom_plugins": [],
    }
    out = render_project_html(payload)
    # Strip name and display name both appear
    assert "Inst 1" in out
    assert "EZkeys 2" in out
    assert "Toontrack" in out  # auval-resolved vendor name


def test_render_includes_vendor_rollup_when_vendors_present():
    payload = {
        "schema_version": 1,
        "project": {
            "name": "x", "key": "C", "gender": "major", "bpm": 120.0,
            "time_signature": "4/4", "track_count": 0,
            "created_at": "2024-01-01T00:00:00",
            "modified_at": "2024-01-01T00:00:00",
            "sample_rate": 44100, "bundle_size_bytes": 0,
            "audio_file_count": 0, "impulse_response_count": 0,
            "frame_rate_index": 1, "frame_rate": 25.0,
        },
        "tracks": [], "track_list": [],
        "vendors": {"Toon": 5, "SToy": 3, "appl": 1},
        "diagnostics": [], "phantom_plugins": [],
    }
    out = render_project_html(payload)
    assert "Toon" in out
    assert "SToy" in out


def test_render_includes_phantom_section_when_phantoms_present():
    payload = {
        "schema_version": 1,
        "project": {
            "name": "x", "key": "C", "gender": "major", "bpm": 120.0,
            "time_signature": "4/4", "track_count": 0,
            "created_at": "2024-01-01T00:00:00",
            "modified_at": "2024-01-01T00:00:00",
            "sample_rate": 44100, "bundle_size_bytes": 0,
            "audio_file_count": 0, "impulse_response_count": 0,
            "frame_rate_index": 1, "frame_rate": 25.0,
        },
        "tracks": [], "track_list": [], "vendors": {},
        "diagnostics": [],
        "phantom_plugins": [{
            "type_code": "aumu", "subtype": "Kat1", "manufacturer": "Artu",
            "fingerprint": "aumu/Kat1/Artu",
            "display_name": "Pigments",
            "resolved_name": "Arturia: Pigments",
        }],
    }
    out = render_project_html(payload)
    assert "Pigments" in out
    assert "phantom" in out.lower()


def test_render_includes_diagnostics_section_when_warnings_present():
    payload = {
        "schema_version": 1,
        "project": {
            "name": "x", "key": "C", "gender": "major", "bpm": 120.0,
            "time_signature": "4/4", "track_count": 0,
            "created_at": "2024-01-01T00:00:00",
            "modified_at": "2024-01-01T00:00:00",
            "sample_rate": 44100, "bundle_size_bytes": 0,
            "audio_file_count": 0, "impulse_response_count": 0,
            "frame_rate_index": 1, "frame_rate": 25.0,
        },
        "tracks": [], "track_list": [], "vendors": {},
        "diagnostics": [{
            "kind": "unresolved_plugin",
            "track": "Inst 4",
            "slot": "instrument",
            "fingerprint": "aumu/Xyz1/UNKN",
            "display_name": "Mystery",
        }],
        "phantom_plugins": [],
    }
    out = render_project_html(payload)
    assert "diagnostic" in out.lower() or "warning" in out.lower()
    assert "Mystery" in out
    assert "Xyz1" in out


def test_render_handles_completely_empty_payload(tmp_path):
    """An empty project (no tracks, vendors, phantoms, diagnostics) renders
    without crashing and produces valid HTML."""
    out = _render(tmp_path)
    # Closing body and html tags
    assert "</body>" in out
    assert "</html>" in out
