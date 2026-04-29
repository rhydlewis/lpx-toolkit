"""Tests for the auval -l line parser.

These are characterisation tests for existing behaviour — the code in
`lpx_inspect.parse_auval_line` predates this suite. Each test pins down one
documented format quirk so a regression shows up immediately.
"""
from types import SimpleNamespace

import lpx_inspect
from lpx_inspect import auval_lookup, parse_auval_line


def test_parses_standard_4cc_line():
    line = "aumu EZk2 Toon  -  Toontrack: EZkeys 2     (file:///Library/Audio/Plug-Ins/Components/EZkeys%202.component/) [AUv2]"
    assert parse_auval_line(line) == ("aumu", "EZk2", "Toon", "Toontrack: EZkeys 2")


def test_preserves_trailing_spaces_in_subtype():
    """Soundtoys EchoBoy uses a 2-char subtype 'EB' padded to 'EB  '. The
    trailing spaces are part of the 4CC and must survive parsing — they go
    into the fingerprint key and need to round-trip with ProjectData bytes."""
    line = "aufx EB   SToy  -  Soundtoys: EchoBoy     (file:///Library/Audio/Plug-Ins/Components/EchoBoy.component/) [AUv2]"
    typ, sub, mfr, name = parse_auval_line(line)
    assert sub == "EB  "
    assert (typ, mfr, name) == ("aufx", "SToy", "Soundtoys: EchoBoy")


def test_preserves_leading_space_in_manufacturer():
    """Kilohearts ' kHs' has a leading space that auval renders as a wider
    gap between subtype and manufacturer columns. The 4CC is still 4 chars,
    just space-padded on the left."""
    line = "aufx kscp  kHs  -  Kilohearts: kHs Compressor     (file:///Library/Audio/Plug-Ins/Components/kHs%20Compressor.component/) [AUv2]"
    typ, sub, mfr, name = parse_auval_line(line)
    assert mfr == " kHs"
    assert (typ, sub, name) == ("aufx", "kscp", "Kilohearts: kHs Compressor")


def test_does_not_split_plugin_names_containing_hyphens():
    """'AmpKnob - RevC' contains the same ' - ' that separates 4CCs from the
    name. Partitioning must use the FIRST occurrence so the full name is
    preserved."""
    line = "aufx Akrc Bgrn  -  Bogren Digital: AmpKnob - RevC     (file:///Library/Audio/Plug-Ins/Components/AmpKnob%20-%20RevC.component/) [AUv2]"
    assert parse_auval_line(line) == (
        "aufx", "Akrc", "Bgrn", "Bogren Digital: AmpKnob - RevC",
    )


def test_returns_none_for_lines_without_separator():
    """Headers, blank lines and version banners have no ' - ' and must be
    skipped without raising."""
    assert parse_auval_line("    AU Validation Tool") is None
    assert parse_auval_line("") is None
    assert parse_auval_line("    Version: 1.10.0") is None


def test_auval_lookup_builds_fingerprint_table_from_subprocess(monkeypatch, auval_sample):
    """auval_lookup() shells out to `auval -l` then folds the parsed lines
    into a dict keyed on `type/subtype/mfr`. Replace subprocess.run so this
    test runs anywhere — not just on a Mac with auval installed."""
    monkeypatch.setattr(
        lpx_inspect.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=auval_sample),
    )
    table = auval_lookup()
    assert table["aumu/EZk2/Toon"] == "Toontrack: EZkeys 2"
    # Trailing-space subtype survives the round-trip into the fingerprint key.
    assert table["aufx/EB  /SToy"] == "Soundtoys: EchoBoy"
    # Leading-space manufacturer survives too.
    assert table["aufx/kscp/ kHs"] == "Kilohearts: kHs Compressor"


def test_auval_lookup_returns_empty_dict_when_binary_missing(monkeypatch):
    """On non-macOS hosts (or when auval is broken), the parser must degrade
    gracefully — returning an empty lookup so the caller can still print
    fingerprints."""
    def boom(*args, **kwargs):
        raise FileNotFoundError("auval not installed")
    monkeypatch.setattr(lpx_inspect.subprocess, "run", boom)
    assert auval_lookup() == {}
