#!/usr/bin/env python3
"""Logic Pro project inspector — extracts metadata, tracks, and AU plugins."""
import plistlib
import re
import struct
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

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
    """A run of consecutive region records sharing one base name — i.e. one
    user-perceived track. base_name is the cleaned form; count is how many
    records contributed; first/last_offset bracket the byte range."""
    base_name: str
    count: int
    first_offset: int
    last_offset: int


def tracks_from_regions(records: list[tuple[int, str]]) -> list[RegionCluster]:
    """Collapse region records into unique tracks, in first-appearance order.

    Tracks' regions interleave in `ProjectData` once a project gets
    edit-heavy. Deduping by base name keeps each track once and sums all
    its regions; first-appearance order is a usable proxy for arrangement
    order without parsing the (still-unidentified) track-list metadata.
    """
    by_name: dict[str, RegionCluster] = {}
    for offset, raw_name in records:
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
            )
        else:
            existing.count += 1
            existing.last_offset = offset
    return list(by_name.values())


def cluster_regions(records: list[tuple[int, str]]) -> list[RegionCluster]:
    """Group consecutive records (in offset order) by their base name.

    Each track's regions are stored contiguously in ProjectData, so a run of
    consecutive records sharing one base name (after take/comp suffix
    stripping) corresponds to a single user-perceived track. Records that
    are recording filenames or bare comp tags are excluded; they don't open
    a new cluster but also don't break the surrounding one.
    """
    clusters: list[RegionCluster] = []
    current: RegionCluster | None = None
    for offset, raw_name in records:
        cleaned = _strip_region_suffixes(raw_name)
        if not _is_user_track_name(cleaned):
            continue
        if current is not None and current.base_name == cleaned:
            current.count += 1
            current.last_offset = offset
        else:
            current = RegionCluster(
                base_name=cleaned,
                count=1,
                first_offset=offset,
                last_offset=offset,
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


# Track-registry signatures observed empirically. Each Logic track entry has
# a 16-byte preamble: 4 zeros + 2-byte signature + 4 zeros + 2 bytes + 2 zeros
# + 2-byte LE length + ASCII name. Different track *kinds* use different
# signatures; buses and presets share the same outer structure but with
# different signatures, so we whitelist only the track ones.
TRACK_SIGNATURES = frozenset({
    b"\x22\x12",  # MIDI / instrument tracks
    b"\x23\x12",  # audio tracks (some)
    b"\xdc\x11",  # audio tracks (some)
    b"\xdf\x11",  # audio tracks (Slide GTR / Intro Lead GTR family)
    b"\xa8\x11",  # single-instrument tracks (Dome Kick)
    b"\x74\x10",  # sub / percussion folder
    b"\xcb\x10",  # sub / dialogue folder
    b"\xe3\x11",  # sub / keys folder
    b"\xe4\x10",  # sub / bells & synth keys folder
    b"\xeb\x11",  # sub / strings & pads folder
    b"\xe7\x11",  # atmosphere / pad-cluster folder
})

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


def find_track_registry_records(raw: bytes) -> list[tuple[int, str]]:
    """Extract (offset, name) pairs from track-registry entries.

    Each Logic track has a registry entry with a 16-byte preamble whose 2-byte
    signature identifies the track kind. We whitelist signatures that
    correspond to real user tracks (audio / instrument / sub headers), which
    excludes buses and preset entries that share the outer structure.
    """
    out: list[tuple[int, str]] = []
    for m in TRACK_REGISTRY_RE.finditer(raw):
        sig = m.group(1)
        if sig not in TRACK_SIGNATURES:
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
        out.append((m.start(), name))
    return out


def find_track_header_records(raw: bytes) -> list[tuple[int, str]]:
    """Extract (offset, name) pairs from track-header records.

    These are emitted once per Logic track and include MIDI/instrument
    tracks that the audio-region (`gRuA`) parser misses entirely. System
    records that share the signature (`*Automation`, take-folder
    `RBA Sequence`, `Untitled` placeholders) are filtered out — they're
    Logic-internal scaffolding, not user tracks.
    """
    out: list[tuple[int, str]] = []
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
        out.append((m.start(), name))
    return out


def find_region_records(raw: bytes) -> list[tuple[int, str]]:
    """Extract (offset, name) pairs for every valid region record."""
    out: list[tuple[int, str]] = []
    for m in REGION_MARKER_RE.finditer(raw):
        len_off = m.end()
        length = struct.unpack("<H", raw[len_off:len_off + 2])[0]
        if not 0 < length <= REGION_NAME_MAX_LEN:
            continue
        name_bytes = raw[len_off + 2:len_off + 2 + length]
        if not all(0x20 <= b < 0x7f for b in name_bytes):
            continue
        out.append((m.start(), name_bytes.decode("ascii")))
    return out


def find_region_names(raw: bytes) -> list[str]:
    """Extract user-facing region names from ProjectData binary.

    Each region record carries: <4-byte id> 0x61 0xff <24 zeros> <uint16-LE
    length> <ascii name>. The name is the same string Logic shows in the
    track header (regions inherit it from their parent track by default).
    """
    return [name for _, name in find_region_records(raw)]


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


def main(path: str, dump_bplists: bool = False) -> None:
    alt = next(Path(path).glob("Alternatives/*"))
    raw = (alt / "ProjectData").read_bytes()
    info = parse_project(Path(path))
    lookup = auval_lookup()

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
    all_records = [
        r for r in region_records + header_records + registry_records
        if r[1] != info.name
    ]
    combined = sorted(all_records, key=lambda r: r[0])
    tracks = tracks_from_regions(combined)
    if tracks:
        print(
            f"\n=== TRACK LIST ({len(tracks)} from region + header + registry records) ==="
            "\n(first-appearance order; strip shown when the region name"
            "\nmatches a default channel-strip pattern)"
        )
        for i, t in enumerate(tracks, 1):
            auto = bool(_AUTO_TRACK_NAME_RE.match(t.base_name))
            strip = t.base_name if auto else "—"
            print(f"  {i:>2}. {t.base_name:30s}  strip: {strip:10s}  ({t.count} regions)")

    if dump_bplists:
        summarise_bplists(extract_bplists(raw))


if __name__ == "__main__":
    args = sys.argv[1:]
    dump = "--bplists" in args
    args = [a for a in args if a != "--bplists"]
    main(args[0], dump_bplists=dump)
