---
name: tdd-loop
description: >-
  Run a vertical-slice TDD loop (red → green → refactor) with this repo’s
  unittest suite. Use when the user asks for TDD, test-first work, red-green-refactor,
  a failing test then implementation, or /tdd-loop; also when adding or changing
  behavior in the Rekordbox WAV converter.
icon: beaker
color: green
---

# TDD loop (this repository)

Drive **one behavior at a time**: failing test → minimal production code → cleanup under green tests. Repeat until the request is done.

Do not write production code before a failing test. Do not write a batch of tests and then a batch of implementation.

## When to use

- User asks for TDD, test-first, red-green-refactor, or `/tdd-loop`
- New behavior, a bug fix, or a regression test for the CLI, GUI, XML, or conversion pipeline
- Existing tests fail and the work is to make them pass for the right reason

Skip this skill for docs-only edits, packaging/build scripts with no testable Python behavior, or exploratory questions that do not change code.

## Repo facts

| Item | Value |
| --- | --- |
| Runner | `python3 -m unittest discover -s src/tests -v` (CI uses the same discover path) |
| Tests | `src/tests/test_*.py` — `unittest.TestCase`, Given-When-Then in method names or docstrings |
| Conversion / XML | `src/rb_playlist_to_wav.py` |
| Tk GUI | `src/rb_converter_gui.py` |
| Shared help | `src/usage_guide.py` |
| Version | `src/version.py` |
| CLI launcher | `rb-converter.py` (keep thin; do not duplicate conversion logic) |

Intended behavior also lives in `README.md` and `USAGE.md`. Conversion **must not** mutate Rekordbox originals; it writes new WAVs and a separate import XML.

Optional phase subagents (if present): `.cursor/agents/tdd-red.md`, `tdd-green.md`, `tdd-refactor.md`. You may follow those prompts; this skill still owns the loop.

## Test seams (prefer these)

Test public behavior, not private helpers:

- Playlist XML parse, playlist selection, import-XML write (`rb_playlist_to_wav`)
- Conversion stats and skip/copy/convert outcomes (including GUI helpers that wrap those stats)
- Path / filename collision handling, NFC/NFD resolution
- Version string shown in the GUI
- CLI argument / prompt behavior only when it is already covered by existing tests

Avoid asserting mock call counts on ffmpeg internals unless the bug is specifically about the ffmpeg invocation. Prefer outcomes: files written, XML attributes, stats, error messages.

## Rules of the loop

1. **Red before green.** A new failing assertion exists before production edits.
2. **One vertical slice.** One behavior, one (or a tight pair of) tests, then the smallest code that passes it.
3. **Fail for the right reason.** RED means the assertion failed, not ImportError/SyntaxError from stubs you invented in production files.
4. **No horizontal slicing.** Do not dump a full test file of future cases and then implement everything.
5. **No tautologies.** Expected values come from the spec, fixtures, or a known-good literal — not from re-running the production formula in the test.
6. **No implementation-coupled tests.** If renaming a private function would fail the test with unchanged behavior, the test is wrong.
7. **Baby steps in GREEN.** Hardcoded returns and simple branches are fine until a later slice forces generalization.
8. **Refactor only while green.** Cleanup after the slice passes; do not mix new behavior into refactor.
9. **Stay in scope.** No extra codecs, Rekordbox APIs, or features the current slice does not require.

## Loop

For each remaining behavior:

### 0. Name the slice

Write one sentence: the observable behavior and the seam you will test. If the request is several behaviors, queue them; do not start the next until this slice is green and cleaned up.

### 1. RED — failing test only

- Edit only `src/tests/test_*.py` (add a new test module if the behavior does not belong in an existing file).
- Match style in `src/tests/test_rb_playlist_to_wav.py`, `test_rb_converter_gui.py`, `test_version_display.py`.
- Run the new test first, then discover:

```bash
python3 -m unittest src.tests.<module> -v
python3 -m unittest discover -s src/tests -v
```

- Stop RED when you have a single intended assertion failure (or a small, related set if they are the same behavior). If the suite is already red for unrelated reasons, fix or isolate that first; do not pile a new slice on a dirty suite.

### 2. GREEN — minimal production code

- Change the smallest production surface that makes the new test pass (`src/rb_playlist_to_wav.py`, `src/rb_converter_gui.py`, `src/usage_guide.py`, `src/version.py`, or the thin launcher only if the slice is launch behavior).
- Re-run discover. Iterate on production code until green.
- If GREEN needs more cases, go back to RED for **one** additional test — do not bulk-add coverage.

### 3. REFACTOR — cleanup under green

- Remove duplication, unclear names, and accidental complexity introduced in GREEN.
- Do not change behavior. Re-run discover after refactor. If anything goes red, fix or revert the refactor before the next slice.

### 4. Next slice or done

- More requested behavior → repeat from step 0.
- Request complete → run discover once more; report which tests were added and what production files changed.

## Commands

Targeted (faster while iterating):

```bash
python3 -m unittest src.tests.test_rb_playlist_to_wav -v
python3 -m unittest src.tests.test_rb_converter_gui -v
python3 -m unittest src.tests.test_version_display -v
python3 -m unittest src.tests.test_rb_converter_spec -v
```

Full suite (required before declaring a slice or the task done):

```bash
python3 -m unittest discover -s src/tests -v
```

ffmpeg is required for conversion tests that actually invoke it; the Cloud Agent environment and CI install it.

## Done when

- Each requested behavior has at least one test that failed for the right reason before it passed
- `python3 -m unittest discover -s src/tests -v` is green
- Production changes are the minimum needed; originals are still never overwritten
- No speculative tests or helpers for slices you did not run
