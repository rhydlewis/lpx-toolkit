"""Tests for diagnostics warnings (#25).

Surfaces issues a user might want to know about before opening a project:
- Unresolved 4CCs (no auval match — plugin missing on this system)
- Duplicate consecutive FX on the same strip (often unintentional)
- Truncated names (the 11-char binary truncation our parser observes)
"""
from lpx_inspect import (
    AURef,
    Track,
    diagnose_project,
)


def _au(fingerprint: str = "aumu/EZk2/Toon",
        display_name: str = "EZkeys 2") -> AURef:
    typ, sub, mfr = fingerprint.split("/")
    return AURef(
        display_name=display_name,
        type_code=typ,
        subtype=sub,
        manufacturer=mfr,
        offset=0,
    )


def _track(
    name: str = "Inst 1",
    instrument: AURef | None = None,
    midi_fx: list[AURef] | None = None,
    audio_fx: list[AURef] | None = None,
) -> Track:
    return Track(
        name=name,
        offset=0,
        descriptor=b"\x29\xf5\xf7\xcf\x08\x02\x00\x00",
        instrument=instrument,
        midi_fx=midi_fx or [],
        audio_fx=audio_fx or [],
    )


def test_diagnose_returns_empty_for_clean_project():
    tracks = [_track(instrument=_au("aumu/EZk2/Toon"))]
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    warnings = diagnose_project(tracks, lookup)
    assert warnings == []


def test_diagnose_flags_unresolved_4cc():
    """Plugin appears in the project but isn't installed on this system."""
    tracks = [_track(instrument=_au("aumu/Xyz1/UNKN"))]
    lookup = {}  # auval lookup empty — nothing resolves
    warnings = diagnose_project(tracks, lookup)
    assert any(w["kind"] == "unresolved_plugin" for w in warnings)
    unresolved = [w for w in warnings if w["kind"] == "unresolved_plugin"][0]
    assert unresolved["fingerprint"] == "aumu/Xyz1/UNKN"


def test_diagnose_does_not_flag_resolved_plugins():
    tracks = [_track(instrument=_au("aumu/EZk2/Toon"))]
    lookup = {"aumu/EZk2/Toon": "Toontrack: EZkeys 2"}
    warnings = diagnose_project(tracks, lookup)
    assert not any(w["kind"] == "unresolved_plugin" for w in warnings)


def test_diagnose_flags_duplicate_consecutive_audio_fx():
    """Two of the same FX in a row on one strip — often a copy-paste mistake."""
    fx = _au("aufx/Comp/appl", display_name="Compressor")
    tracks = [_track(audio_fx=[fx, fx, _au("aufx/EQ  /appl", "ChannelEQ")])]
    warnings = diagnose_project(tracks, lookup={"aufx/Comp/appl": "Apple: Compressor",
                                                  "aufx/EQ  /appl": "Apple: ChannelEQ"})
    dup = [w for w in warnings if w["kind"] == "duplicate_consecutive_fx"]
    assert len(dup) == 1
    assert dup[0]["fingerprint"] == "aufx/Comp/appl"
    assert dup[0]["track"] == "Inst 1"


def test_diagnose_does_not_flag_non_consecutive_duplicates():
    """Same FX with another between is intentional (parallel processing)."""
    comp = _au("aufx/Comp/appl", "Compressor")
    eq = _au("aufx/EQ  /appl", "ChannelEQ")
    tracks = [_track(audio_fx=[comp, eq, comp])]
    warnings = diagnose_project(tracks, lookup={
        "aufx/Comp/appl": "Apple: Compressor",
        "aufx/EQ  /appl": "Apple: ChannelEQ",
    })
    assert not any(w["kind"] == "duplicate_consecutive_fx" for w in warnings)


def test_diagnose_flags_truncated_display_names():
    """Logic stores ~11-char ASCII; longer names get clipped. The presence
    of a length-11 ASCII display_name + a longer auval-resolved name is a
    truncation signal."""
    truncated = _au("aumu/GLST/TCHC", display_name="Glass Strin")
    tracks = [_track(instrument=truncated)]
    lookup = {"aumu/GLST/TCHC": "Crow Hill: Glass Strings"}
    warnings = diagnose_project(tracks, lookup)
    trunc = [w for w in warnings if w["kind"] == "truncated_name"]
    assert len(trunc) == 1
    assert trunc[0]["binary_name"] == "Glass Strin"
    assert "Glass Strings" in trunc[0]["resolved_name"]


def test_diagnose_does_not_flag_short_unrelated_names():
    """A name like 'Compressor' shouldn't be flagged as truncated — it's
    less than 11 chars and matches its resolved form."""
    short = _au("aufx/Comp/appl", display_name="Compressor")
    tracks = [_track(audio_fx=[short])]
    lookup = {"aufx/Comp/appl": "Apple: Compressor"}
    warnings = diagnose_project(tracks, lookup)
    assert not any(w["kind"] == "truncated_name" for w in warnings)
