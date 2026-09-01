# DUC-Bench archive audit and 200-item construction plan

## Executive result

The supplied archive is usable as a **source-grounded seed pool**, but it is not close to a balanced 200-item DUC-Bench by itself.

Verified archive totals:

- 4 runs: two NICE, one Cancer Care Ontario (CCO), one WHO.
- 273 mined candidate source-pairs.
- 160 validator-promoted source-grounded seeds.
- 90 successfully generated candidate vignettes.
- 54 generator errors, 15 revalidation-needed records, and 1 reviewer error among the promoted seeds.
- 113 candidates were rejected or did not resolve to a promotion verdict.

The 90 generated candidates have provisional evidence-arm counts:

- Complicating: 54
- Contradictory: 33
- Uncertainty-inducing: 3

Their old update labels are:

- Revise: 55
- Weaken: 19
- Strengthen: 14
- Maintain: 2

Those old update labels must **not** be treated as the current DUC transition taxonomy.

## Taxonomy migration

The pipeline uses the current separation:

**Evidence arm**

- contradictory
- complicating
- uncertainty-inducing

**Decision transition family**

- Maintain
- Modify
- Replace
- Suspend

`No-conflict` is represented only as a control condition and is not a fourth DUC arm.

The migration deliberately does not map legacy `revise` to `Replace` automatically, because the old `revise` bucket can contain both a true replacement and a conditional modification. Those records are marked `transition_reclassification_required`.

## 200-item pilot design

The default plan is:

- 180 formal DUC items = 6 decision subdomains × 3 evidence arms × 10 items per cell.
- 20 no-conflict controls distributed across the six subdomains.

Decision subdomains:

1. diagnosis
2. treatment selection
3. triage & urgency
4. medication safety
5. public-health advice
6. patient counselling

This is preferable to simply taking the first 200 generated rows because it prevents a treatment-selection/complicating-heavy dataset from determining the benchmark conclusions.

## How far the archive gets us

Using the 90 already-generated items, heuristic subdomain routing, and the validator's provisional arm labels, only **60 of the 200 balanced target slots are filled**. The balanced deficit is therefore **140 items**.

If all 160 promoted source-grounded seeds are considered potentially recoverable, the balanced deficit is still **122 items**. This is because the archive is concentrated in treatment-selection and complicating/contradictory cases and contains almost no uncertainty-inducing material and no explicit no-conflict control pool.

The subdomain routing in this audit is deliberately marked as heuristic and must be reclassified by the current taxonomy reviewer before analysis.

## Why re-mining matters

The old validation logic preferred clear, determinate decision changes. That is a poor selection pressure for the new uncertainty-inducing arm, where the correct behaviour may be reduced confidence, information seeking, conditionalization, or suspension rather than a unique replacement answer.

The new `remine` stage therefore re-screens all 113 rejected/unresolved source pairs under current DUC definitions. It prioritizes source excerpts containing uncertainty language such as `insufficient`, `uncertain`, `not established`, and `low certainty`, but the lexical screen itself never promotes an item.

The audit found:

- 8 non-promoted source pairs with high-priority uncertainty language for re-screening.
- 5 old rejected cases in which the source pair may potentially function as a no-conflict control because Stage 2 did not materially change the same decision. These are only candidates and are not automatically accepted.

## Pipeline safety rules

The generator is instructed to:

- use only supplied source facts and approved premises;
- never invent diagnoses, contraindications, test results, doses, timing rules, efficacy/safety claims, or population restrictions;
- keep the fixed decision question unchanged across stages;
- prevent Stage-2 leakage into Stage 1;
- keep evidence arm separate from transition family;
- map material claims back to source fact IDs;
- reject a requested construction as `constructible: false` when the source packet cannot support it;
- avoid long verbatim copying from guidelines;
- reject pure economic/marketing/comparator tasks unless they instantiate an allowed healthcare decision subdomain.

Every generated draft receives static checks and a second structural-model pass against G1-G6. This still does **not** make an item clinically validated or Gold.

## Recommended sequence for the current deadline

1. Use `existing_90_normalized.yaml` as an audit pool, not as the final dataset.
2. Re-run the 70 promoted-but-not-successfully-generated seeds through the new generator.
3. Run `remine` over the 113 rejected/unresolved pairs, with uncertainty-oriented cases first.
4. Generate from newly eligible re-mined seeds.
5. Mine additional authoritative source packets for any cells still deficient, especially uncertainty-inducing and no-conflict controls.
6. Run G1-G6 structural review on 100% of the provisional experiment set.
7. Label the workshop set explicitly as a **provisional source-grounded pilot**, not a clinically validated Gold benchmark.
8. Reserve Gold status for the full two-clinician review/adjudication process.

## Files produced

See `outputs/` for the normalized 90, promoted 160 seed pool, rejected/unresolved audit pool, target matrices, generation queue, uncertainty-priority re-mining queue, and likely-control candidates.
