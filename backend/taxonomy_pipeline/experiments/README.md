# Case B evaluation

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
