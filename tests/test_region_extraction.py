"""Tests for region-record name extraction from ProjectData.

Region records carry the user-facing track name (each region inherits it from
its parent track). Format observed empirically:

    <4-byte record id> 0x61 0xff <24 zero bytes> <uint16-LE length> <ascii name> <null padding>

These tests build minimal buffers exercising one assertion at a time.
"""
import os
from pathlib import Path

import pytest

from lpx_inspect import (
    TrackEvidence,
    cluster_regions,
    find_region_names,
    find_track_header_records,
    find_track_registry_records,
    partition_track_names,
    tracks_from_regions,
    unique_track_names,
)

# Real-project fixture lives outside the repo (.logicx files are large and
# contain user audio). Set LPX_TEST_PROJECT to a project bundle path to run
# the integration tests against a real project; otherwise they're skipped.
_REAL_PROJECT_ENV = os.environ.get("LPX_TEST_PROJECT")
_REAL_PROJECT_PATH = (
    Path(_REAL_PROJECT_ENV) / "Alternatives" / "000" / "ProjectData"
    if _REAL_PROJECT_ENV
    else None
)
_REAL_PROJECT_AVAILABLE = _REAL_PROJECT_PATH is not None and _REAL_PROJECT_PATH.exists()


def _record(name: bytes, record_id: bytes = b"\xab\xcd\xef\x12") -> bytes:
    """Build a region-record byte fragment carrying `name`."""
    return (
        record_id
        + b"\x61\xff" + b"\x00" * 24
        + len(name).to_bytes(2, "little")
        + name
        + b"\x00" * 16
    )


def test_finds_single_region_name_in_minimal_record():
    raw = b"\x00\x00\x00\x00" + _record(b"Ld GTR Low")
    assert find_region_names(raw) == ["Ld GTR Low"]


def test_finds_each_region_name_in_order():
    """Take folders contain many region records back-to-back. We want each
    one in order of appearance, including duplicates — dedup is a separate
    concern handled by the caller."""
    raw = (
        _record(b"Ld GTR Low")
        + _record(b"Ld GTR Low")
        + _record(b"Acoustic GTR")
    )
    assert find_region_names(raw) == ["Ld GTR Low", "Ld GTR Low", "Acoustic GTR"]


def test_skips_records_with_non_ascii_name_bytes():
    """The marker pattern (0x61 0xff + 24 zeros) can occur by chance inside
    plugin state or other binary blobs. When the bytes that follow aren't
    plausible name characters, skip the record rather than crash."""
    fake = (
        b"\xab\xcd\xef\x12"
        + b"\x61\xff" + b"\x00" * 24
        + b"\x05\x00"                       # claims length 5
        + b"\x80\x81\x82\x83\x84"           # non-ASCII garbage
    )
    real = _record(b"Ld GTR Low")
    assert find_region_names(fake + real) == ["Ld GTR Low"]


def test_skips_records_with_zero_length():
    """A length of zero is meaningless and almost certainly noise. Skip."""
    fake = (
        b"\xab\xcd\xef\x12"
        + b"\x61\xff" + b"\x00" * 24
        + b"\x00\x00"                       # length 0
    )
    real = _record(b"Ld GTR Low")
    assert find_region_names(fake + real) == ["Ld GTR Low"]


def test_skips_records_with_implausibly_large_length():
    """A spurious marker pulling 16-bit garbage as length can claim hundreds
    or thousands. Track names in Logic UI cap out around 32 chars; reject
    anything dramatically over that."""
    fake = (
        b"\xab\xcd\xef\x12"
        + b"\x61\xff" + b"\x00" * 24
        + b"\xff\x7f"                       # length 32767
        + b"X" * 32767
    )
    real = _record(b"Ld GTR Low")
    assert find_region_names(fake + real) == ["Ld GTR Low"]


# --- unique_track_names: condensing region names into the track-header set --


def test_unique_track_names_dedupes_repeats_preserving_first_seen_order():
    """Take folders produce the same name 50+ times. The user wants the
    track list once, in the order tracks first appear in the project."""
    raw = ["Ld GTR Low", "Ld GTR Low", "Acoustic GTR", "Ld GTR Low"]
    assert unique_track_names(raw) == ["Ld GTR Low", "Acoustic GTR"]


def test_unique_track_names_strips_take_folder_comp_suffix():
    """Comp regions inside a take folder are named '<track>: Comp X' (with
    optional trailing '.N' for additional comps). The base track name is
    everything before the ': Comp ' marker."""
    raw = [
        "Ld GTR Harm: Comp A",
        "Ld GTR Harm: Comp A.1",
        "Ld GTR Harm: Comp B",
    ]
    assert unique_track_names(raw) == ["Ld GTR Harm"]


def test_unique_track_names_strips_take_suffix():
    """Individual takes are named '<track>: Take N' or '<track> - Take N'.
    Both forms collapse to the bare track name."""
    raw = [
        "Ld GTR Low: Take 14 ",
        "Ld GTR Low - Take 14",
        "Ld GTR Low - Take 14.1",
    ]
    assert unique_track_names(raw) == ["Ld GTR Low"]


def test_unique_track_names_strips_take_number_suffix():
    """Some takes appear as '<track> #06' or '<track> #06.N'. Strip these
    too — they are not distinct tracks."""
    raw = [
        "Ld GTR Low #06",
        "Ld GTR Low #08.2",
    ]
    assert unique_track_names(raw) == ["Ld GTR Low"]


def test_unique_track_names_strips_numeric_dot_suffix():
    """Logic auto-numbers duplicate regions with '.1', '.2'. Strip when the
    suffix is purely numeric — but leave names like 'Audio 7.1' alone since
    that's the strip name, not a numeric duplicate."""
    raw = ["Slide GTR", "Slide GTR.1", "Slide GTR.2", "Slide GTR.3"]
    assert unique_track_names(raw) == ["Slide GTR"]


def test_unique_track_names_drops_bare_comp_names():
    """A region named just 'Comp A' isn't a track — it's an internal name
    inside a take folder whose parent track wasn't carried into the region
    record. Drop these from the user-facing list."""
    raw = ["Acoustic GTR", "Comp A", "Comp B", "Comp C", "Comp D"]
    assert unique_track_names(raw) == ["Acoustic GTR"]


def test_unique_track_names_drops_recording_filenames():
    """Logic stamps recordings with the project name and a timestamp-like
    counter (e.g. 'get busy living_19 #04.1'). These are file names that
    leak into the region table but aren't user-named tracks. Recognise them
    by the underscore-then-number pattern preceding any take indicator."""
    raw = [
        "Acoustic GTR",
        "get busy living_19 #04.1",
        "get busy living_3#09",
        "get busy living_1.2",
    ]
    assert unique_track_names(raw) == ["Acoustic GTR"]


# --- integration: exercise against a real ProjectData binary ---------------


@pytest.mark.skipif(not _REAL_PROJECT_AVAILABLE, reason="LPX_TEST_PROJECT not set or missing")
def test_real_project_extraction_yields_unique_named_tracks():
    """End-to-end smoke test against a real project. Properties that should
    hold for any non-trivial Logic project: extraction returns at least one
    name, no duplicates after dedupe, none are bare comp tags."""
    raw = _REAL_PROJECT_PATH.read_bytes()
    tracks = unique_track_names(find_region_names(raw))

    assert tracks, "no track names extracted from real project"
    assert len(tracks) == len(set(tracks)), "dedupe failed; duplicates present"

    forbidden = {"Comp A", "Comp B", "Comp C", "Comp D"}
    leaked = forbidden & set(tracks)
    assert not leaked, f"internal tags leaked into output: {leaked}"


# --- cluster_regions: group consecutive same-track records ---------------


def test_cluster_regions_returns_empty_for_empty_input():
    assert cluster_regions([]) == []


def test_cluster_regions_collapses_consecutive_same_name_records():
    """Each track's regions are stored contiguously in the file (verified
    empirically). Consecutive records sharing a base name represent a
    single track."""
    records = [(100, "Acoustic GTR"), (200, "Acoustic GTR"), (300, "Acoustic GTR")]
    clusters = cluster_regions(records)
    assert len(clusters) == 1
    assert clusters[0].base_name == "Acoustic GTR"
    assert clusters[0].count == 3
    assert clusters[0].first_offset == 100
    assert clusters[0].last_offset == 300


def test_cluster_regions_splits_on_name_change():
    records = [
        (100, "Dialogue"),
        (200, "Dialogue"),
        (300, "Audio 3"),
        (400, "Audio 3"),
    ]
    clusters = cluster_regions(records)
    assert [c.base_name for c in clusters] == ["Dialogue", "Audio 3"]
    assert [c.count for c in clusters] == [2, 2]


def test_cluster_regions_strips_take_comp_suffixes_within_cluster():
    """A cluster of 'Audio 3', 'Audio 3: Comp A', 'Audio 3.1' is one track —
    the suffixes are take/comp variations, not separate tracks."""
    records = [
        (100, "Audio 3"),
        (200, "Audio 3: Comp A"),
        (300, "Audio 3.1"),
    ]
    clusters = cluster_regions(records)
    assert len(clusters) == 1
    assert clusters[0].base_name == "Audio 3"
    assert clusters[0].count == 3


def test_cluster_regions_excludes_recording_filenames():
    """Recording filenames like 'get busy living_3#09' don't contribute to
    track clusters."""
    records = [
        (100, "Acoustic GTR"),
        (200, "get busy living_3#09"),
        (300, "Acoustic GTR"),
    ]
    clusters = cluster_regions(records)
    assert len(clusters) == 1
    assert clusters[0].base_name == "Acoustic GTR"
    assert clusters[0].count == 2


def test_cluster_regions_excludes_bare_comp_names():
    records = [
        (100, "Acoustic GTR"),
        (200, "Comp A"),
        (300, "Acoustic GTR"),
    ]
    clusters = cluster_regions(records)
    assert len(clusters) == 1
    assert clusters[0].base_name == "Acoustic GTR"
    assert clusters[0].count == 2


# --- tracks_from_regions: collapse clusters into unique tracks -------------


def test_tracks_from_regions_dedupes_interleaved_clusters():
    """Regions on different tracks are often interleaved. The unique track
    list keeps each base name once, in first-appearance order, with the
    total region count summed across all its clusters."""
    records = [
        (100, "Ld GTR Low"),
        (200, "Middle Lead GTR"),
        (300, "Ld GTR Low"),
        (400, "Middle Lead GTR"),
        (500, "Ld GTR Low"),
    ]
    tracks = tracks_from_regions(records)
    assert [(t.base_name, t.count) for t in tracks] == [
        ("Ld GTR Low", 3),
        ("Middle Lead GTR", 2),
    ]
    # First-appearance order preserved (Ld GTR Low at 100, Middle at 200)
    assert tracks[0].first_offset == 100
    assert tracks[1].first_offset == 200


def test_tracks_from_regions_returns_empty_for_empty_input():
    assert tracks_from_regions([]) == []


# --- find_track_header_records: 0x70 0x03 0x01 0x00 marker ----------------


def _track_header(name: bytes, idx_byte: bytes = b"\x25") -> bytes:
    """Synthesise a track-header record fragment.

    Format observed: 0x70 0x03 0x01 0x00 + 4 bytes + 1 index byte + 7 zeros
    + uint16-LE length + ASCII name + null terminator.
    """
    return (
        b"\x70\x03\x01\x00"
        + b"\x00\x00\x00\x00"
        + idx_byte
        + b"\x00" * 7
        + len(name).to_bytes(2, "little")
        + name
        + b"\x00"
    )


def test_find_track_header_records_extracts_minimal_record():
    raw = b"\x00" * 16 + _track_header(b"Lead Strings")
    records = find_track_header_records(raw)
    assert [ev.name for ev in records] == ["Lead Strings"]


def test_find_track_header_records_skips_logic_internal_noise():
    """Logic emits its own internal records under the same signature —
    automation containers, RBA take-folder sequences, the 'Untitled'
    placeholder. None of these are user-named tracks; filter them."""
    raw = (
        _track_header(b"*Automation")
        + _track_header(b"Pad")
        + _track_header(b"RBA Sequence")
        + _track_header(b"Untitled")
        + _track_header(b"Bells")
        + _track_header(b"Track Alternatives")
        + _track_header(b"MIDI Region")
    )
    records = find_track_header_records(raw)
    assert [ev.name for ev in records] == ["Pad", "Bells"]


def test_find_track_header_records_returns_offsets_in_file_order():
    raw = b"\x00" * 4 + _track_header(b"Pad") + b"\x00" * 32 + _track_header(b"Bells")
    records = find_track_header_records(raw)
    assert len(records) == 2
    assert records[0][1] == "Pad"
    assert records[1][1] == "Bells"
    assert records[0][0] < records[1][0]


# --- find_track_registry_records: signature-whitelisted track entries ----


def _registry_entry(name: bytes, sig: bytes = b"\x22\x12") -> bytes:
    """Synthesise a generalised track-registry record fragment.

    Format observed: 4 zero bytes + 2-byte signature + 4 zero bytes
    + 2 control bytes + 2 zero bytes + uint16-LE length + ASCII name.
    """
    return (
        b"\x00" * 4
        + sig
        + b"\x00" * 4
        + b"\x80\x00"
        + b"\x00" * 2
        + len(name).to_bytes(2, "little")
        + name
    )


def test_find_track_registry_records_extracts_with_track_signature():
    """Signature 22 12 marks MIDI/instrument tracks (Pad, Piano, Bells...)."""
    raw = b"\x00" * 16 + _registry_entry(b"Lead Strings", sig=b"\x22\x12")
    records = find_track_registry_records(raw)
    assert [ev.name for ev in records] == ["Lead Strings"]


def test_find_track_registry_records_skips_bus_signatures():
    """Signature 24 12 marks audio buses (Vocal Verb, EGTR Verb, etc.) —
    don't surface those as user tracks."""
    raw = (
        _registry_entry(b"Lead Strings", sig=b"\x22\x12")
        + _registry_entry(b"Vocal Verb",   sig=b"\x24\x12")
        + _registry_entry(b"Bass",         sig=b"\x22\x12")
    )
    names = [ev.name for ev in find_track_registry_records(raw)]
    assert names == ["Lead Strings", "Bass"]


def test_find_track_registry_records_filters_at_context_name_placeholder():
    """'@ (=Context Name)' is a Logic UI placeholder under several signatures
    — never a user track."""
    raw = (
        _registry_entry(b"Pad")
        + _registry_entry(b"@ (=Context Name)")
        + _registry_entry(b"Bells")
    )
    names = [ev.name for ev in find_track_registry_records(raw)]
    assert names == ["Pad", "Bells"]


def test_find_track_registry_records_recognises_sub_track_signatures():
    """Sub/folder headers (Dialogue, Keys, Strings & Pads, Percussion) use
    distinct signatures (74 10, cb 10, e3 11, e4 10, eb 11)."""
    raw = (
        _registry_entry(b"Dialogue",            sig=b"\xcb\x10")
        + _registry_entry(b"Keys",              sig=b"\xe3\x11")
        + _registry_entry(b"Bells & Synth Keys", sig=b"\xe4\x10")
        + _registry_entry(b"Percussion",        sig=b"\x74\x10")
        + _registry_entry(b"Strings & Pads",    sig=b"\xeb\x11")
    )
    names = [ev.name for ev in find_track_registry_records(raw)]
    assert set(names) == {
        "Dialogue", "Keys", "Bells & Synth Keys", "Percussion", "Strings & Pads",
    }


# --- TrackEvidence + kind propagation --------------------------------------


def test_find_track_registry_records_returns_midi_kind_for_22_12_signature():
    raw = b"\x00" * 16 + _registry_entry(b"Pad", sig=b"\x22\x12")
    [evidence] = find_track_registry_records(raw)
    assert evidence.kind == "midi"


def test_find_track_registry_records_returns_audio_kind_for_df_11_signature():
    raw = b"\x00" * 16 + _registry_entry(b"Slide GTR", sig=b"\xdf\x11")
    [evidence] = find_track_registry_records(raw)
    assert evidence.kind == "audio"


def test_find_track_registry_records_returns_folder_kind_for_sub_signatures():
    raw = b"\x00" * 16 + _registry_entry(b"Keys", sig=b"\xe3\x11")
    [evidence] = find_track_registry_records(raw)
    assert evidence.kind == "folder"


def test_find_track_header_records_tags_kind_as_midi():
    """Track-header records (\\x70\\x03\\x01\\x00 signature) come from MIDI/
    instrument tracks in the project registry."""
    raw = b"\x00" * 16 + _track_header(b"Lead Strings")
    [evidence] = find_track_header_records(raw)
    assert evidence.kind == "midi"
    assert evidence.name == "Lead Strings"


def test_evidence_is_unpackable_as_offset_name_kind_triple():
    """TrackEvidence keeps tuple semantics so callers can unpack it."""
    raw = b"\x00" * 16 + _registry_entry(b"Pad", sig=b"\x22\x12")
    evidence = find_track_registry_records(raw)[0]
    offset, name, kind = evidence
    assert (name, kind) == ("Pad", "midi")
    assert isinstance(offset, int)


def test_tracks_from_regions_propagates_kind_into_cluster():
    """When evidence carries a kind, the resulting RegionCluster reflects it."""
    records = [
        TrackEvidence(100, "Lead Strings", "midi"),
        TrackEvidence(200, "Lead Strings", "midi"),
        TrackEvidence(300, "Slide GTR", "audio"),
    ]
    tracks = tracks_from_regions(records)
    by_name = {t.base_name: t for t in tracks}
    assert by_name["Lead Strings"].kind == "midi"
    assert by_name["Slide GTR"].kind == "audio"


def test_tracks_from_regions_accepts_legacy_tuple_records():
    """Legacy 2-tuple records still work — kind defaults to 'unknown' so
    older callers don't break."""
    records = [(100, "Acoustic GTR"), (200, "Acoustic GTR")]
    tracks = tracks_from_regions(records)
    assert tracks[0].kind == "unknown"


def test_tracks_from_regions_promotes_audio_or_midi_over_folder():
    """When the same name shows up under both a folder signature (e.g.
    Timpani inherits 0x74 0x10 because it lives in the Percussion sub)
    AND a midi-inferring source (track-header record), the region/header
    evidence wins — Timpani is a midi track, not a folder."""
    records = [
        TrackEvidence(100, "Timpani", "folder"),
        TrackEvidence(200, "Timpani", "midi"),
    ]
    [track] = tracks_from_regions(records)
    assert track.kind == "midi"


def test_tracks_from_regions_keeps_folder_when_no_other_evidence():
    """A registry-only track with a folder signature stays a folder."""
    records = [TrackEvidence(100, "Percussion", "folder")]
    [track] = tracks_from_regions(records)
    assert track.kind == "folder"


@pytest.mark.skipif(not _REAL_PROJECT_AVAILABLE, reason="LPX_TEST_PROJECT not set or missing")
def test_real_project_partition_keeps_lists_disjoint():
    """auto_named and user_renamed must not overlap."""
    raw = _REAL_PROJECT_PATH.read_bytes()
    tracks = unique_track_names(find_region_names(raw))
    auto, user = partition_track_names(tracks)

    overlap = set(auto) & set(user)
    assert not overlap, f"partition produced overlap: {overlap}"
    assert sorted(auto + user) == sorted(tracks), "partition lost or invented names"


# --- partition_track_names: split into auto-named vs user-renamed ----------


def test_partition_track_names_separates_auto_from_user_named():
    """Regions inherit the channel-strip name when the user hasn't renamed
    the track. Names matching default patterns ('Audio N', 'Inst N') are
    auto-named; everything else is treated as user-renamed."""
    names = ["Audio 1", "Inst 3", "Acoustic GTR", "Ld GTR Low", "Audio 25"]
    auto, user = partition_track_names(names)
    assert auto == ["Audio 1", "Inst 3", "Audio 25"]
    assert user == ["Acoustic GTR", "Ld GTR Low"]


def test_partition_track_names_treats_bus_aux_master_as_auto():
    """Bus / Aux / Master / Output / Input strips have default-named regions
    we don't want to surface as user tracks."""
    names = ["Bus 1", "Aux 5", "Master", "Output 1-2", "Input 1", "My Synth"]
    auto, user = partition_track_names(names)
    assert auto == ["Bus 1", "Aux 5", "Master", "Output 1-2", "Input 1"]
    assert user == ["My Synth"]
