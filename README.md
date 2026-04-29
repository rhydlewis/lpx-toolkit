# lpx-toolkit

A Python tool that extracts the plugin manifest, track list, and metadata from a Logic Pro project file (`.logicx`) without opening Logic.

Read-only by design. The format is undocumented, so writing back is permanently out of scope — see `CLAUDE.md`.

## What it does

Given a `.logicx` project package, `lpx-toolkit` parses the binary `ProjectData` file and reports:

- Project metadata: name, key, time signature, tempo, created/modified dates, track count
- Active channel strips with their kind (audio/instrument/aux/bus)
- Per-strip plugin chain — instrument, MIDI FX, audio FX — with auval-resolved display names
- User-renamed track labels recovered from region records

Sample output:

```
Project:        piano
Created:        2024-02-29 20:23
Modified:       2024-03-01 13:55
Key:            C major
Time signature: 4/4
Tempo:          105 BPM
Tracks:         3

=== TRACKS (3 active) ===
   1. EZkeys 2  (Inst 1)  [instrument]
        Instrument: Toontrack: EZkeys 2 [aumu/EZk2/Toon]
   2. Scaler 2  (Inst 2)  [instrument]
        Instrument: Plugin Boutique: Scaler 2 [aumu/Scl2/eMai]
   3. Pigments  (Inst 3)  [instrument]
        Instrument: Arturia: Pigments [aumu/Kat1/Artu]
```

## How it works

`.logicx` is a macOS bundle. Inside, `Alternatives/000/ProjectData` is undocumented binary. The parser was reverse-engineered empirically:

- AU plugin descriptors live as 12-byte chunks: `manufacturer + type + subtype` 4CCs stored little-endian. The type field is the anchor (`umua`/`xfua`/`fmua` reversed).
- Channel strips appear as `OCuA` records, each carrying a 16-byte name field followed by a 4-byte type code.
- User-given track names are stored inside `gRuA` (Audio Region) records — name is at offset +112, length-prefixed by a uint16 LE.
- `auval -l` maps captured fingerprints to canonical plugin names from the system Audio Unit registry.
- `MetaData.plist` (a standard Apple plist) supplies key/tempo/time signature.

The parser depends only on the Python standard library. `auval` is the only external call, and only when running on macOS.

See `CLAUDE.md` for a detailed walk through the format and the specific quirks that have been encountered.

## Caveats

- **Not a live state read.** `ProjectData` retains references to plugins from undo history, alternative takes, and previously-deleted tracks. The output is "every AU this project has referenced", not "what's loaded right now".
- **Display names truncate to ~11 characters in the binary.** Full names recovered via `auval -l` when the plugin is installed.
- **`auval` requires the plugins to be installed.** Missing plugins still surface as a fingerprint — useful for "what dependencies do I need before opening this".
- **Format is undocumented.** Apple does not publish the `ProjectData` format. Extraction relies on observed patterns; future Logic versions may shift the layout.

## Requirements

- macOS (for `auval`; the parsing itself is platform-agnostic)
- Python 3.10+
- A Logic Pro project to inspect

## Usage

```sh
python3 lpx_inspect.py ~/Music/Logic/SomeProject.logicx
python3 lpx_inspect.py ~/Music/Logic/SomeProject.logicx --bplists
```

## Tests

The runtime parser is stdlib-only; pytest is a dev-only dep:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

To run the integration tests against a real project, point `LPX_TEST_PROJECT` at it:

```sh
LPX_TEST_PROJECT=~/Music/Logic/SomeProject.logicx .venv/bin/pytest
```

## Project status

Working tool. The track list, plugin chains, and metadata extraction are stable. The full per-strip mapping for user-renamed tracks is the headline open question — see `CLAUDE.md` § *Region records and user-renamed track names* for what's been ruled out.

`pm-feedback.md` records the strategic priorities. `CLAUDE.md` records the read-only contract, format notes, and out-of-scope items.

## Licence

MIT — see `LICENSE`.
