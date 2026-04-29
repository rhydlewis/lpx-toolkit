# PM Feedback: lpx-toolkit

**Author**: Alex (PM)
**Date**: 2026-04-29
**Status**: Strategic assessment — input for next-quarter prioritisation

---

## TL;DR

`lpx-toolkit` solves a small, sharp problem better than any existing alternative: *"What plugins does this Logic project depend on, without opening Logic?"* The current scope is the right MVP. The single highest-leverage next bet is parsing `NSKeyedArchive` blobs to deliver track-to-plugin mapping — that turns a useful manifest into the answer to the question users actually ask. Everything else is a distraction until that lands.

---

## 1. Who benefits, and how

Concrete segments where the pain is real today:

- **Project archaeologists** reopening a 2019 session on a 2026 machine. Before opening it, they want to know: *"Which of these plugins do I still own / still have installed / still have a licence for?"* Today they open the project, wait 90 seconds for missing-plugin dialogs to stack up, and click through them one by one. `lpx-toolkit` answers that in under 2 seconds, offline.
- **Collaborators receiving a `.zip` from a co-producer**. Before paying for a Slate or Soundtoys bundle, they want to scope the dependency list. Today, the only way to find out is to open the project — which fails ungracefully when plugins are missing.
- **Plugin auditors before a system migration / clean reinstall**. "Across my 200 active projects, which plugins do I actually still use?" Right now this is unanswerable without scripted batch-opening of every project. `lpx-toolkit` is a one-line shell loop.
- **Music educators and course creators** who need to publish "plugins required for this course's project files" without booting Logic for each lesson.
- **Archivists / labels / studios** doing format-stable preservation of session metadata. The `.logicx` format isn't documented; this tool is the closest thing to a public schema for what it contains.
- **Sample library / preset / template creators** validating the dependency surface of a template they're about to ship — "did I leave a stray third-party AU on the kick bus?"
- **Backup / sync power users** wanting to detect which projects are "at risk" if a given plugin is uninstalled.

The common thread: **Logic offers no inventory view of project dependencies**. Every one of these users currently solves it by opening Logic, which is slow, surfaces irrelevant errors, and doesn't scale.

## 2. Jobs-to-be-done

> *"When I'm about to open or hand off a Logic project, help me understand what it depends on — fast, offline, and without booting a DAW that may complain about missing plugins."*

Two adjacent JTBD that current scope partially serves:

> *"When I'm cleaning up my plugin library, help me see which plugins are actually used across my projects — not just installed."*

> *"When I'm receiving someone else's project, help me know what I need installed before I open it."*

These three jobs share a property no existing tool offers: **read-only, offline, batch-friendly inspection of an opaque format**. That's the wedge.

## 3. Differentiation

| Alternative | Why `lpx-toolkit` wins |
|---|---|
| Open Logic Pro | 30–90 sec per project; pops modal dialogs for missing plugins; can't be batched. |
| AppleScript / `logic-automator` (AX automation) | Requires Logic to launch; coupled to UI; brittle to Logic UI changes; can't run on a server or in CI. |
| `auval -a` | Probes the system, not the project. Also segfaults on duplicate Waves frameworks (per CLAUDE.md). |
| Commercial DAW migration tools (AATranslator etc.) | Cross-DAW conversion, not introspection. Closed-source, paid, Windows-leaning. |
| `strings ProjectData \| grep` | Misses 4CC structure; produces noise; can't resolve manufacturer/subtype. |

The defensible properties: **offline**, **read-only** (cannot corrupt), **stdlib-only** (trivial to install / vendor / sandbox), **batch-friendly** (folder of 200 projects, single shell loop). None of the alternatives have all four.

## 4. Highest-leverage next bets

Ranked by user-value-per-engineering-week. Be disciplined: do these in order.

### Bet 1: Track → plugin mapping via NSKeyedArchive parsing

This is the headline feature. Users don't ask *"what plugins are referenced anywhere in this project?"* — they ask *"what's on the kick track?"* The current output is a flat manifest plus heuristic "nearest preceding track" assignment in `assign_aus`; the authoritative mapping lives in the `bplist00` blobs the codebase has already started decoding (`extract_bplists`, `resolve_archive`).

Output shape that unlocks new use-cases:

```
Track 03: Drums (instrument)
  Instrument: EZdrummer 3
  Audio FX:   ChannelEQ → Compressor → UADx Galaxy
Track 04: Bass DI (audio)
  Audio FX:   Pro-Q 4 → Decapitator
```

Once you have this, every adjacent feature becomes possible: a "plugin removal impact report", a "project diff" between alternatives, a "what would break if I sold my Soundtoys licence" view.

CLAUDE.md is honest that NSKeyedArchive enrichment supplements but doesn't replace binary parsing for routing — keep that boundary explicit. The win here is *plugin slot order per track*, not rewriting the binary parser.

### Bet 2: JSON output + cache layer

Two small things that disproportionately enable downstream tooling:

- `--json` output makes the tool composable. Today it's a CLI prints-to-stdout; with JSON it becomes a primitive other tools can build on (a `fzf`-driven plugin browser, a Raycast extension, a CI check).
- Cache the parsed `auval -l` table at `~/.cache/lpx-toolkit/auval.json` with mtime invalidation against `/Library/Audio/Plug-Ins/Components/`. CLAUDE.md already flags this; the 5–30 second cold start is the single biggest UX papercut for batch use-cases.

Together these turn a one-project tool into something usable across a project library.

### Bet 3: Cross-project rollup ("plugin usage census")

Once Bet 2 lands, a `lpx-inspect ~/Music/Logic/**/*.logicx --rollup` view is essentially free and answers a real question: *"Which of my installed plugins do I actually use?"* This is the migration / decluttering use-case, and it's the one most likely to get organic word-of-mouth in the Logic community because it solves a problem people complain about publicly.

I'd defer everything else (Alchemy/Quick Sampler sample paths, SwiftUI inspector panel, Markdown export) until these three ship and we have signal on usage patterns.

## 5. Risks / non-goals — stay disciplined

These are tempting and would each dilute the value proposition:

- **Writing back to `ProjectData`.** Permanent, irrecoverable user data loss is one bad commit away. CLAUDE.md correctly forbids it. The undocumented format makes write support a liability, not a feature.
- **GUI / SwiftUI app.** A GUI is a 5x scope expansion for marginal user value over a CLI + JSON output. If anyone wants a GUI, they can wrap the library. Don't build it in this repo.
- **Live mixer state / Logic automation.** Different tool, different paradigm (AX automation, Logic must be running). Belongs in a separate module so the offline-first promise stays intact.
- **Cross-DAW support (Pro Tools, Ableton, Cubase).** Each format is a multi-month reverse-engineering project of its own. The tool's value is *depth* in `.logicx`, not *breadth* across DAWs. AATranslator already plays in that space and lost its way.
- **VST/VST3 support.** Logic doesn't load VSTs. Adding a VST scanner is solving a problem the target user doesn't have.
- **"Smart" features that guess at user intent.** The tool's credibility comes from being a faithful reporter of what's in the file. Filtering Klopfgeist by default is fine; hiding "phantom" plugins from undo history is not — those are real entries and surfacing them is a feature for some users.

## 6. Monetisation / distribution — be skeptical

Honest read: **this is a free open-source CLI, and that's probably the right answer indefinitely.** The total addressable audience (Logic Pro users who care about offline project introspection) is in the low tens of thousands. The willingness-to-pay for a CLI utility in that segment is near-zero. Trying to monetise the core would kill adoption and the goodwill that makes the project valuable.

Plausible-but-skeptical adjacent paths, in descending order of viability:

1. **Homebrew formula + Mac App Store companion app (paid, cheap).** A $4.99 GUI wrapper for non-technical Logic users — produced engineers, composers — who'd never run a CLI but would pay for "show me what's in this project". Realistic ceiling: a few hundred dollars/month. Not a business; a tip jar with a UI.
2. **Studio / education site licences.** If the cross-project rollup (Bet 3) becomes useful enough for asset-management workflows at studios or music schools, there might be a small B2B angle. Don't build for it speculatively.
3. **Sponsorship from a plugin or sample-library vendor** who benefits from "users discover they're using our plugin". Possible but scope creep risk — you'd start building features for the sponsor, not users.

What I would **not** pursue: SaaS upload-your-project service (privacy-hostile, undifferentiated), plugin marketplace integration (chases the wrong customer), Patreon (audience too small).

The strongest distribution play is **credibility within the Logic community**: a clear README, a blog post on the format reverse-engineering, and a Homebrew formula. The tool's value compounds as more people cite it; capturing that as revenue is a separate, lower-priority question.

---

## Recommendation

**Build Bet 1 (track→plugin mapping) next quarter.** It's the difference between "interesting hack" and "the tool every Logic power user keeps in their pipeline." Bets 2 and 3 are sequencing decisions for the same quarter if Bet 1 ships cleanly.

Hold the line on the non-goals — especially write support and GUI. The current scope is doing the right thing; the risk is feature-creep, not feature-poverty.
