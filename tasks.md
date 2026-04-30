# Tasks

Cross-session persistence of the in-flight task list. Mirror this back into the harness's task tracker at session start with TaskCreate; sync any status changes here when commits land.

Priority ordering follows `pm-feedback.md` (Bet 1 → Bet 2 → Bet 3) and the user's confirmed list. Tasks that are deferred or blocked are clearly tagged.

---

## Pending

Priority order (set 2026-04-30 after repo went public):

1. #21 PyPI release ✓ + Homebrew tap ✓
2. #38 GitHub Actions CI ✓
3. #41 Promotion on forums and Reddit (user-actioned) — only remaining active task

Reverse-engineering puzzles tracked as GitHub issues — see [#1](https://github.com/rhydlewis/lpx-toolkit/issues/1)–[#4](https://github.com/rhydlewis/lpx-toolkit/issues/4).

### Active

#### #41 Promotion on forums and Reddit `[user-actioned]`

Get the tool in front of Logic Pro users and music producers. Not a coding task — needs the user's voice for the post copy.

**Where to post:**
- r/LogicPro — primary audience. Mention "before you open a project on a new machine, see every plugin it needs". Lead with the rollup view image.
- r/WeAreTheMusicMakers — secondary. Frame as "audit your plugin library, see which ones you actually use".
- logicprohelp.com — Logic-specific forum, longer-form post likely OK.
- Hacker News — `Show HN: lpx-toolkit — read-only Logic Pro project inspector`. Lead with the reverse-engineering angle (undocumented binary format, parsed offline) rather than the music-production angle.
- Mastodon (#LogicPro / #musicproduction tags) and any DAW Discord communities the user frequents.

**Hook**: read-only by design (safe), runs offline (privacy), HTML dashboard, cross-project rollup answers "which of my installed plug-ins do I actually use?", source-available + free.

**Distinctive vs other tools**: most "Logic project utilities" require Logic to be running. This doesn't — it parses the project file directly. That's the wedge.

**Materials ready**: README (with two screenshots), CONTRIBUTING.md, four open reverse-engineering issues for community participation, public repo at https://github.com/rhydlewis/lpx-toolkit.

### Deferred (now tracked as GitHub issues)

[#1](https://github.com/rhydlewis/lpx-toolkit/issues/1) Track Stack parent→child mapping · [#2](https://github.com/rhydlewis/lpx-toolkit/issues/2) Region→strip bridge · [#3](https://github.com/rhydlewis/lpx-toolkit/issues/3) Hidden-track flag · [#4](https://github.com/rhydlewis/lpx-toolkit/issues/4) UI track-row order

Investigation logs preserved below for reference.

### Deferred — investigation logs

#### #27 Detect summing/folder tracks (track groups) `[deferred 2026-04-30]`

**Already shipped via #35**: `kind: "summing-stack"` and `kind: "folder"` classification on the parent track. Text/JSON/HTML all surface this distinctly.

**Still missing**: per-child parent reference — which audio/MIDI tracks belong to which `Sub N`.

Investigation summary:
- Registry-record trailer byte at offset +4 (uint16 LE) varies per child (`0x02`, `0x07`, `0x0f`, etc.) but does NOT correlate with UI parent. All Sub 9 Guitars children should share a value but have different ones — likely a routing-history or audio-output-bus field, not a parent link.
- The 64-byte registry preamble is identical across siblings — no parent pointer there.
- Children aren't grouped contiguously in the registry by track_id (audio children are scattered through ids 9–1900; summing-stacks are 5000+).

**To resolve**: 2-3 sessions of OCuA channel-strip reverse-engineering. Each child track's output routes to the parent Sub's strip; that field is in the OCuA descriptor (24KB+ records, only a handful of fields decoded so far). The user-facing JTBD doesn't currently demand it, so deferred.

#### #28 Strict region→strip bridge `[partially solved 2026-04-30]`

**Audio strip mapping is solved.** Each registry record's post-name `uint16 LE` holds the channel-strip number for audio tracks. Wired up as `TrackEvidence.strip_id` / `RegionCluster.strip_id`. 100% accuracy on the 31 audio tracks in the busy-living test project.

**Per-track ID also solved**: each registry record is preceded by a 64-byte preamble carrying a uint16 LE track ID at bytes 2-3. Now exposed as `TrackEvidence.track_id`.

**Still open**: linking *region* records (`gRuA`) to their parent track. The strip number lives on the registry record, not the region — so for projects where the region count matters per channel strip we'd still need a region→registry bridge.

#### #31 Find hidden-track flag `[deferred]`

Ground-truth confirmed: control bytes in track-registry preamble are a track index, not visibility. `Strings` appears both visible AND hidden with identical `22 12 | 80 43`. Hidden flag must live in a separate track-list table not yet reverse-engineered. Reopen when (a) the canonical track list is found (task #28 territory), or (b) a different anchor surfaces.

#### #34 Find the UI track-order list

UI track-order list **STILL NOT FOUND** as of 2026-04-30 deeper investigation. Diff approach (Angle 5) attempted but blocked by plugin-state noise. Output now sorts by `track_id` (track-creation order) which is close-but-not-equal to UI order.

**Findings from the 2026-04-30 row-swap diff session:**

User produced a paired project: one before, one after swapping Piano (was row 12) ↔ E Piano (was row 13). Diff results:

- File grew by **48 bytes** total (consistent across all later registry records — they all shift by +48)
- 5,047,737 differing byte positions out of 7.13 M — i.e. 70% of the file
- Most diffs were **plugin-state re-serialisation noise** (Soundtoys EffectRack base64 strings of slightly different lengths, NSKeyedArchiver UID renumbering inside Smart Controls bplists)
- The single byte change in the registry preamble (Piano's preamble[0] flipped 0→1, Red Dialogue's flipped 1→0) turned out to be the **per-track focus byte**, not the order field — Red Dialogue had been the previously focused track
- Searches for adjacent Piano(2091)+E_Piano(2605) `track_id` pairs in any uint16 / uint32 LE encoding within 32 bytes returned **0 matches in either file**
- Decoded the 4 bplists whose content actually changed (vs just shifting): all were Smart Controls layouts, no track ordering

What's been ruled out:

- 4-byte / 8-byte LE arrays of offsets pointing into the registry block
- Flat arrays of `track_id` values in any direct uint16 or uint32 LE encoding
- All 225 NSKeyedArchive blobs in `ProjectData`
- `DisplayStateArchive` plist (only window/screenset state)
- Per-track focus byte (preamble[0]) — selected-track flag, not row order
- The cluster of 24 bplists near the registry block (per-region Metro/LoopFamily records)

**MINIMAL-TEST DIFF FINDINGS (2026-04-30):**

User produced a clean 2-track minimal pair (`LPX Test Original.logicx` Bass=row1/Synth=row2 vs `LPX Test Edited.logicx` Synth=row1/Bass=row2). Both files are Logic 12 (busy-living was created in Logic 11, saved in Logic 12).

**Initial hypothesis (CORRECTED)**: I thought the `\x04\x02\x07\x01` blocks at @250 and @950 were per-track row positions. They're **screensets** — Logic stores 2 by default. busy-living has the same blocks at the same offsets with values `3` and `7`, which can't be row positions for a 69-track project. The earlier claim was a coincidence — reordering tracks updates the screenset cursor positions as a side-effect.

**The real lead** is at file offsets ~7896-8100. ORIG has populated UUID-prefixed records there. EDIT has them **zeroed out** and replaced with sequential counters:

```
ORIG: 1e 00 00 00 00 00 00 00 [16-byte UUID]
ORIG: 21 00 00 00 00 00 00 00 [16-byte UUID]
EDIT: 19 00 00 00 04 00 00 00 [16 zero bytes]
EDIT: 19 00 00 00 08 00 00 00 [16 zero bytes]
EDIT: 19 00 00 00 0c 00 00 00 [16 zero bytes]
```

First uint32 fixed at `0x19` (type tag), second uint32 increments by 4. **This is the most likely candidate for the row-ordering structure**: when the user manually reordered, Logic replaced UUID-keyed entries with a sequential explicit list.

**Next investigation steps:**
1. Identify what type `0x19` records are (search for the same pattern across the busy-living project — should find ~69 of them if hypothesis holds)
2. Decode the original (non-zeroed) entries — figure out where the UUIDs key into for the track lookup
3. Verify on busy-living: 69 tracks → expect 69 of these records in some order
4. Test that this reconstructs the UI row order accurately

**0x19 hypothesis ALSO ruled out (2026-04-30 follow-up):** busy-living has only 1 run of 24-byte-stride `0x19` records (257 entries). Only 1 entry has a non-zero UUID; all others are zeroed. That's not a 69-track ordering — it's a sparse pre-allocated table with mostly empty slots. The 0x19 records in the test EDIT file may have been misleading.

**3-track minimal test (2026-04-30 follow-up #2):** User dragged Audio 3 from row 3 to row 2 in a clean Logic 12 minimal project. Findings:
- Registry preambles are byte-identical between ORIG and EDIT (focus byte too)
- +514 type-0x19 placeholder records (free-list expansion, not ordering — 514 slots for 3 tracks)
- +12 type-0x17 records in the score-editor (karT) region (event-sequence allocation)
- +2 Smart Controls bplists (Logic auto-creates on track click)
- 0 matches for any flat ordered array of track_ids (uint16/uint32, LE/BE)

**#34 is officially deferred** until a fundamentally new angle surfaces. Cluster-based ordering (track-id sort) ships as the working approximation.

Other angles still untried (from earlier sessions):

1. Enumerate every NSKeyedArchive `$classname` across all 225 blobs — look for `TracksAreaTrackList`, `TrackListOrdering`, or similar named class
2. The 18 `_WsChannelUUID` records already extracted are tied to Smart Controls — their UUIDs might appear in some other ordered list
3. Inspect the `ivnE` ("Environment") records (103 occurrences) — possible track-routing topology including display order
4. Systematically scan the 4.7 M byte range between OCuA (~1.4 M) and the registry (~6.1 M)

---

## Completed

#### #15 CLAUDE.md Out of scope + read-only test ✓

Added explicit Out-of-scope section to CLAUDE.md (no write-back, no GUI, no AX automation, no cross-DAW, no VST, no SaaS upload, no smart-guessing). Added a hard test (`tests/test_readonly_invariant.py`) asserting `parse_project` leaves bundle bytes/mtime unchanged.

#### #16 Find region→strip bridge field `[Bet 1]` ✓

Closed via cluster-based approximation. Each registry record maps to one Logic track (verified empirically). The strict bridge field remains unidentified — moved to #28 as deferred.

#### #29 Quick win: extract track-header records (70 03 01 00) ✓

Added `find_track_header_records()` to pick up MIDI/instrument track names that gRuA misses. Filtered Logic-internal noise (`*Automation`, `RBA Sequence`, `Untitled`, `Track Alternatives`, `MIDI Region`, `TRASH`).

#### #30 Track registry extractor (signature whitelist) ✓

`find_track_registry_records()` uses generalised 16-byte preamble pattern with signature whitelist. Six MIDI/instrument signatures, six sub/folder signatures, four bus signatures filtered out. Lifted coverage from 26 → 66 unique track names.

#### #32 Track type column in TRACK LIST output ✓

`TrackEvidence` NamedTuple propagates `kind` (audio/midi/folder/unknown) from each extractor through `RegionCluster`. Conflict resolution: prefer concrete audio/midi over generic folder.

#### #33 Stop deduping by name — use registry records as authoritative track count ✓

`tracks_from_evidence()` replaces name-collapsing `tracks_from_regions()` in the main pipeline. One entry per registry record; gRuA region counts attach to the first matching name. Output now matches Logic's actual track count exactly (69 in busy-living test project).

#### #17 JSON output mode `[Bet 2a]` ✓

`--json` flag emits structured project data via `project_to_json()`. Schema versioned (`schema_version: 1`) with stable top-level keys: `project` (metadata + dates), `tracks` (per-strip plugin chain), `vendors` (manufacturer 4CC → count). Pipes cleanly into `jq` / `python -c "import json,sys; ..."`. 7 schema-locking tests in `tests/test_json_output.py`.

#### #23 Vendor rollup ✓

Closed by #17 — `vendors` is a top-level field in the JSON output. Standalone CLI display can be added later if needed.

#### #18 Auval cache layer with mtime invalidation ✓

`auval_lookup_cached()` reads/writes `~/.cache/lpx-toolkit/auval.json`. Invalidates when `/Library/Audio/Plug-Ins/Components/` mtime advances. `main()` uses this in place of `auval_lookup()`. 8 tests in `tests/test_auval_cache.py` covering: round-trip, missing/corrupt cache, fresh-cache hit, mtime-stale refresh, cold start, auval-unavailable degradation, and the default cache path location (outside the project bundle so the read-only contract is preserved).

#### #19 Cross-project rollup `--rollup` ✓

`rollup_projects()` parses each path (skipping bad ones with a stderr warning), reuses `project_to_json()` per project, and `aggregate_rollup()` produces the final shape: per-project summaries, fingerprints (count of projects each plugin appears in), vendors (manufacturer 4CC → total plugin count). CLI: `--rollup` flag followed by N `.logicx` paths. Verified on the Logic test projects — `EZkeys 2` correctly identified as appearing in 2 of 3 sampled projects. 4 tests in `tests/test_rollup.py`.

#### #36 Replace ad-hoc arg parsing with argparse ✓

`build_parser()` returns an `argparse.ArgumentParser`; `cli(argv=None)` is the entry point that dispatches between inspect/rollup modes and validates path requirements. `__version__` is the single source of truth for `--version` / `-v`. `--help` / `-h` auto-handled. Unknown flags now produce argparse-style errors (`unrecognized arguments: --bogus`) instead of crashing with `StopIteration` deep in `main()`. 11 tests in `tests/test_cli.py`. Unblocks distribution work (#21).

#### #26 Klopfgeist default filter ✓

Defensive filter for Logic's built-in metronome (`aumu/klop/appl`). The current parser doesn't actually surface Apple built-in AUs (manufacturer `appl`) — Logic stores them differently from third-party plugins — but `is_metronome_au()` and `filter_metronome()` are in place against future format changes. 5 tests.

#### #24 Extended metadata ✓

`ProjectInfo` gained `sample_rate`, `bundle_size_bytes`, `audio_file_count`, `impulse_response_count`, `frame_rate_index`. JSON output exposes all + a decoded `frame_rate` (24/25/29.97/30 fps via `frame_rate_for_index()`). Text output adds Sample rate, Frame rate, Bundle size, Audio files lines. 7 tests in `tests/test_extended_metadata.py`.

#### #25 Diagnostics warnings ✓

`diagnose_project()` returns a list of warning dicts. Three kinds emitted: `unresolved_plugin` (no auval match), `duplicate_consecutive_fx` (same plugin twice in a row on a strip), `truncated_name` (11-char binary name + longer auval-resolved). Surfaced in JSON as top-level `diagnostics` array and in text under `=== DIAGNOSTICS ===`. The busy-living project surfaces 36 truncations on guitar strips (CLA Guitars → CLA Guitars (m->s)), validating the truncation detector. 7 tests.

#### #22 Phantom plugin distinction ✓

`find_phantom_aus()` returns AUs in `ProjectData` that aren't attached to any active user track — sources include undo history, deleted tracks, alternative takes. Deduped by fingerprint; the metronome is filtered by default; `include_metronome=True` overrides. JSON exposes as top-level `phantom_plugins` array; text under `=== PHANTOM PLUGINS ===`. 6 tests in `tests/test_phantom_plugins.py`. Inspector mockup highlights this as a key differentiator for "is this project clean?".

#### #20 Rich HTML dashboard output ✓

`--html` flag generates a self-contained HTML dashboard styled to match `inspector-mockup.html` and opens it in the macOS default browser via `open`. `render_project_html()` consumes the JSON payload (single source of truth for all data sections). HTML lands in `tempfile.gettempdir()` named `lpx-toolkit-<slug>.html`. Pixel-faithful palette: dark theme, Fraunces italic display + IBM Plex Mono via Google Fonts, amber/phosphor accents, project metadata sheet, tracks table with FX chains, vendor rollup bar chart, phantom plugin grid (when present), diagnostics warning blocks. Verified on busy-living: 56 tracks, 7 vendors, 36 diagnostics rendered correctly. 12 tests in `tests/test_html_output.py` covering structure, escaping, every section. Rollup HTML deferred to a follow-up.

#### #28 Strict region→strip bridge ✓ (audio strip mapping)

Audio-track registry records encode the channel-strip number in the post-name `uint16 LE`. Wired up via `_decode_audio_strip_id()` and surfaced as `TrackEvidence.strip_id` / `RegionCluster.strip_id`. 100% accuracy on the 31 audio tracks in the busy-living test project. Still open: region→strip bridge (different problem — the strip number lives on the registry record, not the region).

#### #28b Per-track ID extraction ✓ (new finding 2026-04-30)

Each registry record has a 64-byte preamble whose bytes 2-3 are a uint16 LE per-track ID. Exposed as `TrackEvidence.track_id` / `RegionCluster.track_id`. Stable, globally unique within a project. Track-list output now sorts by this ID for stable ordering close to UI order (but not exactly).

#### #37 Add --serve mode (local HTTP server for browsing + rollup) ✓

`lpxtool --serve [DIR]` (default `~/Music/Logic`) binds a `ThreadingHTTPServer` to `127.0.0.1` on a free port (overridable via `--port`) and opens the index in the browser. Five routes:
  - `GET /` — HTML index of `.logicx` bundles in DIR (theme-toggleable, reuses `_HTML_STYLE`)
  - `GET /project/<idx>` — full HTML dashboard via `render_project_html`
  - `GET /api/projects` — JSON list with `{index, name, path}`
  - `GET /api/projects/<idx>` — full JSON payload via `project_to_json`
  - `GET /api/rollup` — aggregated rollup JSON across the directory

`_list_projects()` is non-recursive and ignores anything that isn't a `.logicx` directory. Read-only contract preserved — every route only reads from the bundle (verified live against 111 projects in `~/Music/Logic`). 18 tests in `tests/test_serve.py` cover unit (project listing, index render) + integration (real HTTP server in a daemon thread, every route asserted including 404s).

#### #39 Add lpxtool.png screenshot to README ✓

Embedded the existing `lpxtool.png` in the README directly under the warning callout, so a visitor sees the HTML dashboard before the text-mode sample output. Relative path renders on GitHub.

#### #40 Light/dark mode toggle for HTML dashboard ✓

`_HTML_STYLE` now defines a light palette under `:root[data-theme="light"]` (warm-paper background, dark ink, slightly darker accents to hold contrast). `render_project_html()` adds: (a) inline boot script in `<head>` that reads `localStorage["lpxtool-theme"]` and applies the attribute before body paint (no flash); (b) fixed top-right toggle button (◐) that flips the attribute and persists the choice. Smooth colour transitions on body/sheet/track surfaces. 4 new tests in `tests/test_html_output.py` lock the toggle markup, light-palette presence, localStorage persistence, and head-block boot order.

#### #21 Homebrew tap + PyPI packaging ✓

**PyPI** (live at https://pypi.org/project/lpx-toolkit/). Three releases shipped in one day:
- `0.1.0` — debut release
- `0.1.1` — added `lpx-toolkit` console script alongside `lpxtool` so `uvx lpx-toolkit ...` resolves without `--from`
- `0.1.2` — friendly errors for non-bundle paths (`uvx lpx-toolkit .` from inside `~/Music/Logic` was crashing with StopIteration; now suggests `--rollup`)

**Homebrew tap** (live at https://github.com/rhydlewis/homebrew-tap). `Formula/lpxtool.rb` wraps the PyPI sdist via `Language::Python::Virtualenv` and ships both `lpx-toolkit` and `lpxtool` console scripts. Verified end-to-end: `brew install rhydlewis/tap/lpxtool` → both binaries on PATH → real-project parse from a brew-installed wheel. README's install section now shows uvx (primary), pipx, brew, and pip-in-venv.

Bumping the formula on each PyPI release: edit `url` + `sha256` in `Formula/lpxtool.rb`. The README in `homebrew-tap` documents the one-liner that pulls them from PyPI's JSON API.

#### #38 GitHub Actions CI (pytest on push/PR) ✓

`.github/workflows/test.yml` runs `pytest` on every push to main and every PR against Python 3.10 / 3.11 / 3.12 / 3.13 on `macos-latest`. README carries a tests-status badge. CI caught a real Python 3.10/3.11 incompatibility (f-string with `\"` inside an expression — only legal from 3.12 per PEP 701) on its first run, fixed in a follow-up commit. All four matrix jobs green.

#### #35 Distinguish Folder Stack / Summing Stack / Aux Stack ✓

Trailer-pattern discriminator: a Summing Stack carries `XX 01 00 NN 00 01` immediately after its name (where `XX` ≈ `0x54 + sub_number`, `NN` is the Sub number). When this matches, kind is upgraded to `summing-stack` regardless of signature — so `Backline` and `Guitars` (which use signature `23 12` shared with regular audio tracks) are now correctly classified. `_is_summing_stack_trailer()` accepts a leading null between name and trailer for records that emit one (e.g. Guitars). Aux-based Track Stacks (`Atmosphere (Millenniums)`) and their children stay as the generic `folder` — distinguishing them further is left as a follow-up. CLAUDE.md "Summing Stack detection" section documents the format.

---

## How to use this file

- **Session start**: read this file, mirror pending tasks into the harness's tracker via `TaskCreate` if the session needs that level of structure.
- **Status changes**: when a task lands a commit, move it from Pending to Completed with a one-line summary of the outcome.
- **New tasks**: append under Pending with a short description.
- **Don't duplicate work**: if a task is in progress in the harness tracker, it's also tracked here — keep them in sync.

The numeric IDs (#15, #16, …) come from the harness's tracker and may not be sequential after pruning. Treat them as stable references.
