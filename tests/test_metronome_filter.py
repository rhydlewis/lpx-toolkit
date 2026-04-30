"""Tests for the Klopfgeist (Logic metronome) filter.

CLAUDE.md notes that Logic loads Klopfgeist into every project as the
metronome. While our current AU extractor doesn't seem to surface Apple
built-ins (different storage format), this filter is defensive — if a
future project does reference Klopfgeist via the standard descriptor
pattern, the user shouldn't see it as a "real" plugin by default.
"""
from lpx_inspect import (
    AURef,
    KLOPFGEIST_FINGERPRINT,
    is_metronome_au,
    filter_metronome,
)


def _au(fingerprint: str = "aumu/klop/appl") -> AURef:
    typ, sub, mfr = fingerprint.split("/")
    return AURef(
        display_name="Klopfgeist",
        type_code=typ,
        subtype=sub,
        manufacturer=mfr,
        offset=0,
    )


def test_klopfgeist_constant_is_documented_fingerprint():
    """The constant exists and matches the documented form."""
    assert KLOPFGEIST_FINGERPRINT == "aumu/klop/appl"


def test_is_metronome_au_recognises_klopfgeist():
    assert is_metronome_au(_au("aumu/klop/appl")) is True


def test_is_metronome_au_does_not_match_user_plugins():
    assert is_metronome_au(_au("aumu/EZk2/Toon")) is False
    assert is_metronome_au(_au("aufx/EB  /SToy")) is False


def test_filter_metronome_removes_klopfgeist_from_au_list():
    """`filter_metronome` strips Klopfgeist while preserving everything
    else, in original order."""
    aus = [
        _au("aumu/EZk2/Toon"),
        _au("aumu/klop/appl"),
        _au("aufx/EB  /SToy"),
    ]
    filtered = filter_metronome(aus)
    fingerprints = [a.fingerprint for a in filtered]
    assert "aumu/klop/appl" not in fingerprints
    assert fingerprints == ["aumu/EZk2/Toon", "aufx/EB  /SToy"]


def test_filter_metronome_include_keeps_klopfgeist():
    """When `include=True`, Klopfgeist is retained."""
    aus = [_au("aumu/klop/appl"), _au("aumu/EZk2/Toon")]
    filtered = filter_metronome(aus, include=True)
    assert [a.fingerprint for a in filtered] == [
        "aumu/klop/appl", "aumu/EZk2/Toon",
    ]
