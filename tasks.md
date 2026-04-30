# Tasks

Cross-session persistence of the in-flight task list. Mirror this back into the harness's task tracker at session start with TaskCreate; sync any status changes here when commits land.

Priority ordering follows `pm-feedback.md` (Bet 1 → Bet 2 → Bet 3) and the user's confirmed list. Tasks that are deferred or blocked are clearly tagged.

---

## Pending

### Distribution + UX polish

#### #20 Rich HTML dashboard output

`--html` flag emits self-contained HTML using `inspector-mockup.html` as the design reference. Consumes the same internal model as JSON to avoid drift. Defer until Bets 1+2 land.

#### #21 Homebrew tap + PyPI packaging

Package as installable CLI: `pyproject.toml` `[project.scripts]` entry point, PyPI release workflow, then a Homebrew tap formula. PM identifies Homebrew as "strongest distribution play within the Logic community."

### Feature additions

#### #22 Phantom plugin distinction

Separate plugins on active tracks vs orphans (undo history, deleted tracks). Surface as a dedicated section per the inspector-mockup design. Makes "is this project clean?" a single-glance answer.

#### #24 Extended metadata

Surface sample rate, bundle size, region count, project length, frame rate index. Sample rate is already in `MetaData.plist` (`SampleRate`), region count is `len(unique_track_names())`, size is `os.stat`, length needs computing from event sequence end.

#### #25 Diagnostics warnings

Surface unresolved 4CCs (no auval match), duplicate consecutive FX on same strip, name-truncation flags. Useful "is this safe to open" check before booting Logic.

#### #26 Klopfgeist default filter

Hide Logic's metronome AU from active-plugin lists by default; expose `--include-metronome` flag for users who want it. CLAUDE.md flags this; mockup also shows it as a phantom by convention.

#### #27 Detect summing/folder tracks (track groups)

Logic's Track Stacks (summing stacks, folder stacks) group child tracks under a parent. Extract the parent→child relationships and surface them in the tracks output (indent or grouped row). Format reverse-engineering needed.

### Reverse-engineering follow-ups (deferred — need new evidence)

#### #28 Strict region→strip bridge `[partially solved 2026-04-30]`

**Audio strip mapping is solved.** Each registry record's post-name `uint16 LE` holds the channel-strip number for audio tracks. Wired up as `TrackEvidence.strip_id` / `RegionCluster.strip_id`. 100% accuracy on the 31 audio tracks in the busy-living test project (Andy & Red→1, Audio 3→3, Slide GTR→19, Audio 27→27, etc.).

**Per-track ID also solved**: each registry record is preceded by a 64-byte preamble carrying a uint16 LE track ID at bytes 2-3. Now exposed as `TrackEvidence.track_id`.

**Still open**: linking *region* records (`gRuA`) to their parent track. The strip number lives on the registry record, not the region — so for projects where the region count matters per channel strip we'd still need a region→registry bridge. The original deferred work for region UUID lookups remains.

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

#### #28 Strict region→strip bridge ✓ (audio strip mapping)

Audio-track registry records encode the channel-strip number in the post-name `uint16 LE`. Wired up via `_decode_audio_strip_id()` and surfaced as `TrackEvidence.strip_id` / `RegionCluster.strip_id`. 100% accuracy on the 31 audio tracks in the busy-living test project. Still open: region→strip bridge (different problem — the strip number lives on the registry record, not the region).

#### #28b Per-track ID extraction ✓ (new finding 2026-04-30)

Each registry record has a 64-byte preamble whose bytes 2-3 are a uint16 LE per-track ID. Exposed as `TrackEvidence.track_id` / `RegionCluster.track_id`. Stable, globally unique within a project. Track-list output now sorts by this ID for stable ordering close to UI order (but not exactly).

#### #35 Distinguish Folder Stack / Summing Stack / Aux Stack ✓

Trailer-pattern discriminator: a Summing Stack carries `XX 01 00 NN 00 01` immediately after its name (where `XX` ≈ `0x54 + sub_number`, `NN` is the Sub number). When this matches, kind is upgraded to `summing-stack` regardless of signature — so `Backline` and `Guitars` (which use signature `23 12` shared with regular audio tracks) are now correctly classified. `_is_summing_stack_trailer()` accepts a leading null between name and trailer for records that emit one (e.g. Guitars). Aux-based Track Stacks (`Atmosphere (Millenniums)`) and their children stay as the generic `folder` — distinguishing them further is left as a follow-up. CLAUDE.md "Summing Stack detection" section documents the format.

---

## How to use this file

- **Session start**: read this file, mirror pending tasks into the harness's tracker via `TaskCreate` if the session needs that level of structure.
- **Status changes**: when a task lands a commit, move it from Pending to Completed with a one-line summary of the outcome.
- **New tasks**: append under Pending with a short description.
- **Don't duplicate work**: if a task is in progress in the harness tracker, it's also tracked here — keep them in sync.

The numeric IDs (#15, #16, …) come from the harness's tracker and may not be sequential after pruning. Treat them as stable references.
