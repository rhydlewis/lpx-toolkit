"""Tests for phantom plugin distinction (#22).

Phantom plugins are AU references that exist in `ProjectData` but aren't
attached to any active user track. Common sources: undo history, deleted
tracks, alternative takes. CLAUDE.md flags them as 'real entries that
aren't currently on any track' — the inspector mockup surfaces them as a
dedicated section.
"""
from lpx_inspect import (
    AURef,
    Track,
    find_phantom_aus,
)


def _au(fingerprint: str, offset: int = 0, display_name: str = "") -> AURef:
    typ, sub, mfr = fingerprint.split("/")
    return AURef(
        display_name=display_name or "plugin",
        type_code=typ,
        subtype=sub,
        manufacturer=mfr,
        offset=offset,
    )


def _track(
    name: str = "Inst 1",
    instrument: AURef | None = None,
    midi_fx: list[AURef] | None = None,
    audio_fx: list[AURef] | None = None,
    is_active: bool = True,
) -> Track:
    # descriptor with bit 0x04 of byte 2 set when active
    desc = (b"\x29\xf5\xf7\xcf\x08\x02\x00\x00" if is_active
            else b"\x29\xf5\xf3\xcf\x00\x00\x00\x00")
    return Track(
        name=name, offset=0, descriptor=desc,
        instrument=instrument,
        midi_fx=midi_fx or [],
        audio_fx=audio_fx or [],
    )


def test_returns_empty_when_all_aus_attached_to_active_tracks():
    """No phantoms when every AU appears on an active track."""
    a = _au("aumu/EZk2/Toon")
    tracks = [_track(instrument=a)]
    phantoms = find_phantom_aus(all_aus=[a], tracks=tracks)
    assert phantoms == []


def test_returns_unattached_aus_as_phantoms():
    """AUs not on any track are phantoms."""
    on_track = _au("aumu/EZk2/Toon")
    orphan = _au("aumu/Kat1/Artu")  # Not assigned to anything
    tracks = [_track(instrument=on_track)]
    phantoms = find_phantom_aus(all_aus=[on_track, orphan], tracks=tracks)
    assert len(phantoms) == 1
    assert phantoms[0].fingerprint == "aumu/Kat1/Artu"


def test_aus_on_inactive_tracks_count_as_phantoms():
    """An AU attached only to an inactive (empty) channel strip is a phantom
    from the user's perspective — Logic kept the reference in undo / deleted
    history but the track has no plugin slot anymore."""
    on_inactive = _au("aufx/Comp/appl")
    tracks = [_track(audio_fx=[on_inactive], is_active=False)]
    phantoms = find_phantom_aus(all_aus=[on_inactive], tracks=tracks)
    assert len(phantoms) == 1
    assert phantoms[0].fingerprint == "aufx/Comp/appl"


def test_dedupes_phantoms_by_fingerprint():
    """Same plugin appearing as phantom in multiple places counts once."""
    p1 = _au("aumu/Kat1/Artu", offset=100)
    p2 = _au("aumu/Kat1/Artu", offset=200)
    phantoms = find_phantom_aus(all_aus=[p1, p2], tracks=[])
    assert len(phantoms) == 1


def test_excludes_metronome_by_default():
    """Klopfgeist gets filtered as 'always present' even when phantom."""
    klopf = _au("aumu/klop/appl")
    phantoms = find_phantom_aus(all_aus=[klopf], tracks=[])
    assert phantoms == []


def test_includes_metronome_when_requested():
    klopf = _au("aumu/klop/appl")
    phantoms = find_phantom_aus(all_aus=[klopf], tracks=[], include_metronome=True)
    assert len(phantoms) == 1
