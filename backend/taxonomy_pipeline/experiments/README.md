# Experiments

## Case B evaluation

This directory contains the evaluator for saved Case B model outputs. The
runtime prompt is defined in
`src/taxonomy_pipeline/classification/case_b.py`; this directory does not keep
separate prompt copies. Generated model outputs and evaluation summaries are
ignored because they are paid-run artifacts.

Edit the runtime prompt in `case_b.py`, run Case B with a separate output and
checkpoint for that version, and repeat for each prompt version. For example:

```bash
taxonomy-pipeline case-b --report report.json \
  --output experiments/results/case_b_v1.json \
  --checkpoint experiments/results/case_b_v1.checkpoint.json

python experiments/evaluate_case_b.py \
  --baseline ../sql-files-nus \
  --report report.json \
  --skillsfuture-sql ../skillsfuture/skillsfuture_taxonomy.sql \
  --output v1=experiments/results/case_b_v1.json \
  --output v2=experiments/results/case_b_v2.json \
  --summary experiments/results/comparison.json
```

The evaluator reads the expected Case B IDs from `report.json`, then evaluates
each saved output independently through the original
`backend/skillsfuture/validate_classification_proposals.py`. Its
`classification_validator` section contains validation errors, warnings,
records needing review, and the validator output. No other validation pipeline
is run by this evaluator. The summary does not compare prompts automatically;
compare the per-version metrics manually, especially `error_count`,
`records_needing_review`, `warning_count`, and the individual error messages.

Confidence remains the model's estimate and is not treated as a calibrated
probability. Never reuse a checkpoint after changing a prompt.

## Golden run

`golden_run.py` checks that a refactor doesn't change behaviour (#104). It runs
one full classification round on the real data through the CLI and saves every
stage's output in one folder:
- the scorecard;
- Case A and Case B;
- the three merge-previews;
- bulk-decide;
- integrate;
- the enriched scorecard and `check-comparable`.

It is free and deterministic:
- **No models:** scoring is lexical only, so there is no sentence model and no judge.
- **No LLM:** Case B runs its real code against a stub model that answers from a
  hash of each tag ID, and the hash of every prompt is recorded.
- **No reviewer:** every tag still pending after bulk-decide is excluded, so
  integrate gets a fully reviewed run.

Run it from `backend/taxonomy_pipeline/`. A recording takes about two minutes
and is about 300 MB. `golden_runs/` is git-ignored.

```bash
python experiments/golden_run.py record golden_runs/before    # on dev, before the change
python experiments/golden_run.py record golden_runs/after     # after it
python experiments/golden_run.py compare golden_runs/before golden_runs/after
```

`compare` ignores the keys that differ on every run by design (`run_id`,
timestamps, event and submission IDs) and the recording folder's own path.
Everything else must match exactly, including each command's exit code and
printed output (`commands.json`). It exits non-zero and prints the first
difference in each file that changed.

`golden_run.py` installs the stub by replacing `case_b._openai_client`. If a
refactor moves the OpenAI client setup, update that one line in the same
change.
