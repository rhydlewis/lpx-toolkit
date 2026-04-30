# Contributing to lpx-toolkit

Thanks for taking a look. This document covers what you need to know to work on the code. For the deep reverse-engineering notes on the `.logicx` binary format, see `CLAUDE.md`.

## Development setup

```sh
git clone https://github.com/rhydlewis/lpx-toolkit.git
cd lpx-toolkit
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

The runtime parser is stdlib-only — `pytest` is the only dev dependency.

During development you can run the tool either via the installed entry point or as a script:

```sh
.venv/bin/lpxtool ~/Music/Logic/SomeProject.logicx
# or
.venv/bin/python lpx_inspect.py ~/Music/Logic/SomeProject.logicx
```

## Running tests

```sh
.venv/bin/pytest
```

To run the integration tests against a real Logic project, point `LPX_TEST_PROJECT` at it:

```sh
LPX_TEST_PROJECT=~/Music/Logic/SomeProject.logicx .venv/bin/pytest
```

Run a single test file or test by name during iteration:

```sh
.venv/bin/pytest tests/test_auval_parser.py -x
.venv/bin/pytest tests/test_auval_parser.py::test_preserves_trailing_spaces_in_subtype -x
```

## Development principles

- **TDD is non-negotiable.** Write a failing test first, then the minimum code to make it pass. See the *Testing* section in `CLAUDE.md` for the RED-GREEN-REFACTOR workflow this project uses.
- **Read-only contract.** This tool MUST NOT write to anything inside a `.logicx` bundle, ever. `tests/test_readonly_invariant.py` enforces this with a SHA-256 + mtime snapshot — do not weaken or skip it.
- **Stdlib-only at runtime.** Pure-Python, no external runtime deps. `auval` is the only outside command, and only when running on macOS.
- **Charity about format variation.** Logic's project format has shifted across versions. Prefer "skip and continue with a warning" over raising on unexpected bytes.

The full coding conventions, code style, and out-of-scope list are in `CLAUDE.md`.

## Where help is wanted

Several pieces of the `.logicx` format are still partially understood. Each of these has had multiple investigation rounds — see `CLAUDE.md` for what's already been tried so you don't duplicate dead ends:

- **UI track-row order.** We can extract every track and its stable per-project ID, but the user-visible row order is encoded somewhere we haven't pinned down. Cluster-based ordering ships as the working approximation.
- **Hidden-track flag.** Hidden tracks come back from the registry like any other; the flag distinguishing them is somewhere outside the registry record itself.
- **Region → channel-strip mapping.** Audio regions carry a track name but no direct strip number. We approximate by clustering and name-matching against default strip names.

Ground-truth-driven diffs (a tiny project saved twice with one specific change) are the most productive way to attack any of these.

## Reporting issues

If `lpxtool` fails on a project of yours and you can share it (or a sanitised copy), include:

- The exact `lpxtool` command you ran
- The full error output
- The Logic version that created/last-saved the project, if you know it

If you can't share the project, the diagnostic JSON from `lpxtool --json` (with audio paths redacted) is often enough to triage.
