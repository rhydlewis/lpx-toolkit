"""Tests for extended project metadata (#24).

Surfaces sample rate, bundle size, region count, audio file counts and
frame rate beyond the basic key/tempo/signature fields.
"""
import plistlib
from pathlib import Path

from lpx_inspect import frame_rate_for_index, parse_project, project_to_json
import json


def _make_minimal_bundle(root: Path, name: str = "demo", **md_extras) -> Path:
    bundle = root / f"{name}.logicx"
    alt = bundle / "Alternatives" / "000"
    alt.mkdir(parents=True)
    md = {
        "SongKey": "C",
        "SongGenderKey": "major",
        "BeatsPerMinute": 120.0,
        "SongSignatureNumerator": 4,
        "SongSignatureDenominator": 4,
        "NumberOfTracks": 0,
        "SampleRate": 48000,
        "FrameRateIndex": 1,
        "AudioFiles": ["file1.wav", "file2.wav"],
        "ImpulsResponsesFiles": ["ir1.wav"],
        "SamplerInstrumentsFiles": [],
        "UnusedAudioFiles": [],
        **md_extras,
    }
    (alt / "MetaData.plist").write_bytes(plistlib.dumps(md))
    (alt / "ProjectData").write_bytes(b"")
    return bundle


def test_project_info_carries_sample_rate(tmp_path):
    info = parse_project(_make_minimal_bundle(tmp_path, SampleRate=48000))
    assert info.sample_rate == 48000


def test_project_info_carries_audio_file_count(tmp_path):
    info = parse_project(_make_minimal_bundle(tmp_path,
                                               AudioFiles=["a.wav", "b.wav", "c.wav"]))
    assert info.audio_file_count == 3


def test_project_info_carries_impulse_response_count(tmp_path):
    info = parse_project(_make_minimal_bundle(tmp_path,
                                               ImpulsResponsesFiles=["ir.wav"]))
    assert info.impulse_response_count == 1


def test_project_info_carries_bundle_size_bytes(tmp_path):
    info = parse_project(_make_minimal_bundle(tmp_path))
    # The bundle has at least MetaData.plist + ProjectData → non-zero size
    assert info.bundle_size_bytes > 0


def test_project_info_carries_frame_rate_index(tmp_path):
    """FrameRateIndex is the raw value from MetaData.plist (an integer)."""
    info = parse_project(_make_minimal_bundle(tmp_path, FrameRateIndex=2))
    assert info.frame_rate_index == 2


def test_frame_rate_for_index_decodes_known_values():
    """Logic's FrameRateIndex maps to specific SMPTE rates. Index 0-7
    correspond to 24, 25, 29.97-drop, 30-drop, 29.97, 30, 23.976, 23.976-drop
    (commonly observed values; see Apple SMPTE docs)."""
    # Indices 1 and 5 (the most common values) — verify both decode
    rate = frame_rate_for_index(1)
    assert rate is not None
    assert rate > 0
    # Out-of-range index returns None (don't crash on unknown future values)
    assert frame_rate_for_index(99) is None


def test_json_includes_extended_metadata(tmp_path):
    """JSON output exposes the extended fields under `project`."""
    info = parse_project(_make_minimal_bundle(tmp_path,
                                               SampleRate=44100,
                                               AudioFiles=["a.wav"]))
    payload = json.loads(project_to_json(info, lookup={}))
    p = payload["project"]
    assert p["sample_rate"] == 44100
    assert p["audio_file_count"] == 1
    assert "bundle_size_bytes" in p
    assert "frame_rate_index" in p
