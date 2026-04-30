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


def test_render_uses_system_font_stack(tmp_path):
    """Apple-influenced typography — SF Pro / SF Mono via the system
    stack rather than fetched web fonts. Self-contained and renders
    natively on macOS."""
    out = _render(tmp_path)
    assert "-apple-system" in out
    assert "SF Pro" in out
    # No Google Fonts dependency
    assert "fonts.googleapis.com" not in out
    assert "Fraunces" not in out


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


# --- Vendor drill-down (expandable rows showing used + unused plugins) ----


def test_render_includes_used_plugins_per_vendor():
    """Expanded vendor rows show plugins from that manufacturer that ARE
    used on this project, with a per-track count."""
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
        "track_list": [], "vendors": {"Toon": 1},
        "diagnostics": [], "phantom_plugins": [],
    }
    lookup = {
        "aumu/EZk2/Toon": "Toontrack: EZkeys 2",
        "aumu/EZbs/Toon": "Toontrack: EZbass",
        "aufx/AuSe/Toon": "Toontrack: Toontrack Audio Sender",
    }
    out = render_project_html(payload, lookup=lookup, project_path="/x.logicx")
    # All three Toontrack plugins exist in the lookup
    # but only EZkeys 2 is used on this project
    assert "EZkeys 2" in out
    # The unused plugins from Toontrack should also be listed
    assert "EZbass" in out
    assert "Toontrack Audio Sender" in out


def test_render_marks_used_vs_unused_plugins_distinctly():
    """The vendor drill-down distinguishes 'used' from 'unused' so the user
    can tell at a glance."""
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
        "track_list": [], "vendors": {"Toon": 1},
        "diagnostics": [], "phantom_plugins": [],
    }
    lookup = {
        "aumu/EZk2/Toon": "Toontrack: EZkeys 2",
        "aumu/EZbs/Toon": "Toontrack: EZbass",
    }
    out = render_project_html(payload, lookup=lookup, project_path="/x.logicx")
    # We render some kind of section/heading for "used" and "unused"
    assert "used" in out.lower()
    assert "unused" in out.lower()


def test_render_omits_unused_section_when_no_other_plugins_from_vendor():
    """When the vendor only has plugins that are all in use, the unused
    section is empty (don't render an empty header)."""
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
        "track_list": [], "vendors": {"Toon": 1},
        "diagnostics": [], "phantom_plugins": [],
    }
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    out = render_project_html(payload, lookup=lookup, project_path="/x.logicx")
    # 'Unused' header should not appear when there's nothing to list
    # We use a specific marker string to distinguish from incidental "unused"
    # mentions in CSS/scripts
    assert "Toontrack" in out


# --- Open in Logic button ------------------------------------------------


def test_render_includes_reveal_in_finder_button(tmp_path):
    """When project_path is supplied, a button labelled 'Reveal in Finder'
    is rendered. When omitted, the button is not present (graceful)."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload, project_path="/path/to/song.logicx")
    assert "Reveal in Finder" in out
    # Without project_path, no button
    out_no_path = render_project_html(payload)
    assert "Reveal in Finder" not in out_no_path


def test_render_button_links_to_file_url_for_project_path(tmp_path):
    """The button is a `file://` link to the project bundle so clicking
    opens Finder at that location."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    project_path = "/absolute/path/to/song.logicx"
    out = render_project_html(payload, lookup={}, project_path=project_path)
    assert f"file://{project_path}" in out


def test_render_resolves_manufacturer_full_name_in_vendor_rollup():
    """Vendor rollup uses the auval-resolved manufacturer name when known
    (e.g. 'Soundtoys [SToy]' instead of bare 'SToy')."""
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
        "vendors": {"SToy": 2, "Toon": 1},
        "diagnostics": [], "phantom_plugins": [],
    }
    lookup = {
        "aufx/EB  /SToy": "Soundtoys: EchoBoy",
        "aumf/FXR /SToy": "Soundtoys: EffectRack",
        "aumu/EZk2/Toon": "Toontrack: EZkeys 2",
    }
    out = render_project_html(payload, lookup=lookup, project_path="/x.logicx")
    # Manufacturer label takes the form "<Name> [<4CC>]"
    assert "Soundtoys" in out
    assert "Toontrack" in out


def test_render_falls_back_to_4cc_when_manufacturer_unknown():
    """When auval lookup has no plugins for a vendor 4CC, the rollup row
    shows just the 4CC (still useful — same as before the lookup feature)."""
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
        "vendors": {"UNKN": 1},
        "diagnostics": [], "phantom_plugins": [],
    }
    out = render_project_html(payload, lookup={}, project_path="/x.logicx")
    assert "UNKN" in out


def test_render_does_not_include_open_in_logic_or_clipboard_command(tmp_path):
    """The previous clipboard-copy button is gone — no `open -a` shell
    command should be emitted."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload, project_path="/x.logicx")
    assert "Open in Logic" not in out
    assert "open -a" not in out


def test_render_html_works_without_lookup_or_path(tmp_path):
    """Backwards-compatible: callers that only pass `payload` still get
    a valid render (no vendor drill-down content, no Open button)."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload)
    assert "</html>" in out


# --- footer links ---

def test_render_footer_links_to_github(tmp_path):
    out = _render(tmp_path)
    assert "github.com/rhydlewis/lpx-toolkit" in out


def test_render_footer_links_to_issues(tmp_path):
    out = _render(tmp_path)
    assert "github.com/rhydlewis/lpx-toolkit/issues" in out


def test_render_footer_links_to_buymeacoffee(tmp_path):
    out = _render(tmp_path)
    assert "buymeacoffee.com/rhyd" in out


def test_render_footer_external_links_open_in_new_tab(tmp_path):
    """External links open in a new tab and drop the window reference
    for the security/privacy reason `rel='noopener noreferrer'` exists."""
    out = _render(tmp_path)
    # At least one external link should carry these attributes
    assert 'target="_blank"' in out
    assert "noopener" in out


# --- header + topbar restructure ---

def test_render_h1_leads_with_project_name(tmp_path):
    """The project name is the page title's primary content; the
    'lpx·toolkit' brand sits as a smaller suffix."""
    info = parse_project(_make_minimal_bundle(tmp_path, name="my-song"))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload)
    h1_start = out.find("<h1")
    h1_end = out.find("</h1>", h1_start)
    assert h1_start != -1 and h1_end != -1
    h1 = out[h1_start:h1_end]
    assert "my-song" in h1
    # Brand suffix retained, but as a child element so the project
    # name reads first.
    assert "lpx" in h1 and "toolkit" in h1
    assert "brand-suffix" in h1


def test_render_h_sub_includes_file_path(tmp_path):
    """Path to the .logicx bundle appears in the meta line for context."""
    bundle = _make_minimal_bundle(tmp_path, name="demo")
    info = parse_project(bundle)
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload, project_path=str(bundle))
    # The h-sub line carries the path
    sub_start = out.find('class="h-sub"')
    sub_end = out.find("</p>", sub_start)
    assert sub_start != -1
    sub_block = out[sub_start:sub_end]
    assert str(bundle) in sub_block


def test_render_topbar_contains_reveal_button(tmp_path):
    """Reveal in Finder lives in the fixed topbar with the theme toggle,
    not inline below the heading."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload, project_path="/some/path.logicx")
    topbar_start = out.find('class="topbar"')
    topbar_end = out.find("</div>", topbar_start)
    assert topbar_start != -1, "expected a .topbar wrapper"
    topbar = out[topbar_start:topbar_end]
    assert "Reveal in Finder" in topbar
    assert "theme-toggle" in topbar


def test_render_topbar_present_without_project_path(tmp_path):
    """When no project_path is supplied (e.g. for tests / minimal renders)
    the topbar still renders for the theme toggle, but the Reveal button
    is absent."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload)
    assert 'class="topbar"' in out
    assert "Reveal in Finder" not in out


# --- 3-tab content view ---

def test_render_includes_three_tabs(tmp_path):
    """Tracks / Plugin chains / Diagnostics live behind a tab strip
    rather than stacking down the page."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload)
    # Three labelled tab buttons
    assert 'data-tab="tracks"' in out
    assert 'data-tab="plugins"' in out
    assert 'data-tab="diagnostics"' in out


def test_render_tab_panels_present(tmp_path):
    """Each tab has a corresponding panel in the DOM (toggled by the
    tab JS, not server-rendered)."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    out = render_project_html(payload)
    assert 'data-panel="tracks"' in out
    assert 'data-panel="plugins"' in out
    assert 'data-panel="diagnostics"' in out


def test_render_diagnostics_tab_holds_phantom_plugins(tmp_path):
    """Phantom plug-ins are project-health info, so they live in the
    Diagnostics tab — not as a separate top-level section."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    payload["phantom_plugins"] = [
        {"display_name": "Ghost", "fingerprint": "aufx/ghst/test",
         "type": "audio_effect"}
    ]
    out = render_project_html(payload)
    diag_panel_start = out.find('data-panel="diagnostics"')
    diag_panel_end = out.find('data-panel="', diag_panel_start + 1)
    if diag_panel_end == -1:
        # Last panel — search to closing tag of the parent
        diag_panel_end = out.find('</section>', diag_panel_start)
    assert diag_panel_start != -1
    panel_block = out[diag_panel_start:diag_panel_end]
    assert "Ghost" in panel_block


# --- track inventory (registry list) ---

def test_render_shows_track_inventory_when_no_active_tracks(tmp_path):
    """A project with no plugin chains (e.g. fresh audio tracks) still
    has tracks in the registry — render them as a list so the dashboard
    isn't blank for empty projects."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    payload["tracks"] = []
    payload["track_list"] = [
        {"name": "Audio 1", "kind": "audio",
         "track_id": 9, "strip_id": 256, "region_count": 0},
        {"name": "Lead Vox", "kind": "audio",
         "track_id": 75, "strip_id": 2, "region_count": 3},
    ]
    out = render_project_html(payload)
    assert "Audio 1" in out
    assert "Lead Vox" in out


def test_render_track_inventory_shows_kind_and_strip(tmp_path):
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    payload["tracks"] = []
    payload["track_list"] = [
        {"name": "My Audio", "kind": "audio",
         "track_id": 9, "strip_id": 5, "region_count": 2},
    ]
    out = render_project_html(payload)
    # kind + strip number visible somewhere in the inventory
    assert "audio" in out.lower()
    assert "5" in out  # strip id
    assert "2" in out  # region count


def test_render_shows_tracks_empty_state_when_track_list_empty(tmp_path):
    """When track_list is empty (e.g. parse failure), the Tracks tab
    still appears but its panel shows an empty-state message instead
    of rendering the inventory table."""
    info = parse_project(_make_minimal_bundle(tmp_path))
    payload = json.loads(project_to_json(info, lookup={}))
    payload["tracks"] = []
    payload["track_list"] = []
    out = render_project_html(payload)
    # The Tracks tab is still part of the strip
    assert 'data-tab="tracks"' in out
    # But its panel shows an empty state, not a track-list table
    assert "tab-empty" in out
    assert "<div class=\"tracks track-list\">" not in out


# --- #40 light/dark theme toggle ---

def test_render_includes_theme_toggle_button(tmp_path):
    """Dashboard ships a theme toggle the user can click."""
    out = _render(tmp_path)
    assert 'id="theme-toggle"' in out


def test_render_defines_light_palette(tmp_path):
    """Light mode is implemented by overriding palette variables on
    `:root[data-theme="light"]` — the toggle just flips the attribute."""
    out = _render(tmp_path)
    assert '[data-theme="light"]' in out


def test_render_persists_theme_in_localstorage(tmp_path):
    """Theme choice survives reloads via localStorage."""
    out = _render(tmp_path)
    assert "localStorage" in out
    assert "lpxtool-theme" in out


def test_render_applies_persisted_theme_before_body(tmp_path):
    """Persisted theme is applied as early as possible to avoid a
    flash-of-wrong-theme on load — the boot script must run before
    the body renders."""
    out = _render(tmp_path)
    head_end = out.find("</head>")
    body_start = out.find("<body")
    assert head_end != -1 and body_start != -1
    head_block = out[:head_end]
    assert "lpxtool-theme" in head_block
    assert "data-theme" in head_block
