# DUC-Bench Data Curation Pipeline

Code for constructing and auditing provisional **Decision Update Consistency (DUC-Bench)** medical decision-updating datasets from source-grounded evidence routes.

> **Anonymous-review repository.** This repository intentionally contains no author-identifying metadata. The generated development sets are **Draft/provisional** items and must not be described as clinically validated Gold cases unless they complete warrant verification, independent clinical review, adjudication, and release freeze.

## Benchmark taxonomy

The current pipeline keeps two annotation axes separate:

- **Evidence arm:** `contradictory`, `complicating`, `uncertainty_inducing`
- **Decision transition:** `Maintain`, `Modify`, `Replace`, `Suspend`

A no-conflict condition may be used as a control, but it is not a fourth evidence arm.

The six decision subdomains used by the construction pipeline are diagnosis, treatment selection, triage/urgency, medication safety, public-health advice, and patient counselling.

## Repository layout

```text
ducbench/
  archive.py       archive discovery and normalization
  models.py        canonical record helpers
  planner.py       coverage planning and target matrices
  prompts.py       source-grounded generation/review prompts
  providers.py     OpenAI and Anthropic provider adapters
  quality.py       static structural checks
  pipeline.py      inspect, generate, and re-mine workflows
  virtual.py       deterministic 300/320 development-set builders
  virtual450.py    matched 450-item development-set builder
  cli.py           command-line interface

tests/
  test_quality.py

docs/
  ARCHIVE_AUDIT.md
```

Generated datasets, source archives, caches, credentials, and model outputs are intentionally excluded from version control.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or install the package in editable mode:

```bash
pip install -e .
```

## Inspect an archived curation run

```bash
python -m ducbench.cli inspect /path/to/archive --out outputs/audit
```

This normalizes successfully generated candidates, reconstructs the validator-promoted seed pool, retains rejected/unresolved routes for audit, and produces coverage/target reports.

## LLM-assisted source-grounded generation

Set a provider key locally; do not place credentials in configuration files or commits.

```bash
export OPENAI_API_KEY='...'
# or
export ANTHROPIC_API_KEY='...'
```

Smoke test first:

```bash
python -m ducbench.cli generate /path/to/archive \
  --provider openai \
  --model YOUR_MODEL_ID \
  --workers 3 \
  --limit 3 \
  --out outputs/smoke
```

Then run the desired batch. The generator is instructed to use supplied evidence facts and approved premises only and to return an unconstructible result rather than inventing clinical facts when the evidence packet is insufficient.

## Re-screen rejected or unresolved source pairs

```bash
python -m ducbench.cli remine /path/to/archive \
  --provider openai \
  --model YOUR_MODEL_ID \
  --out outputs/remine
```

This is useful because the earlier validator favored determinate changes and can suppress uncertainty-inducing cases or legitimate no-change controls.

## Deterministic development-set builders

These commands require no external model API. They operate on validator-promoted source routes and create **Draft** experimental records.

### 300-record curated development set

```bash
python -m ducbench.cli virtual300 /path/to/archive --out outputs/virtual_300
```

### 320-record full audit pool

```bash
python -m ducbench.cli virtual320 /path/to/archive --out outputs/virtual_320
```

### 450-record matched development set

```bash
python -m ducbench.cli virtual450 /path/to/archive --out outputs/virtual_450
```

The 450-record build uses 150 curated base routes with three matched variants per route. It preserves the four transition families exactly: **Maintain, Modify, Replace, Suspend**. Route variants are grouped by `base_route_id`/`matched_set_id`; they are not statistically independent clinical cases and should remain in the same split.

Some uncertainty/Suspend variants use explicitly constructed scenario premises. Those records are tagged as requiring independent premise approval; the code does not represent them as facts extracted from the source guideline.

## Validation boundary

Automated construction and static checks establish schema/traceability properties only. They do **not** establish clinical correctness, source sufficiency, safety, or Gold status. Gold release requires the benchmark's full review process.

## Tests

```bash
pytest -q
```

## Reproducibility and anonymity

For double-blind review, do not link directly to a personal GitHub profile in the paper. Publish this repository through the venue-approved anonymous-code mechanism or an anonymizing repository mirror, and verify that README text, commit metadata, repository history, issue discussions, file paths, and bundled artifacts do not reveal author identities.
