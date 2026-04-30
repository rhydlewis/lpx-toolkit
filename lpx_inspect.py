#!/usr/bin/env python3
"""Logic Pro project inspector — extracts metadata, tracks, and AU plugins."""
import json
import plistlib
import re
import struct
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

# 4CC types stored little-endian in ProjectData.
AU_TYPES = {
    b"umua": "instrument",      # aumu — Music Device
    b"fmua": "midi_effect",     # aumf — Music Effect (MIDI FX)
    b"xfua": "audio_effect",    # aufx — Audio Effect
}

# Track-name field heuristic: a 16-byte field opening with 0x20 (a leading
# space that's part of the format), then the ASCII name, null-padded.
# A 4-byte channel type-code follows; its final byte has the top two bits
# set (0xC5 / 0xCD / 0xCF / 0xED observed across audio / bus / inst / output).
TRACK_NAME_RE = re.compile(rb"(?<=\x00)\x20([\x21-\x7e][\x20-\x7e]{0,14})")
NAME_FIELD_LEN = 16
DESCRIPTOR_LEN = 8  # 4-byte type code + 1-byte activity flag + 3 reserved


@dataclass
class AURef:
    display_name: str
    type_code: str       # e.g. 'aumu'
    subtype: str         # e.g. 'EZk2'
    manufacturer: str    # e.g. 'Toon'
    offset: int

    @property
    def fingerprint(self) -> str:
        return f"{self.type_code}/{self.subtype}/{self.manufacturer}"

    @property
    def kind(self) -> str:
        return AU_TYPES[bytes(reversed(self.type_code.encode()))]


@dataclass
class Track:
    name: str
    offset: int
    descriptor: bytes  # 8 bytes: type-code (4) + activity flag (1) + reserved
    instrument: AURef | None = None
    midi_fx: list[AURef] = field(default_factory=list)
    audio_fx: list[AURef] = field(default_factory=list)

    @property
    def kind(self) -> str:
        head, b1, b2, _ = self.descriptor[:4]
        if head == 0x89:
            return "master"
        if head == 0x49:
            return "output"
        if head == 0xE9:
            return "bus"
        if head == 0xAB:
            return "aux" if b1 == 0xF5 else "audio"
        if head == 0x29:
            return "instrument" if b2 in (0xF3, 0xF7) else "input"
        return "unknown"

    @property
    def is_user_track(self) -> bool:
        return self.kind in ("audio", "instrument")

    @property
    def is_active(self) -> bool:
        # Two independent activity signals (either is sufficient):
        #   * bit 0x04 of descriptor[2] — set when a plugin is loaded
        #   * descriptor[4] non-zero    — set when the strip is otherwise
        #                                  customised (sends, routing, etc.)
        return bool(self.descriptor[2] & 0x04) or self.descriptor[4] != 0

    def display_name(self, lookup: dict[str, str]) -> str:
        """Track header name as Logic would show it.

        Software-instrument tracks default to the loaded instrument's name.
        Audio tracks default to the channel-strip name. User-renamed track
        names live inside NSKeyedArchive blobs not parsed here.
        """
        if self.kind == "instrument" and self.instrument:
            full = lookup.get(self.instrument.fingerprint, "")
            if full:
                # Auval format is "Manufacturer: Plugin Name" — drop prefix.
                return full.split(": ", 1)[-1]
            return self.instrument.display_name
        return self.name


@dataclass
class ProjectInfo:
    name: str
    key: str
    gender: str
    bpm: float
    sig_numerator: int
    sig_denominator: int
    track_count: int
    tracks: list[Track]
    created_at: datetime
    modified_at: datetime


def reverse_4cc(b: bytes) -> str:
    return b[::-1].decode("ascii", errors="replace")


def extract_name(raw: bytes, marker_offset: int, lookback: int = 200) -> str:
    """Find the closest *meaningful* ASCII run preceding the AU marker.
    Skips 4-char-or-less runs (likely 4CCs) and archive noise."""
    chunk = raw[max(0, marker_offset - lookback):marker_offset]
    runs = re.findall(rb"[\x20-\x7e]{4,}", chunk)

    NOISE = ("$class", "NS.", "bplist", "WNS.")
    candidates = []
    for run in runs:
        decoded = run.decode("ascii")
        if len(decoded) <= 4:
            continue
        if any(n in decoded for n in NOISE):
            continue
        cleaned = re.sub(r"<[^>]+>", "", decoded).strip()
        if len(cleaned) >= 4:
            candidates.append(cleaned)

    return candidates[-1] if candidates else "<unknown>"


# Suffixes Logic appends to region names that should be stripped to recover
# the underlying track name. Order matters — strip take/comp wrappers first,
# then trailing numeric duplicate suffixes.
_TRACK_NAME_SUFFIX_RES = (
    re.compile(r":\s*Comp\s+\S+(?:\.\d+)?\s*$"),     # ": Comp A", ": Comp A.1"
    re.compile(r":\s*Take\s+\d+\s*(?:\.\d+)?\s*$"),  # ": Take 14", ": Take 14.1"
    re.compile(r"\s*-\s*Take\s+\d+(?:\.\d+)?\s*$"),  # " - Take 14"
    re.compile(r"\s*#\d+(?:\.\d+)?\s*$"),            # " #06", " #08.2"
    re.compile(r"\.\d+$"),                            # ".1", ".2" (numeric dup)
)

# Names that are clearly internal/structural rather than user-given tracks.
_BARE_COMP_RE = re.compile(r"^Comp\s+[A-Z]$")
# Recording filenames look like "<project name>_<digits>[ #...]" — the trailing
# number is a counter or take id Logic appends when bouncing/recording.
_RECORDING_FILENAME_RE = re.compile(r"_\d+\s*(?:#\d+)?$")


def _strip_region_suffixes(name: str) -> str:
    """Iteratively peel known suffixes until the name stops shrinking."""
    while True:
        before = name
        for pattern in _TRACK_NAME_SUFFIX_RES:
            name = pattern.sub("", name)
        name = name.rstrip()
        if name == before:
            return name


def _is_user_track_name(name: str) -> bool:
    if not name:
        return False
    if _BARE_COMP_RE.match(name):
        return False
    if _RECORDING_FILENAME_RE.search(name):
        return False
    return True


# Channel-strip default names regions inherit when the user hasn't renamed
# the track. Anything matching one of these is treated as auto-named.
_AUTO_TRACK_NAME_RE = re.compile(
    r"^(Audio|Inst|Bus|Aux|Output|Input|Master)(\s+\d+(-\d+)?)?$"
)


@dataclass
class RegionCluster:
    """A run of consecutive records sharing one base name — i.e. one
    user-perceived track. `base_name` is the cleaned form; `count` is how
    many records contributed; `first/last_offset` bracket the byte range;
    `kind` is the inferred track type ('audio'/'midi'/'folder'/'unknown')
    derived from the strongest evidence available; `track_id` is Logic's
    per-track uint16 (0 if unknown); `strip_id` is the channel-strip
    number for audio tracks (0 elsewhere)."""
    base_name: str
    count: int
    first_offset: int
    last_offset: int
    kind: str = "unknown"
    track_id: int = 0
    strip_id: int = 0


def _unpack_record(record) -> tuple[int, str, str]:
    """Accept TrackEvidence, (offset, name, kind), or legacy (offset, name)."""
    if len(record) >= 3:
        return record[0], record[1], record[2]
    return record[0], record[1], "unknown"


def _stronger_kind(existing: str, candidate: str) -> str:
    """Pick the more authoritative kind from two evidence records.

    Some signatures are shared between sub/folder tracks and the MIDI
    instrument tracks inside them (e.g. the 0x74 0x10 signature carries
    both `Percussion` and the `Timpani` track underneath it). When both
    'folder' and 'audio'/'midi' evidence is present for one name, the
    region/header source is more specific — prefer it over 'folder'.
    """
    if candidate == "unknown":
        return existing
    if existing == "unknown":
        return candidate
    if existing == "folder" and candidate in ("audio", "midi"):
        return candidate
    return existing


def tracks_from_evidence(
    registry_records: list,
    header_records: list,
    region_records: list,
) -> list[RegionCluster]:
    """Build the canonical track list from multi-source evidence.

    Each whitelisted *registry* record is exactly one Logic track —
    verified empirically (Andy & Red ×2, Reversed Intro GTR ×2,
    Synth Lead ×2 all show 2 registry records). Region (gRuA) counts
    attach to matching registry entries by name. Tracks visible only via
    region evidence (no registry entry) are appended as audio extras.

    Header records (the `\\x70\\x03\\x01\\x00` source) refine the kind
    when registry signature is ambiguous — e.g. Timpani's registry
    signature 0x74 0x10 is the Percussion folder colour group, but the
    track-header records correctly identify it as MIDI.
    """
    # Aggregate kind hints from headers and regions (by name)
    name_kind_hints: dict[str, str] = {}
    for record in list(header_records) + list(region_records):
        _, raw_name, kind = _unpack_record(record)
        cleaned = _strip_region_suffixes(raw_name)
        if not _is_user_track_name(cleaned):
            continue
        name_kind_hints[cleaned] = _stronger_kind(
            name_kind_hints.get(cleaned, "unknown"), kind
        )

    # Per-name region counts and first-appearance offsets
    region_counts: Counter = Counter()
    region_first_offset: dict[str, int] = {}
    for record in region_records:
        offset, raw_name, _ = _unpack_record(record)
        cleaned = _strip_region_suffixes(raw_name)
        if not _is_user_track_name(cleaned):
            continue
        region_counts[cleaned] += 1
        region_first_offset.setdefault(cleaned, offset)

    out: list[RegionCluster] = []
    used_names: set[str] = set()

    # One entry per registry record — preserves duplicate names (= different tracks).
    for record in registry_records:
        # Registry records carry track_id/strip_id; legacy 2/3-tuple records don't.
        if isinstance(record, TrackEvidence):
            offset = record.offset
            raw_name = record.name
            kind = record.kind
            track_id = record.track_id
            strip_id = record.strip_id
        else:
            offset, raw_name, kind = _unpack_record(record)
            track_id = 0
            strip_id = 0
        cleaned = _strip_region_suffixes(raw_name)
        if not _is_user_track_name(cleaned):
            continue
        # Refine kind with header/region evidence (folder→midi/audio when
        # header or region source disagrees with the registry signature)
        kind = _stronger_kind(kind, name_kind_hints.get(cleaned, "unknown"))
        # Region count attributed to the FIRST registry entry for this name;
        # later entries with the same name get 0 (we can't split regions
        # without a track-ID — best-effort, biases to first).
        regions_attributed = (
            region_counts.get(cleaned, 0) if cleaned not in used_names else 0
        )
        used_names.add(cleaned)
        first_off = region_first_offset.get(cleaned, offset)
        out.append(RegionCluster(
            base_name=cleaned,
            count=regions_attributed,
            first_offset=first_off,
            last_offset=offset,
            kind=kind,
            track_id=track_id,
            strip_id=strip_id,
        ))

    # Region-only entries — names with gRuA evidence but no registry record
    for name, count in region_counts.items():
        if name in used_names:
            continue
        offset = region_first_offset[name]
        out.append(RegionCluster(
            base_name=name,
            count=count,
            first_offset=offset,
            last_offset=offset,
            kind="audio",
        ))

    return out


def tracks_from_regions(records) -> list[RegionCluster]:
    """Collapse region records into unique tracks, in first-appearance order.

    Tracks' regions interleave in `ProjectData` once a project gets
    edit-heavy. Deduping by base name keeps each track once and sums all
    its regions; first-appearance order is a usable proxy for arrangement
    order without parsing the (still-unidentified) track-list metadata.
    """
    by_name: dict[str, RegionCluster] = {}
    for record in records:
        offset, raw_name, kind = _unpack_record(record)
        cleaned = _strip_region_suffixes(raw_name)
        if not _is_user_track_name(cleaned):
            continue
        existing = by_name.get(cleaned)
        if existing is None:
            by_name[cleaned] = RegionCluster(
                base_name=cleaned,
                count=1,
                first_offset=offset,
                last_offset=offset,
                kind=kind,
            )
        else:
            existing.count += 1
            existing.last_offset = offset
            existing.kind = _stronger_kind(existing.kind, kind)
    return list(by_name.values())


def cluster_regions(records) -> list[RegionCluster]:
    """Group consecutive records (in offset order) by their base name.

    Each track's regions are stored contiguously in ProjectData, so a run of
    consecutive records sharing one base name (after take/comp suffix
    stripping) corresponds to a single user-perceived track. Records that
    are recording filenames or bare comp tags are excluded; they don't open
    a new cluster but also don't break the surrounding one.
    """
    clusters: list[RegionCluster] = []
    current: RegionCluster | None = None
    for record in records:
        offset, raw_name, kind = _unpack_record(record)
        cleaned = _strip_region_suffixes(raw_name)
        if not _is_user_track_name(cleaned):
            continue
        if current is not None and current.base_name == cleaned:
            current.count += 1
            current.last_offset = offset
            current.kind = _stronger_kind(current.kind, kind)
        else:
            current = RegionCluster(
                base_name=cleaned,
                count=1,
                first_offset=offset,
                last_offset=offset,
                kind=kind,
            )
            clusters.append(current)
    return clusters


def partition_track_names(names: list[str]) -> tuple[list[str], list[str]]:
    """Split track names into (auto_named, user_renamed).

    Auto-named entries match Logic's default channel-strip naming pattern
    ('Audio 3', 'Inst 12', 'Bus 7', 'Master', 'Output 1-2', etc.). Everything
    else is something the user typed.
    """
    auto: list[str] = []
    user: list[str] = []
    for n in names:
        (auto if _AUTO_TRACK_NAME_RE.match(n) else user).append(n)
    return auto, user


def unique_track_names(names: list[str]) -> list[str]:
    """Reduce a stream of region names to the user-facing track names."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        cleaned = _strip_region_suffixes(n)
        if not _is_user_track_name(cleaned) or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


REGION_MARKER_RE = re.compile(rb"\x61\xff" + b"\x00" * 24)
# Logic UI track names are short — generous upper bound rejects 16-bit garbage
# pulled from random binary noise that happens to follow the marker pattern.
REGION_NAME_MAX_LEN = 200


# Track-header records carry one entry per track (canonical name, MIDI
# tracks included). They share a 4-byte signature followed by 4 bytes,
# 1 varying byte (likely a track index), 7 zeros, the uint16-LE name length
# and the ASCII name terminated by a null. Logic emits its own internal
# records under the same signature — they're filtered by name below.
TRACK_HEADER_RE = re.compile(
    rb"\x70\x03\x01\x00[\s\S]{4}[\s\S]\x00\x00\x00\x00\x00\x00\x00([\s\S]\x00)"
)
TRACK_HEADER_NOISE = frozenset({
    "*Automation",
    "RBA Sequence",
    "Untitled",
    "Track Alternatives",
    "Track Automation Root Folder",
    "MIDI Region",
    "TRASH",
    # The project file name itself appears in the registry — not a track.
    # The actual project name passed in is filtered against this at the
    # output layer (we don't know it here without a path).
})


class TrackEvidence(NamedTuple):
    """Single evidence record for a track: where in the file we saw it,
    what name it carried, and which extractor (with what kind hint) found
    it. Kind is one of 'audio', 'midi', 'folder', 'unknown'.

    `track_id` is a per-track uint16 LE Logic stores immediately before
    each registry record (32-byte preamble, bytes 2-3). Stable across the
    project and unique per track — useful as a key for cross-referencing
    from other records. 0 when the source doesn't carry one (e.g. region
    or header records).

    `strip_id` is the channel-strip number for audio tracks (uint16 LE
    that follows the name). Only meaningful when `kind == 'audio'`; 0
    elsewhere.
    """
    offset: int
    name: str
    kind: str
    track_id: int = 0
    strip_id: int = 0


# Track-registry signatures observed empirically. Each Logic track entry has
# a 16-byte preamble: 4 zeros + 2-byte signature + 4 zeros + 2 bytes + 2 zeros
# + 2-byte LE length + ASCII name. Different track *kinds* use different
# signatures; buses and presets share the same outer structure but with
# different signatures, so we whitelist only the track ones.
TRACK_SIGNATURE_KIND: dict[bytes, str] = {
    b"\x22\x12": "midi",     # MIDI / instrument tracks
    b"\xa8\x11": "midi",     # single-instrument tracks (Dome Kick)
    b"\x23\x12": "audio",    # audio tracks (some)
    b"\xdc\x11": "audio",    # audio tracks (some)
    b"\xdf\x11": "audio",    # audio tracks (Slide GTR / Intro Lead GTR)
    b"\x74\x10": "folder",   # sub / percussion
    b"\xcb\x10": "folder",   # sub / dialogue
    b"\xe3\x11": "folder",   # sub / keys
    b"\xe4\x10": "folder",   # sub / bells & synth keys
    b"\xeb\x11": "folder",   # sub / strings & pads
    b"\xe7\x11": "folder",   # atmosphere / pad-cluster
}
TRACK_SIGNATURES = frozenset(TRACK_SIGNATURE_KIND.keys())

TRACK_REGISTRY_RE = re.compile(
    rb"\x00\x00\x00\x00([\s\S]{2})\x00\x00\x00\x00[\s\S]{2}\x00\x00([\s\S]\x00)"
)
# Names that show up under track signatures but are Logic-internal placeholders
# or system buses, not user-named tracks.
TRACK_REGISTRY_NOISE = frozenset({
    "@ (=Context Name)",
    "(Folder)",
    "Not Assigned",
    "Transform Parameter Set",
    "Untitled",
    "Unused",
    "Click",
    "MIDI Click",
    "Master",
    "Stereo Out",
    "Preview",
    "VCA 1",
})


def _is_summing_stack_trailer(trailer: bytes) -> bool:
    """Summing Stacks (Sub N folders) carry the trailer pattern
    `XX 01 00 NN 00 01` immediately after the name, where XX varies (looks
    like 0x54 + sub_number) and NN is the Sub number. Other folder kinds
    (Aux Stack, child tracks inside an Aux Stack) have `XX 00 00 ff 00 01`
    or similar — second byte is 0x00 not 0x01.

    Some records (e.g. Guitars) emit a trailing null after the name, so
    we accept the pattern at offset 0 *or* at offset 1 (skipping one null).
    """
    for start in (0, 1):
        candidate = trailer[start:start + 6]
        if len(candidate) < 6:
            continue
        if (candidate[1] == 0x01
                and candidate[2] == 0x00
                and candidate[4] == 0x00
                and candidate[5] == 0x01):
            return True
    return False


def _decode_audio_strip_id(post_name: bytes) -> int:
    """First non-zero uint16-LE in the bytes after the name.

    Audio-track registry records encode their channel-strip number here.
    Padding can be 0 or 1 bytes depending on the name length (records
    appear to be 2-byte-aligned), so we accept either offset.
    """
    if len(post_name) >= 2:
        v = post_name[0] | (post_name[1] << 8)
        if 0 < v < 512:
            return v
    if len(post_name) >= 3:
        v = post_name[1] | (post_name[2] << 8)
        if 0 < v < 512:
            return v
    return 0


def find_track_registry_records(raw: bytes) -> list[TrackEvidence]:
    """Extract TrackEvidence records from track-registry entries.

    Each Logic track has a registry entry with a 16-byte preamble whose 2-byte
    signature identifies the track kind. We whitelist signatures that
    correspond to real user tracks (audio / instrument / sub headers), which
    excludes buses and preset entries that share the outer structure.

    Folder-signature records are further refined: a Summing Stack
    (`Sub N` strip) shows a distinct trailer pattern after the name, so we
    upgrade kind from generic `folder` to `summing-stack` when matched.

    Each record is preceded by a 32-byte 'track-link' structure carrying a
    uint16-LE per-track ID at bytes 2-3. We capture that as `track_id`.
    """
    out: list[TrackEvidence] = []
    for m in TRACK_REGISTRY_RE.finditer(raw):
        sig = bytes(m.group(1))
        kind = TRACK_SIGNATURE_KIND.get(sig)
        if kind is None:
            continue
        length_lo, length_hi = m.group(2)[0], m.group(2)[1]
        if length_hi != 0:
            continue
        if not 0 < length_lo <= REGION_NAME_MAX_LEN:
            continue
        name_off = m.end()
        nb = raw[name_off:name_off + length_lo]
        if not all(0x20 <= b < 0x7f for b in nb):
            continue
        name = nb.decode("ascii")
        if name in TRACK_REGISTRY_NOISE:
            continue
        # Trailer-pattern check upgrades 'folder' or 'audio' kind to
        # 'summing-stack' when the post-name bytes match.
        trailer = raw[name_off + length_lo:name_off + length_lo + 8]
        if _is_summing_stack_trailer(trailer):
            kind = "summing-stack"
        # track_id lives in the preceding 32-byte 'track-link' structure.
        track_id = 0
        if m.start() >= 62:
            track_id = raw[m.start() - 62] | (raw[m.start() - 61] << 8)
        # strip_id only meaningful for audio tracks
        strip_id = 0
        if kind == "audio":
            strip_id = _decode_audio_strip_id(trailer)
        out.append(TrackEvidence(
            offset=m.start(),
            name=name,
            kind=kind,
            track_id=track_id,
            strip_id=strip_id,
        ))
    return out


def find_track_header_records(raw: bytes) -> list[TrackEvidence]:
    """Extract TrackEvidence records from track-header entries.

    These are emitted once per Logic track and include MIDI/instrument
    tracks that the audio-region (`gRuA`) parser misses entirely. System
    records that share the signature (`*Automation`, take-folder
    `RBA Sequence`, `Untitled` placeholders) are filtered out — they're
    Logic-internal scaffolding, not user tracks.
    """
    out: list[TrackEvidence] = []
    for m in TRACK_HEADER_RE.finditer(raw):
        length_lo, length_hi = m.group(1)[0], m.group(1)[1]
        if length_hi != 0:
            continue
        if not 0 < length_lo <= REGION_NAME_MAX_LEN:
            continue
        name_off = m.end()
        nb = raw[name_off:name_off + length_lo]
        if not all(0x20 <= b < 0x7f for b in nb):
            continue
        if name_off + length_lo >= len(raw) or raw[name_off + length_lo] != 0:
            continue
        name = nb.decode("ascii")
        if name in TRACK_HEADER_NOISE:
            continue
        out.append(TrackEvidence(offset=m.start(), name=name, kind="midi"))
    return out


def find_region_records(raw: bytes) -> list[TrackEvidence]:
    """Extract TrackEvidence records for every valid audio region."""
    out: list[TrackEvidence] = []
    for m in REGION_MARKER_RE.finditer(raw):
        len_off = m.end()
        length = struct.unpack("<H", raw[len_off:len_off + 2])[0]
        if not 0 < length <= REGION_NAME_MAX_LEN:
            continue
        name_bytes = raw[len_off + 2:len_off + 2 + length]
        if not all(0x20 <= b < 0x7f for b in name_bytes):
            continue
        out.append(TrackEvidence(
            offset=m.start(),
            name=name_bytes.decode("ascii"),
            kind="audio",
        ))
    return out


def find_region_names(raw: bytes) -> list[str]:
    """Extract user-facing region names from ProjectData binary.

    Each region record carries: <4-byte id> 0x61 0xff <24 zeros> <uint16-LE
    length> <ascii name>. The name is the same string Logic shows in the
    track header (regions inherit it from their parent track by default).
    """
    return [ev.name for ev in find_region_records(raw)]


def find_aus(raw: bytes) -> list[AURef]:
    found = []
    for marker in AU_TYPES:
        for m in re.finditer(re.escape(marker), raw):
            off = m.start()
            if off < 4 or off + 8 > len(raw):
                continue
            mfr_le = raw[off - 4:off]
            type_le = raw[off:off + 4]
            sub_le = raw[off + 4:off + 8]

            if not all(re.fullmatch(rb"[\x20-\x7e]{4}", x)
                       for x in (mfr_le, type_le, sub_le)):
                continue

            found.append(AURef(
                display_name=extract_name(raw, off),
                type_code=reverse_4cc(type_le),
                subtype=reverse_4cc(sub_le),
                manufacturer=reverse_4cc(mfr_le),
                offset=off,
            ))
    return found


def find_tracks(raw: bytes) -> list[Track]:
    """Locate channel-strip records by their 16-byte name field + type code.

    Validates each candidate by requiring null padding inside the field and
    a plausible type-code byte (high two bits set) immediately afterwards.
    """
    tracks: list[Track] = []
    seen: set[int] = set()

    for m in TRACK_NAME_RE.finditer(raw):
        start = m.start()
        end_field = start + NAME_FIELD_LEN
        if end_field + DESCRIPTOR_LEN > len(raw):
            continue

        name_end = start + 1 + len(m.group(1))
        if raw[name_end:end_field] != b"\x00" * (end_field - name_end):
            continue

        descriptor = raw[end_field:end_field + DESCRIPTOR_LEN]
        if (descriptor[3] & 0xC0) != 0xC0:
            continue

        name = m.group(1).decode("ascii").strip()
        if not name or start in seen:
            continue
        seen.add(start)

        tracks.append(Track(name=name, offset=start, descriptor=descriptor))

    tracks.sort(key=lambda t: t.offset)
    return tracks


def assign_aus(tracks: list[Track], aus: list[AURef]) -> None:
    """Each AU descriptor belongs to the nearest preceding track marker."""
    if not tracks:
        return

    sorted_tracks = sorted(tracks, key=lambda t: t.offset)
    for au in sorted(aus, key=lambda r: r.offset):
        owner = None
        for t in sorted_tracks:
            if t.offset > au.offset:
                break
            owner = t
        if owner is None:
            continue
        if au.kind == "instrument":
            if owner.instrument is None:
                owner.instrument = au
        elif au.kind == "midi_effect":
            owner.midi_fx.append(au)
        else:
            owner.audio_fx.append(au)


# auval -l columns are: type(4) SP subtype(4) SP manufacturer(4) SP "-" SP name
def parse_auval_line(line: str) -> tuple[str, str, str, str] | None:
    if " - " not in line:
        return None
    cols, _, name = line.partition(" - ")
    if len(cols) < 14:
        return None
    typ, sub, mfr = cols[0:4], cols[5:9], cols[10:14]
    return typ, sub, mfr, name.split("(file:")[0].strip()


def auval_lookup() -> dict[str, str]:
    try:
        out = subprocess.run(["auval", "-l"], capture_output=True,
                             text=True, timeout=30).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}

    table = {}
    for line in out.splitlines():
        parsed = parse_auval_line(line)
        if parsed:
            typ, sub, mfr, label = parsed
            table[f"{typ}/{sub}/{mfr}"] = label
    return table


# ---------------------------------------------------------------------------
# Auval cache layer (#18)
#
# `auval -l` is slow (5-30s cold start) and macOS-only. We cache the parsed
# table at ~/.cache/lpx-toolkit/auval.json and invalidate when the system
# Audio Units folder mtime advances.
# ---------------------------------------------------------------------------

AUVAL_CACHE_PATH = Path.home() / ".cache" / "lpx-toolkit" / "auval.json"
COMPONENTS_DIR = Path("/Library/Audio/Plug-Ins/Components")


def get_components_mtime() -> float | None:
    """Latest mtime of the Audio Units components folder, or None if missing."""
    try:
        return COMPONENTS_DIR.stat().st_mtime
    except (FileNotFoundError, PermissionError):
        return None


def save_auval_cache(
    table: dict[str, str],
    components_mtime: float | None,
    path: Path = AUVAL_CACHE_PATH,
) -> None:
    """Write the parsed auval table + components mtime to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "components_mtime": components_mtime,
        "table": table,
    }))


def load_auval_cache(path: Path = AUVAL_CACHE_PATH) -> tuple[dict[str, str], float | None]:
    """Return (table, components_mtime). Empty + None when missing or corrupt."""
    if not path.exists():
        return {}, None
    try:
        payload = json.loads(path.read_text())
        return payload.get("table", {}), payload.get("components_mtime")
    except (json.JSONDecodeError, OSError):
        return {}, None


def auval_lookup_cached(path: Path = AUVAL_CACHE_PATH) -> dict[str, str]:
    """Return the auval table, using a disk cache invalidated by mtime.

    Cold start (no cache): run auval, save the result, return it.
    Warm path (cache exists, components mtime unchanged): return cache.
    Stale (components mtime advanced): re-run auval, refresh cache.
    auval missing or broken: return empty dict; don't write cache.
    """
    cached_table, cached_mtime = load_auval_cache(path=path)
    current_mtime = get_components_mtime()

    if cached_table and cached_mtime == current_mtime:
        return cached_table

    fresh = auval_lookup()
    if fresh:
        save_auval_cache(fresh, current_mtime, path=path)
    return fresh


def deduplicate(refs: list[AURef]) -> list[AURef]:
    """Keep distinct (offset, fingerprint) refs — same plugin at different
    offsets is genuinely separate (different track or undo-history entry)."""
    seen: set[tuple[int, str]] = set()
    out: list[AURef] = []
    for r in refs:
        k = (r.offset, r.fingerprint)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _bundle_dates(path: Path) -> tuple[datetime, datetime]:
    """Return (created, modified) datetimes for a .logicx bundle.

    macOS exposes a creation timestamp via `st_birthtime`; on filesystems
    without one, fall back to mtime so creation never reports as later than
    modification.
    """
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime)
    created = datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_mtime))
    return created, modified


def parse_project(logicx_path: Path) -> ProjectInfo:
    alt = next(logicx_path.glob("Alternatives/*"))
    md = plistlib.load(open(alt / "MetaData.plist", "rb"))
    raw = (alt / "ProjectData").read_bytes()

    aus = deduplicate(find_aus(raw))
    tracks = find_tracks(raw)
    assign_aus(tracks, aus)
    created_at, modified_at = _bundle_dates(logicx_path)

    return ProjectInfo(
        name=logicx_path.stem,
        key=md.get("SongKey", "?"),
        gender=md.get("SongGenderKey", "?"),
        bpm=md.get("BeatsPerMinute", 0.0),
        sig_numerator=md.get("SongSignatureNumerator", 4),
        sig_denominator=md.get("SongSignatureDenominator", 4),
        track_count=md.get("NumberOfTracks", 0),
        tracks=tracks,
        created_at=created_at,
        modified_at=modified_at,
    )


def fmt_au(au: AURef, lookup: dict[str, str]) -> str:
    name = lookup.get(au.fingerprint) or au.display_name
    return f"{name} [{au.fingerprint}]"


# ---------------------------------------------------------------------------
# NSKeyedArchive (bplist) extraction
# ---------------------------------------------------------------------------
#
# `ProjectData` is a custom binary format with NSKeyedArchive blobs spliced
# throughout. Each blob starts with the magic `bplist00` and ends with a
# 32-byte trailer; the file format itself never tells you a blob's length up
# front, so we scan forward from each `bplist00` for the first 32-byte chunk
# that parses as a valid trailer + lets `plistlib` accept the slice.
#
# What lives inside (per get-busy-living-style projects):
#   * Smart Controls layouts — `contentTagLayoutName`, Smart Knob/Button labels
#   * Plugin parameter mappings (Smart Controls → plugin parameter)
#   * Channel UUIDs (`_WsChannelUUID`) tying layouts to channel strips
#   * Automation curve points (`MAGraphPoint`)
# What does NOT live here (it's in the binary section of ProjectData):
#   * User-facing track names
#   * Channel routing / strip configuration
#   * Plugin slot assignments
#   * Region positions and timing
#
# So bplists are useful for *enriching* the track view with Smart Controls
# metadata, but they are not a substitute for binary parsing for core data.


def find_bplist_end(raw: bytes, start: int, max_end: int) -> int | None:
    """Return one-past-end of the bplist whose `bplist00` magic is at `start`.

    Walks forward looking for a 32-byte trailer:
        5 unused (zero) | sortVersion | offsetIntSize | objectRefSize
        | uint64 numObjects | uint64 topObject | uint64 offsetTableOffset
    A candidate is accepted only if `plistlib.loads()` of the slice succeeds.
    """
    pos = start + 8
    while pos + 32 <= max_end:
        trailer = raw[pos:pos + 32]
        if trailer[:5] == b"\x00\x00\x00\x00\x00" \
                and trailer[6] in (1, 2, 4, 8) \
                and trailer[7] in (1, 2, 4, 8):
            num_objects = struct.unpack(">Q", trailer[8:16])[0]
            top_object = struct.unpack(">Q", trailer[16:24])[0]
            ot_offset = struct.unpack(">Q", trailer[24:32])[0]
            length = (pos + 32) - start
            if (0 < num_objects < 1_000_000
                    and top_object < num_objects
                    and 8 <= ot_offset < length):
                try:
                    plistlib.loads(raw[start:pos + 32], fmt=plistlib.FMT_BINARY)
                    return pos + 32
                except Exception:
                    pass
        pos += 1
    return None


@dataclass
class BPlistBlob:
    start: int
    end: int
    archive: dict


def extract_bplists(raw: bytes) -> list[BPlistBlob]:
    """Find every embedded NSKeyedArchive blob and parse it."""
    out: list[BPlistBlob] = []
    starts = [m.start() for m in re.finditer(rb"bplist00", raw)]
    for i, s in enumerate(starts):
        next_s = starts[i + 1] if i + 1 < len(starts) else len(raw)
        end = find_bplist_end(raw, s, min(s + 2_000_000, next_s + 200))
        if end is None:
            continue
        try:
            arch = plistlib.loads(raw[s:end], fmt=plistlib.FMT_BINARY)
        except Exception:
            continue
        out.append(BPlistBlob(start=s, end=end, archive=arch))
    return out


def resolve_archive(archive: dict, ref=None, _seen: set | None = None) -> object:
    """Walk an NSKeyedArchive object graph into native Python.

    Resolves NSArray / NSDictionary / NSString / NSData wrappers; preserves
    custom classes as dicts annotated with a synthetic `__class` key.
    Cycles return a `<cycle:N>` sentinel.
    """
    if _seen is None:
        _seen = set()
    if ref is None:
        ref = next(iter(archive["$top"].values()))
    idx = ref.data if isinstance(ref, plistlib.UID) else ref
    if idx in _seen:
        return f"<cycle:{idx}>"
    _seen = _seen | {idx}

    objects = archive["$objects"]
    obj = objects[idx]
    if obj == "$null":
        return None
    if isinstance(obj, (int, float, bool, bytes, str)):
        return obj
    if isinstance(obj, list):
        return [resolve_archive(archive, x, _seen) for x in obj]
    if not isinstance(obj, dict):
        return obj

    class_name: str | None = None
    cls_uid = obj.get("$class")
    if isinstance(cls_uid, plistlib.UID):
        cls_obj = objects[cls_uid.data]
        if isinstance(cls_obj, dict):
            class_name = cls_obj.get("$classname")

    if class_name in ("NSArray", "NSMutableArray", "NSSet", "NSMutableSet"):
        return [resolve_archive(archive, x, _seen) for x in obj.get("NS.objects", [])]
    if class_name in ("NSDictionary", "NSMutableDictionary"):
        keys = [resolve_archive(archive, k, _seen) for k in obj.get("NS.keys", [])]
        vals = [resolve_archive(archive, v, _seen) for v in obj.get("NS.objects", [])]
        out: dict = {}
        for k, v in zip(keys, vals):
            try:
                out[k] = v
            except TypeError:  # unhashable (dict/list keys); stringify
                out[repr(k)] = v
        return out
    if class_name in ("NSString", "NSMutableString"):
        return obj.get("NS.string")
    if class_name in ("NSData", "NSMutableData"):
        return obj.get("NS.data")

    out = {"__class": class_name}
    for k, v in obj.items():
        if k.startswith("$"):
            continue
        if isinstance(v, plistlib.UID):
            out[k] = resolve_archive(archive, v, _seen)
        elif isinstance(v, list):
            out[k] = [resolve_archive(archive, x, _seen) if isinstance(x, plistlib.UID) else x for x in v]
        else:
            out[k] = v
    return out


def summarise_bplists(blobs: list[BPlistBlob]) -> None:
    """Print a class-distribution and Smart-Controls layout summary."""
    print(f"\n=== NSKeyedArchive blobs ({len(blobs)}) ===")
    if not blobs:
        return

    class_counts: Counter[str] = Counter()
    for blob in blobs:
        for obj in blob.archive.get("$objects", []):
            if isinstance(obj, dict) and "$classname" in obj:
                class_counts[obj["$classname"]] += 1

    print(f"Class distribution (top 15):")
    for c, n in class_counts.most_common(15):
        print(f"  {n:>4}: {c}")

    layouts: Counter[str] = Counter()
    channel_uuids: set[bytes] = set()
    for blob in blobs:
        try:
            top = resolve_archive(blob.archive)
        except Exception:
            continue
        if isinstance(top, dict):
            name = top.get("contentTagLayoutName")
            if isinstance(name, str):
                layouts[name] += 1
            uuid_obj = top.get("UUID")
            if isinstance(uuid_obj, dict):
                ub = uuid_obj.get("UUIDBytes")
                if isinstance(ub, bytes):
                    channel_uuids.add(ub)

    if layouts:
        print(f"\nSmart Controls layouts ({sum(layouts.values())} blobs reference one):")
        for n, c in sorted(layouts.items(), key=lambda x: -x[1])[:20]:
            print(f"  {c:>4}: {n}")

    if channel_uuids:
        print(f"\nDistinct channel UUIDs referenced: {len(channel_uuids)}")


JSON_SCHEMA_VERSION = 1


def _au_to_dict(au: AURef, lookup: dict[str, str]) -> dict:
    """Serialise an AURef to the JSON shape."""
    return {
        "type_code": au.type_code,
        "subtype": au.subtype,
        "manufacturer": au.manufacturer,
        "fingerprint": au.fingerprint,
        "display_name": au.display_name,
        "resolved_name": lookup.get(au.fingerprint),
    }


def _track_to_dict(track: Track, lookup: dict[str, str]) -> dict:
    """Serialise a Track to the JSON shape."""
    return {
        "kind": track.kind,
        "strip_name": track.name,
        "is_active": track.is_active,
        "display_name": track.display_name(lookup),
        "instrument": _au_to_dict(track.instrument, lookup) if track.instrument else None,
        "midi_fx": [_au_to_dict(fx, lookup) for fx in track.midi_fx],
        "audio_fx": [_au_to_dict(fx, lookup) for fx in track.audio_fx],
    }


def project_to_json(info: ProjectInfo, lookup: dict[str, str]) -> str:
    """Serialise project state to a stable JSON wire format.

    Schema (version 1):
      schema_version: int
      project: { name, key, gender, bpm, time_signature, track_count,
                 created_at (ISO), modified_at (ISO) }
      tracks: [ { kind, strip_name, display_name, is_active,
                  instrument, midi_fx, audio_fx } ]
      vendors: { manufacturer_4cc: plugin_count }
    """
    user_tracks = [t for t in info.tracks if t.is_user_track and t.is_active]
    track_dicts = [_track_to_dict(t, lookup) for t in user_tracks]

    # Vendor rollup: count plugins per manufacturer 4CC
    vendor_counts: Counter[str] = Counter()
    for t in user_tracks:
        if t.instrument:
            vendor_counts[t.instrument.manufacturer] += 1
        for fx in t.midi_fx:
            vendor_counts[fx.manufacturer] += 1
        for fx in t.audio_fx:
            vendor_counts[fx.manufacturer] += 1

    payload = {
        "schema_version": JSON_SCHEMA_VERSION,
        "project": {
            "name": info.name,
            "key": info.key,
            "gender": info.gender,
            "bpm": info.bpm,
            "time_signature": f"{info.sig_numerator}/{info.sig_denominator}",
            "track_count": info.track_count,
            "created_at": info.created_at.isoformat(),
            "modified_at": info.modified_at.isoformat(),
        },
        "tracks": track_dicts,
        "vendors": dict(vendor_counts),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def aggregate_rollup(project_jsons: list[dict]) -> dict:
    """Aggregate per-project JSON payloads into a cross-project rollup.

    Returns a dict with:
      projects: per-project summaries (name + plugin counts)
      fingerprints: count of distinct fingerprints across all projects
      vendors: count of plugins per manufacturer 4CC across all projects
    """
    fp_counts: Counter[str] = Counter()
    vendor_counts: Counter[str] = Counter()
    project_summaries: list[dict] = []

    for payload in project_jsons:
        proj = payload.get("project", {})
        tracks = payload.get("tracks", [])
        per_proj_fps: set[str] = set()
        plugin_count = 0
        for track in tracks:
            for au in [track.get("instrument")] + track.get("midi_fx", []) + track.get("audio_fx", []):
                if au is None:
                    continue
                fp = au.get("fingerprint")
                if fp:
                    per_proj_fps.add(fp)
                    plugin_count += 1
        # Each project counts each fingerprint once toward fingerprints rollup
        for fp in per_proj_fps:
            fp_counts[fp] += 1
        # Vendor counts add through directly
        for vendor, count in payload.get("vendors", {}).items():
            vendor_counts[vendor] += count
        project_summaries.append({
            "name": proj.get("name"),
            "plugin_count": plugin_count,
            "unique_fingerprints": len(per_proj_fps),
        })

    return {
        "projects": project_summaries,
        "fingerprints": dict(fp_counts),
        "vendors": dict(vendor_counts),
    }


def rollup_projects(paths: list[Path], lookup: dict[str, str]) -> dict:
    """Parse each project, build the cross-project rollup.

    Bad projects (missing Alternatives, corrupt MetaData) are skipped with
    a warning to stderr — the rollup still completes.
    """
    payloads = []
    for path in paths:
        try:
            info = parse_project(Path(path))
            payloads.append(json.loads(project_to_json(info, lookup)))
        except (StopIteration, FileNotFoundError, KeyError, ValueError) as exc:
            print(f"[rollup] skipped {path}: {exc}", file=sys.stderr)
            continue
    return aggregate_rollup(payloads)


def main(path: str, dump_bplists: bool = False, as_json: bool = False) -> None:
    alt = next(Path(path).glob("Alternatives/*"))
    raw = (alt / "ProjectData").read_bytes()
    info = parse_project(Path(path))
    lookup = auval_lookup_cached()

    if as_json:
        print(project_to_json(info, lookup))
        return

    fmt_dt = "%Y-%m-%d %H:%M"
    print(f"Project:        {info.name}")
    print(f"Created:        {info.created_at.strftime(fmt_dt)}")
    print(f"Modified:       {info.modified_at.strftime(fmt_dt)}")
    print(f"Key:            {info.key} {info.gender}")
    print(f"Time signature: {info.sig_numerator}/{info.sig_denominator}")
    print(f"Tempo:          {info.bpm:g} BPM")
    print(f"Tracks:         {info.track_count}")

    user_tracks = [t for t in info.tracks if t.is_user_track and t.is_active]
    print(f"\n=== TRACKS ({len(user_tracks)} active) ===")
    for i, t in enumerate(user_tracks, 1):
        display = t.display_name(lookup)
        label = f"{display}  ({t.name})" if display != t.name else t.name
        print(f"  {i:>2}. {label}  [{t.kind}]")
        if t.instrument:
            print(f"        Instrument: {fmt_au(t.instrument, lookup)}")
        for fx in t.midi_fx:
            print(f"        MIDI FX:    {fmt_au(fx, lookup)}")
        for fx in t.audio_fx:
            print(f"        Audio FX:   {fmt_au(fx, lookup)}")

    region_records = find_region_records(raw)
    header_records = find_track_header_records(raw)
    registry_records = find_track_registry_records(raw)
    # Project name leaks into the various registries — filter at output time.
    project_name = info.name
    region_records = [r for r in region_records if r.name != project_name]
    header_records = [r for r in header_records if r.name != project_name]
    registry_records = [r for r in registry_records if r.name != project_name]

    tracks = tracks_from_evidence(registry_records, header_records, region_records)
    if tracks:
        # Sort by track_id (Logic's per-track creation order) — matches UI
        # ordering for the simple case of "added tracks in order, no manual
        # rearrangement". When the user reorders rows, the file stores a
        # separate ordering list we haven't located yet (#34).
        tracks.sort(key=lambda t: (t.track_id, t.first_offset))
        print(
            f"\n=== TRACK LIST ({len(tracks)} tracks) ==="
            "\n(track-id order; duplicate names = different tracks; UI"
            "\nrow ordering is a permutation of this stored elsewhere)"
        )
        for i, t in enumerate(tracks, 1):
            strip_label = f"strip {t.strip_id}" if t.strip_id else "—"
            id_label = f"id {t.track_id}" if t.track_id else "—"
            print(
                f"  {i:>2}. {t.base_name:30s}  "
                f"type: {t.kind:13s}  "
                f"{id_label:7s}  "
                f"{strip_label:9s}  "
                f"({t.count} regions)"
            )

    if dump_bplists:
        summarise_bplists(extract_bplists(raw))


def main_rollup(paths: list[str]) -> None:
    """Cross-project rollup mode — emits aggregated JSON."""
    lookup = auval_lookup_cached()
    bundle_paths = [Path(p) for p in paths]
    result = rollup_projects(bundle_paths, lookup)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--rollup" in args:
        args = [a for a in args if a != "--rollup"]
        main_rollup(args)
    else:
        dump = "--bplists" in args
        as_json = "--json" in args
        args = [a for a in args if a not in ("--bplists", "--json")]
        main(args[0], dump_bplists=dump, as_json=as_json)
