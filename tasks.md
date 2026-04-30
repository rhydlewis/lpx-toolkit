# Tasks

Cross-session persistence of the in-flight task list. Mirror this back into the harness's task tracker at session start with TaskCreate; sync any status changes here when commits land.

Priority ordering follows `pm-feedback.md` (Bet 1 → Bet 2 → Bet 3) and the user's confirmed list. Tasks that are deferred or blocked are clearly tagged.

---

## Pending

### Bet 2 — output composability (next up)

#### #17 JSON output mode `[Bet 2a]`

`--json` flag emits structured project data (tracks, plugins, metadata) for piping into other tools. Same internal model the HTML dashboard will consume. Schema must include: project metadata, per-track strip + plugin chain, phantom plugins, vendor rollup, diagnostics.

#### #18 Auval cache layer with mtime invalidation `[Bet 2b]`

Cache parsed `auval -l` table at `~/.cache/lpx-toolkit/auval.json`. Invalidate when `/Library/Audio/Plug-Ins/Components/` mtime advances. Eliminates the 5–30s cold start that dominates batch-use UX.

### Bet 3 — cross-project use

#### #19 Cross-project rollup `--rollup` `[Bet 3]`

`lpx-inspect ~/Music/Logic/**/*.logicx --rollup` aggregates plugin usage across many projects. Answers "which installed plugins do I actually use?". Depends on JSON output (#17) and cache (#18).

### Distribution + UX polish

#### #20 Rich HTML dashboard output

`--html` flag emits self-contained HTML using `inspector-mockup.html` as the design reference. Consumes the same internal model as JSON to avoid drift. Defer until Bets 1+2 land.

#### #21 Homebrew tap + PyPI packaging

Package as installable CLI: `pyproject.toml` `[project.scripts]` entry point, PyPI release workflow, then a Homebrew tap formula. PM identifies Homebrew as "strongest distribution play within the Logic community."

### Feature additions

#### #22 Phantom plugin distinction

Separate plugins on active tracks vs orphans (undo history, deleted tracks). Surface as a dedicated section per the inspector-mockup design. Makes "is this project clean?" a single-glance answer.

#### #23 Vendor rollup

Count plugins per manufacturer 4CC. Per-project summary feeds into the cross-project `--rollup`. Trivial once auval cache is keyed by manufacturer.

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

UI track-order list **STILL NOT FOUND** as of 2026-04-30 deeper investigation. Output now sorts by `track_id` (track-creation order) which is close-but-not-equal to UI order — the user's manual reordering is stored separately.

What's been ruled out:

- 4-byte / 8-byte LE arrays of offsets pointing into the registry block (`6111929`–`6186949`)
- 4-byte / 8-byte LE arrays of `track_id` values in any direct encoding (uint16 LE or uint32 LE) — search for the UI prefix `[5203, 9, 1677, 73, 5331, 2477, 2155]` returned 0 hits
- All 225 NSKeyedArchive blobs in `ProjectData` — none has a top-level array of size 50-80 except `scalingGraph` (automation) and one of size 64 (parameter mappings)
- `DisplayStateArchive` plist — only window/screenset state, no track-list ordering
- The cluster of 24 bplists near the registry block — they're per-region Metro/LoopFamily records, not per-track ordering

Next angles for whoever picks this up:

1. Enumerate every NSKeyedArchive `$classname` across all 225 blobs — look for `TracksAreaTrackList`, `TrackListOrdering`, or similar named class. Currently we filter by class on extraction; a complete inventory might surface a track-list class we missed.
2. The 18 `_WsChannelUUID` records already extracted are tied to Smart Controls — but their UUIDs might appear in some other ordered list.
3. Inspect the `ivnE` ("Environment") records (103 occurrences). They might carry track-routing topology including display order.
4. Test whether the project file at offsets *between* `OCuA` (~1.4 M) and the track registry (~6.1 M) contains a track-list-like structure. That's a 4.7 M byte range we haven't systematically searched.

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
