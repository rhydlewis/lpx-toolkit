# CLAUDE.md

Guidance for Claude Code working on this project.

## What this project is

A Python tool that extracts the Audio Unit (AU) plugin manifest from Logic Pro `.logicx` project files by parsing the binary `ProjectData` file directly. No Logic Pro runtime dependency.

Read `README.md` for the user-facing description. This file covers the things that are useful when *writing code* against this codebase.

## How the parsing actually works

The `ProjectData` file inside a `.logicx` bundle is undocumented binary. What we know about its structure has been derived empirically:

- **AU component descriptors** are stored as three contiguous 4-byte codes: `manufacturer + type + subtype`, all little-endian (i.e. reversed from how `auval` displays them). The type field is the anchor — we scan for `umua` / `xfua` / `fmua` (reversed `aumu` / `aufx` / `aumf`) and read 4 bytes either side.
- **Display names** are stored as ASCII shortly before the descriptor, in what appears to be a fixed-width field of ~11 characters plus a terminator. Longer names are truncated in the binary; the full name has to come from `auval -l` lookup.
- **NSKeyedArchive blobs** are spliced throughout `ProjectData`. They begin with `bplist00` and contain serialised Cocoa objects — track configurations, plugin states, channel strip settings. We don't currently parse these; they're the path to richer extraction (track→plugin mapping, parameter values, full plugin names) and represent the next major piece of work.
- **Other plists in the bundle** (`MetaData.plist`, `DisplayState.plist`, `ProjectInformation.plist`) are standard Apple plists and parseable with `plistlib`. They contain project-level metadata and UI state but **not** plugin information. `MetaData.plist`'s `*Files` arrays only track external sample/IR file references.

## auval quirks

`auval -l` is the right command for our lookup table. **Do not use `auval -a`** — it instantiates every plugin and segfaults on broken installs (notably duplicated Waves frameworks).

`auval -l` output is column-aligned, not space-separated. Manufacturer 4CCs can contain spaces (e.g. `kHs ` for Kilohearts, `EB  ` for Soundtoys EchoBoy in the subtype slot). **Parse by fixed offsets, not regex with `\s+`** — `\s+` will eat significant trailing spaces and break fingerprint matching.

The fingerprint key format is `f"{type}/{subtype}/{manufacturer}"` — preserve trailing/leading spaces verbatim. Both the parser output and the `auval` lookup must use the same key construction or matches will silently fail.

## Region records and user-renamed track names

User-given track header names (e.g. `Acoustic GTR`, `Ld GTR Low`) live inside Audio Region records in the binary section, not inside `OCuA` channel-strip records. The `gRuA` 4CC (`AuRg` reversed) marks the start of each region; the name is at offset +112, length-prefixed by a uint16 LE at +110.

**The strict strip mapping is unsolved.** Regions and `OCuA` records sit in non-overlapping byte ranges, and the candidate fields inside `gRuA` records are not consistent within a track's regions. Tried so far (don't redo without new evidence):

- Region offsets vs OCuA byte ranges — zero overlap
- 4 bytes immediately preceding the `\x61\xff` marker — varies per region within the same track
- 4 bytes at `gRuA+50` — varies within a track and doesn't appear inside any OCuA range
- Bytes 0–80 of the `gRuA` header — only ~12 of 80 vary across records of one track, but those that DO vary aren't found inside any OCuA byte range either
- Position 28 — looks discriminating at first but resolves to `name_length + 209`, just a length-related field
- Nearest preceding `karT` record — `karT` is the score-editor "track" (notation metadata) not the channel strip; one huge `karT` range spans most of the file
- Length-prefixed user-track-name strings inside OCuA byte ranges — only appear inside the *last* OCuA (a project summary record), not inside the strip they belong to
- The last OCuA range — contains MIDI map names, drum kit labels, etc. but no track-list lookup table

What we ship instead: `cluster_regions()` returns runs of consecutive same-named records (regions of one take folder), and `tracks_from_regions()` collapses them into unique tracks in first-appearance order with region counts. For tracks whose name matches Logic's default channel-strip pattern (`Audio 3`, `Inst 12`...) we annotate the strip; for user-renamed tracks the strip stays unknown. This is the most useful approximation without the bridge.

Don't conflate "no mapping yet" with "parser bug" — it's a reverse-engineering task, not a code defect.

## Track-registry record format

Beyond `gRuA` (audio regions), the binary section of `ProjectData` carries a *track registry* with one entry per Logic track (audio, instrument, sub/folder header). Two extraction paths:

**Track-header records** — fixed signature `\x70\x03\x01\x00`, 18-byte preamble before name. Catches MIDI/instrument tracks Logic emits as track-list entries (Pad, Lead Strings, Bells, etc.). Logic-internal records (`*Automation`, `RBA Sequence`, `Untitled`, `Track Alternatives`) share the signature and are filtered by name.

**Track-registry records** — generalised pattern: `<4 zeros><2-byte signature><4 zeros><2 control bytes><2 zeros><uint16 LE length><name>`. Each track *kind* uses a distinct signature:

| Signature | Kind | Examples |
|---|---|---|
| `22 12` | MIDI/instrument | Pad, Piano, Bells, Drums, Bass, Lead Strings |
| `23 12` | audio (some) | Andy & Red, Red Dialogue, Ld GTR Low, Ld GTR Harm |
| `dc 11` | audio (some) | Acoustic GTR, Classical GTR |
| `df 11` | audio | Slide GTR, Intro Lead GTR family, Middle/Outro Lead GTR |
| `a8 11` | single instrument | Dome Kick |
| `74 10` | sub / percussion folder | Timpani, Percussion |
| `cb 10` | sub / dialogue folder | Dialogue |
| `e3 11` | sub / keys folder | Keys |
| `e4 10` | sub / bells & synth folder | Bells & Synth Keys |
| `eb 11` | sub / strings & pads folder | Strings & Pads |
| `e7 11` | atmosphere / pad-cluster | Atmosphere (Millenniums) |

Bus signatures (`24 12`, `30 11`, `38 11`, `f5 11`) share the outer structure but are filtered out — buses live on the channel-strip side, not the track side.

### Summing Stack detection

Logic distinguishes Folder Stacks (visual only), Summing Stacks (`Sub N` strip — children sum to an aux), and Aux-based Track Stacks (`Atmosphere (Millenniums)` shows `Aux 8`). Summing Stacks share the registry signature with regular audio tracks (`23 12`, `dc 11`) so the signature alone isn't enough.

The discriminator is a trailer pattern after the name: `XX 01 00 NN 00 01` where `XX` ≈ `0x54 + sub_number` and `NN` is the Sub number. When this matches, kind is upgraded to `summing-stack` regardless of signature. Some records (e.g. Guitars) emit a trailing null between the name and the trailer, so `_is_summing_stack_trailer()` checks both offset-0 and offset-1 starts.

Aux-based Track Stacks and the children inside them (Atmosphere, Pad 1, Pad 2 in busy-living) currently report as the generic `folder` — distinguishing them from Summing Stacks works, but the *kind* of each non-Summing folder is left as a follow-up.

The 2 *control bytes* (offset −6/−5 from name) encode a track index/ID-like value, **not** visibility. Verified against ground truth from a project with 18 hidden tracks named: same name appearing as both visible and hidden ("Strings") shares identical control bytes `22 12 | 80 43`. The `0x80 0x13` value initially looked promising for "hidden" but only because `Ld GTR Low`/`Ld GTR Harm` happened to share an index range with other hidden tracks — not a flag, just an index.

**The hidden flag is somewhere else.** Hypothesis: a separate track-list table (still un-found) carries it. Search for ~69-occurrence record markers came up empty — closest were `Comp` (94) and `Unti` (70, false hit on 'Untitled' string ending). Tracks may need a different anchor. Open until ground-truth-driven analysis identifies the right field.

## Things that look like bugs but aren't

- **Duplicate / "phantom" plugin entries.** `ProjectData` retains references from undo history and deleted tracks. If a project shows 7 instruments but the user says they only have 5, the extra 2 are real entries — just not currently on any track. This is documented behaviour, not a parser bug.
- **Klopfgeist always present.** It's Logic's metronome AU, loaded into every project. Filter it out for user-facing instrument lists if appropriate.
- **Truncated names like "Glass Strin" or "AIR Tape Do".** The 11-char truncation is in the source data, not our extraction. The fix is `auval` resolution, not extending the lookback.
- **Manufacturer codes appearing as instrument names** (e.g. `nooT`, `Artu`). This was a real bug — the name extractor was picking up 4CCs as the "nearest ASCII run". Fix is to filter out runs ≤4 chars in `extract_name`.

## Code style

- Python 3.10+ — use modern syntax (`X | Y` unions, `match` statements where appropriate, `dataclass`).
- Standard library only where possible. The current parser has zero non-stdlib dependencies and that's worth preserving.
- Type hints throughout.
- Functions over classes for the parsing layer; reserve classes for value objects (`AUReference` is a `@dataclass`).
- British English in comments and docstrings, US English in code identifiers (matches stdlib conventions).

## Testing

**TEST-DRIVEN DEVELOPMENT IS NON-NEGOTIABLE.** Write code in response to a failing test. This is not a suggestion or a preference: it is the fundamental practice that enables all other principles in this document. All work should be done in small, incremental changes that maintain a working state throughout development.

### Development Workflow

RED-GREEN-REFACTOR in vertical slices (one test → one implementation → repeat):

- **RED**: Write ONE failing test for the next behaviour
- **VERIFY RED**: Run the test. Confirm it *fails* (not errors) and the failure message matches the missing behaviour. A test that fails due to a typo or import error is not RED — fix the error and re-verify.
- **GREEN**: Write MINIMUM code to pass that test
- **VERIFY GREEN**: Run ALL tests. Confirm the new test passes and no existing tests broke.
- **REFACTOR**: Assess improvement opportunities (only refactor if it adds value). Stay GREEN throughout — never refactor while RED.
- Each increment leaves codebase in working state

### Running tests

The runtime parser stays stdlib-only; pytest is a dev-only dependency installed in a project-local venv:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

`pyproject.toml` configures `testpaths = ["tests"]` and `pythonpath = ["."]` so `from lpx_inspect import …` works from inside `tests/` without any package install. Run a single test by name during iteration:

```sh
.venv/bin/pytest tests/test_auval_parser.py::test_preserves_trailing_spaces_in_subtype -x
```

### Verifying RED is genuine

A test that fails with `ImportError`, `NameError`, `SyntaxError`, or `AttributeError: module 'X' has no attribute 'Y'` is **not RED** — it's broken-test-infrastructure. Read the failure message before declaring RED. It must point at the missing *behaviour*, e.g. `AssertionError: expected 'EZkeys 2' got 'unknown'`. If it doesn't, fix the test before writing implementation.

The `inspect.py` → `lpx_inspect.py` rename was a real example of this trap: a stdlib name collision that masquerades as a test failure.

### Project-specific testing notes

- **Don't commit `.logicx` fixtures to the repo** — they can be large (MBs) and contain user audio. Either use a tiny synthetic fixture or generate one programmatically.
- **Mock `auval`** via `monkeypatch.setattr(lpx_inspect.subprocess, "run", ...)` returning a `SimpleNamespace(stdout=...)`. The captured fixture lives at `tests/fixtures/auval_sample.txt` and covers each documented quirk (trailing-space subtype, leading-space manufacturer, hyphenated plugin name) — extend it when you encounter a new quirk in the wild.
- The byte-extraction logic can be tested against hand-constructed binary fixtures: 4CC + name + padding patterns are easy to synthesise. Build helpers in `conftest.py` rather than repeating layout-byte literals across test files.
- **Characterisation tests vs RED-first**: the current `tests/test_auval_parser.py` pins down behaviour that already existed when the suite was added. New behaviour (e.g. region-record track-name extraction) starts RED-first — write the failing test before any implementation.

## Things to be careful about

- **Don't add features that require Logic to be open or running.** The whole value of this approach is being able to inspect projects offline. If we need live state, that's a separate tool using the macOS Accessibility API (see `gzinck/logic-automator` for prior art) — keep it in a separate module/package.
- **Don't try to write back to `ProjectData`.** The format is undocumented and corrupting a project file is a permanent loss of work. This tool is read-only and should stay that way unless there's an extremely good reason and a thorough test fixture set.
- **Don't shell out to `auval` on every invocation without caching.** It can take 5-30 seconds on first run. Cache the parsed lookup table (e.g. JSON in `~/.cache/logic-inspector/auval.json`) with sensible invalidation (mtime of `/Library/Audio/Plug-Ins/Components/`).
- **Be charitable about format variation.** Logic project format has shifted across versions. If a parser assumption fails, prefer "skip and continue" over "raise"; surface unparseable regions as warnings, not crashes.

## When stuck

If extraction fails on a new project:

1. Run the diagnostic script (the original `temp2.py` pattern: full `MetaData.plist` dump + direct byte search for known plugin names + ASCII strings around AU markers).
2. Check whether the plugin is referenced as raw bytes or only inside an `NSKeyedArchive` blob — that determines whether it's a parser fix or requires the bplist-decoding work.
3. The conversation history that built this tool is a useful reference for the empirical format discoveries. The format wasn't documented; it was reverse-engineered iteratively.

## Read-only contract

**This tool MUST NOT write to a Logic project file under any circumstance.**

The `.logicx` format is undocumented. Any unintended write — to `ProjectData`, the bundle plists, even an mtime touch — risks silent corruption of irrecoverable user work. There is no "harmless" write path here.

Enforcement:

- `tests/test_readonly_invariant.py` snapshots every file's SHA-256 + mtime before `parse_project()` and asserts no change after. This is the contract; do not weaken or skip it.
- Extraction helpers (`find_aus`, `find_tracks`, `find_region_names`, `extract_bplists`) take `bytes`, not paths — they cannot open a file at all.
- The only filesystem writes this codebase is allowed to perform live outside the project bundle: the auval cache at `~/.cache/lpx-toolkit/`, future JSON/HTML output to user-specified paths, and stdout. Nothing else.

If you find yourself reaching for `open(bundle_path, "w")` or `Path(...).write_*()` against anything inside a `.logicx`, stop. The answer is to put the data somewhere outside the bundle.

## Out of scope

These are explicit non-goals. Do not propose them as features without an extraordinarily strong reason.

- **Writing or modifying Logic projects.** See *Read-only contract* above. The `tests/test_readonly_invariant.py` guard exists precisely so this can never happen by accident.
- **GUI / SwiftUI app in this repo.** A CLI + JSON output is the right surface. A GUI is a 5x scope expansion for marginal value over scriptability. If anyone wants a GUI, they wrap the library from a separate repo.
- **Live mixer state / Logic automation.** Different tool, different paradigm (macOS Accessibility API, requires Logic to be running). Belongs in a separate package — the offline-first promise is the wedge.
- **Cross-DAW support** (Pro Tools, Ableton Live, Cubase, Reaper). Each format is a multi-month reverse-engineering project. Depth in `.logicx` is the value, not breadth across DAWs.
- **VST/VST3 plugin support.** Logic only loads Audio Units. A VST scanner solves a problem the target user doesn't have.
- **SaaS / "upload your project" service.** Privacy-hostile, undifferentiated, and contradicts the offline-first wedge.
- **"Smart" features that guess at user intent.** Credibility comes from being a faithful reporter of what's in the file. Hiding orphan plugins from undo history without a flag, or auto-renaming tracks based on plugin contents, are anti-patterns. Filtering opt-out is fine; silent guesses are not.
