"""Tests for the auval-inventory cross-reference (#42).

The inventory tab answers: "Before I open this project on this Mac, will
all its plugins resolve — and which ones are missing?"

`inventory_for_project()` cross-references the user's local AU registry
(parsed `auval -l` table) against the plugins this project actually uses
(active tracks only). Returns:

  - entries: every installed AU, marked used / unused for this project
  - unresolved: every fingerprint referenced by the project that is NOT
    in the local auval registry (these would trigger Logic's missing-
    plugin alert on load)
"""
import json

from lpx_inspect import inventory_for_project, project_to_json, parse_project


# A trivial fake "project payload" — only the keys inventory_for_project()
# inspects (`tracks` + each track's instrument/midi_fx/audio_fx). We don't
# need a parsed bundle for these unit tests.
def _payload(tracks):
    return {
        "tracks": tracks,
        # Other keys are irrelevant to the helper but real payloads carry them.
        "phantom_plugins": [],
    }


def _au(fp, name="", mfr=None):
    typ, sub, manu = fp.split("/")
    return {
        "type_code": typ,
        "subtype": sub,
        "manufacturer": manu,
        "fingerprint": fp,
        "display_name": name,
        "resolved_name": None,
    }


def _track(*aus):
    """Single instrument-or-effect track wrapping the supplied AUs."""
    inst = aus[0] if aus and aus[0]["type_code"] == "aumu" else None
    fx = [a for a in aus if a is not inst]
    return {
        "kind": "audio",
        "strip_name": "Audio 1",
        "display_name": "x",
        "is_active": True,
        "instrument": inst,
        "midi_fx": [],
        "audio_fx": fx,
    }


# --- Returned shape -------------------------------------------------------


def test_returns_entries_and_unresolved_keys():
    result = inventory_for_project(lookup={}, payload=_payload([]))
    assert set(result.keys()) >= {"entries", "unresolved"}
    assert isinstance(result["entries"], list)
    assert isinstance(result["unresolved"], list)


def test_empty_lookup_and_empty_project_returns_empty_lists():
    result = inventory_for_project(lookup={}, payload=_payload([]))
    assert result["entries"] == []
    assert result["unresolved"] == []


# --- Inventory entries ----------------------------------------------------


def test_every_lookup_entry_appears_in_inventory():
    """The user's full auval registry shows in the tab — installed-but-unused
    is the whole point. The list is bounded by the registry, not the project."""
    lookup = {
        "aumu/EZk2/Toon": "Toontrack: EZkeys 2",
        "aumu/EZbs/Toon": "Toontrack: EZbass",
        "aufx/AuSe/Toon": "Toontrack: Audio Sender",
    }
    result = inventory_for_project(lookup=lookup, payload=_payload([]))
    fps = {e["fingerprint"] for e in result["entries"]}
    assert fps == set(lookup.keys())


def test_used_in_project_is_true_when_track_references_fingerprint():
    lookup = {
        "aumu/EZk2/Toon": "Toontrack: EZkeys 2",
        "aumu/EZbs/Toon": "Toontrack: EZbass",
    }
    payload = _payload([_track(_au("aumu/EZk2/Toon", "EZkeys 2"))])
    result = inventory_for_project(lookup=lookup, payload=payload)
    by_fp = {e["fingerprint"]: e for e in result["entries"]}
    assert by_fp["aumu/EZk2/Toon"]["used_in_project"] is True
    assert by_fp["aumu/EZbs/Toon"]["used_in_project"] is False


def test_entry_includes_humanised_type_label():
    """aumu/aufx/aumf get readable type labels — the user shouldn't have to
    memorise four-character codes to read this column."""
    lookup = {
        "aumu/EZk2/Toon": "Toontrack: EZkeys 2",
        "aufx/CGTX/ksWV": "Waves: CLA Guitars",
        "aumf/SCRP/Test": "Test: Scripter",
    }
    result = inventory_for_project(lookup=lookup, payload=_payload([]))
    by_fp = {e["fingerprint"]: e for e in result["entries"]}
    assert by_fp["aumu/EZk2/Toon"]["type"] == "Instrument"
    assert by_fp["aufx/CGTX/ksWV"]["type"] == "Effect"
    assert by_fp["aumf/SCRP/Test"]["type"] == "MIDI FX"


def test_entry_includes_4cc_components():
    """The 4CCs are exposed for power users / clipboard copying. Trailing or
    leading spaces in 4CCs MUST be preserved (per CLAUDE.md auval quirks)."""
    lookup = {"aufx/EB  /SToy": "Soundtoys: EchoBoy"}
    result = inventory_for_project(lookup=lookup, payload=_payload([]))
    [entry] = result["entries"]
    assert entry["type_4cc"] == "aufx"
    assert entry["subtype_4cc"] == "EB  "
    assert entry["manufacturer_4cc"] == "SToy"


def test_entry_extracts_plugin_name_from_auval_label():
    """auval labels are 'Manufacturer: Plugin Name'. The display name is the
    plugin name only — the manufacturer goes in its own column."""
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    [entry] = inventory_for_project(lookup=lookup, payload=_payload([]))["entries"]
    assert entry["name"] == "EZkeys 2"
    assert entry["manufacturer"] == "Toontrack"


def test_entry_falls_back_to_4cc_when_label_has_no_colon():
    """Some auval entries don't have a 'Vendor: Plugin' format. Fall back to
    the raw label as the name and the manufacturer 4CC for the vendor."""
    lookup = {"aufx/Xyz1/UNKN": "weird-plugin"}
    [entry] = inventory_for_project(lookup=lookup, payload=_payload([]))["entries"]
    assert entry["name"] == "weird-plugin"
    assert entry["manufacturer"] == "UNKN"


def test_entries_are_sorted_by_manufacturer_then_name():
    """When nothing is used, ordering falls back to alphabetical by
    manufacturer then by plugin name — stable and predictable."""
    lookup = {
        "aumu/B/Toon": "Toontrack: B",
        "aumu/A/Toon": "Toontrack: A",
        "aufx/Z/Wavs": "Waves: Z",
        "aufx/A/Wavs": "Waves: A",
        "aumu/X/Artu": "Arturia: X",
    }
    result = inventory_for_project(lookup=lookup, payload=_payload([]))
    names = [(e["manufacturer"], e["name"]) for e in result["entries"]]
    assert names == [
        ("Arturia", "X"),
        ("Toontrack", "A"),
        ("Toontrack", "B"),
        ("Waves", "A"),
        ("Waves", "Z"),
    ]


def test_entries_used_rows_sort_above_unused_rows():
    """With hundreds of installed AUs and only a handful used in this
    project, used rows must surface at the top — otherwise the user has
    to scan an alphabetical list of 300+ entries to find their three."""
    lookup = {
        "aumu/A/Aaaa": "Aardvark: A",   # alphabetically first, but unused
        "aumu/B/Bbbb": "Bbbbeee: B",    # unused
        "aumu/Z/Zzzz": "Zzzz: Z",       # used — should still float to top
    }
    payload = _payload([_track(_au("aumu/Z/Zzzz", "Z"))])
    result = inventory_for_project(lookup=lookup, payload=payload)
    names = [(e["manufacturer"], e["name"], e["used_in_project"])
             for e in result["entries"]]
    assert names == [
        ("Zzzz", "Z", True),
        ("Aardvark", "A", False),
        ("Bbbbeee", "B", False),
    ]


# --- Unresolved fingerprints (the missing-plugin banner data) -------------


def test_unresolved_lists_project_fingerprints_not_in_lookup():
    """The pre-flight signal: this project references X plugin, but X isn't
    on this Mac. That's the headline use case for the tab."""
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    payload = _payload([
        _track(_au("aumu/EZk2/Toon", "EZkeys 2")),  # resolved
        _track(_au("aumu/Mssg/UNKN", "Mystery")),   # NOT in lookup
    ])
    result = inventory_for_project(lookup=lookup, payload=payload)
    fps = [u["fingerprint"] for u in result["unresolved"]]
    assert fps == ["aumu/Mssg/UNKN"]


def test_unresolved_carries_display_name_and_4ccs():
    payload = _payload([_track(_au("aumu/Mssg/UNKN", "Mystery"))])
    result = inventory_for_project(lookup={}, payload=payload)
    [u] = result["unresolved"]
    assert u["fingerprint"] == "aumu/Mssg/UNKN"
    assert u["display_name"] == "Mystery"
    assert u["type_4cc"] == "aumu"
    assert u["subtype_4cc"] == "Mssg"
    assert u["manufacturer_4cc"] == "UNKN"


def test_unresolved_dedupes_repeated_fingerprints_across_tracks():
    """Same missing plugin used on five tracks shouldn't appear five times in
    the banner — it's one missing plugin."""
    fp = "aumu/Mssg/UNKN"
    payload = _payload([
        _track(_au(fp, "Mystery")),
        _track(_au(fp, "Mystery")),
    ])
    result = inventory_for_project(lookup={}, payload=payload)
    assert len(result["unresolved"]) == 1


def test_unresolved_is_empty_when_all_project_plugins_resolve():
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    payload = _payload([_track(_au("aumu/EZk2/Toon", "EZkeys 2"))])
    result = inventory_for_project(lookup=lookup, payload=payload)
    assert result["unresolved"] == []


# --- Wiring into project_to_json -----------------------------------------


# --- #43 + #44: bundle metadata + preset counts merged into entries ----


def test_entry_includes_version_when_bundle_data_provided():
    """Each entry carries the AU bundle's version string from
    CFBundleShortVersionString (or CFBundleVersion fallback)."""
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    bundles = {"aumu/EZk2/Toon": {
        "version": "2.5.1", "signed_by": "Toontrack AB",
        "manufacturer_name": "Toontrack", "plugin_name": "EZkeys 2",
        "bundle_path": "/Library/Audio/Plug-Ins/Components/EZkeys 2.component",
    }}
    [entry] = inventory_for_project(
        lookup=lookup, payload=_payload([]), bundles=bundles,
    )["entries"]
    assert entry["version"] == "2.5.1"


def test_entry_includes_signed_by_when_bundle_data_provided():
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    bundles = {"aumu/EZk2/Toon": {
        "version": "2.5.1", "signed_by": "Toontrack AB",
        "manufacturer_name": "Toontrack", "plugin_name": "EZkeys 2",
        "bundle_path": "/x", }}
    [entry] = inventory_for_project(
        lookup=lookup, payload=_payload([]), bundles=bundles,
    )["entries"]
    assert entry["signed_by"] == "Toontrack AB"


def test_entry_version_and_signed_by_default_to_none_without_bundles():
    """Back-compat: callers that don't supply bundles still get a valid
    payload, with version/signed_by set to None."""
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    [entry] = inventory_for_project(
        lookup=lookup, payload=_payload([]),
    )["entries"]
    assert entry["version"] is None
    assert entry["signed_by"] is None


def test_entry_falls_back_to_none_when_bundle_missing_for_fingerprint():
    """auval may know about an AU that the bundle scan missed (e.g. older
    OS scan that didn't hit a freshly-added plugin). The entry still
    appears, with version/signed_by = None."""
    lookup = {
        "aumu/EZk2/Toon": "Toontrack: EZkeys 2",
        "aufx/EB  /SToy": "Soundtoys: EchoBoy",
    }
    bundles = {"aumu/EZk2/Toon": {
        "version": "2.5.1", "signed_by": "Toontrack AB",
        "manufacturer_name": "Toontrack", "plugin_name": "EZkeys 2",
        "bundle_path": "/x"}}
    result = inventory_for_project(
        lookup=lookup, payload=_payload([]), bundles=bundles,
    )
    by_fp = {e["fingerprint"]: e for e in result["entries"]}
    assert by_fp["aumu/EZk2/Toon"]["version"] == "2.5.1"
    assert by_fp["aufx/EB  /SToy"]["version"] is None
    assert by_fp["aufx/EB  /SToy"]["signed_by"] is None


def test_entry_includes_preset_count_when_presets_provided():
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    presets = {"aumu/EZk2/Toon": 12}
    [entry] = inventory_for_project(
        lookup=lookup, payload=_payload([]), presets=presets,
    )["entries"]
    assert entry["preset_count"] == 12


def test_entry_preset_count_defaults_to_zero():
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    [entry] = inventory_for_project(
        lookup=lookup, payload=_payload([]),
    )["entries"]
    assert entry["preset_count"] == 0


def test_project_to_json_exposes_auval_inventory(tmp_path):
    """The JSON payload carries the cross-reference under a top-level key,
    so HTML/JSON consumers see the same data."""
    import plistlib
    bundle = tmp_path / "demo.logicx"
    alt = bundle / "Alternatives" / "000"
    alt.mkdir(parents=True)
    md = {
        "SongKey": "C", "SongGenderKey": "major",
        "BeatsPerMinute": 120.0,
        "SongSignatureNumerator": 4, "SongSignatureDenominator": 4,
        "NumberOfTracks": 0, "SampleRate": 44100,
    }
    (alt / "MetaData.plist").write_bytes(plistlib.dumps(md))
    (alt / "ProjectData").write_bytes(b"")
    info = parse_project(bundle)
    payload = json.loads(project_to_json(
        info,
        lookup={"aumu/EZk2/Toon": "Toontrack: EZkeys 2"},
    ))
    assert "auval_inventory" in payload
    inv = payload["auval_inventory"]
    assert "entries" in inv
    assert "unresolved" in inv
    fps = {e["fingerprint"] for e in inv["entries"]}
    assert "aumu/EZk2/Toon" in fps
