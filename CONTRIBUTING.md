# Contributing to hayamimi

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # includes requirements.txt
.venv/Scripts/python scripts/download_models.py
```

## Tests

```bash
.venv/Scripts/python -m pytest tests
```

`tests/test_units.py` covers unit-level logic (character-set arbitration,
fallback behavior, etc.) and doesn't require the full model set.

## Accuracy evaluation

Model or routing changes must be validated against real speech, not just
unit tests -- this project's whole design history (`docs/BENCHMARKS.md`) is
built on real-speech regressions that unit tests alone would have missed
(e.g. iteration #27's Cantonese regression from a routing change that passed
every existing test).

- `scripts/eval_accuracy.py` -- ja accuracy vs. `faster-whisper large-v3-turbo`
  as reference.
- `scripts/eval_engine.py` -- end-to-end scorecard across all 5 language
  routes (what `docs/SCORECARD.md` is generated from).
- `scripts/bench_offline.py` -- offline RTF (speed) benchmarking for a given
  model directory.

**Policy: any change to model routing, decoding parameters, or
language-detection logic should be re-validated by re-running the relevant
eval script against real speech clips before merging, not just against
synthetic/TTS test data.** TTS audio is too clean to catch the failure modes
that matter here (see `docs/EVAL.md` vs. `docs/EVAL_REAL.md` for why this
project moved from synthetic to real-speech evaluation early on). If you
touch language-detection or routing logic specifically, re-run the eval
across *all* languages, not just the one you changed -- several regressions
in this project's history were caught only because of a full re-score
(see `docs/BENCHMARKS.md` iterations #12 and #27).

## Pull requests

- Keep unrelated changes out of one PR.
- If you change decode parameters, routing thresholds, or add/replace a
  model, include the before/after eval numbers in the PR description.
- If you add a new model dependency, update `THIRD_PARTY_NOTICES.md` and
  `scripts/download_models.py` in the same PR.
