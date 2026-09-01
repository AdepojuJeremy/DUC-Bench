from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
import csv
import json

from .archive import discover_runs, build_seed_pool, dump_yaml
from .planner import compute_plan
from .prompts import GENERATOR_SYSTEM, STRUCTURAL_REVIEW_SYSTEM, generation_prompt, structural_review_prompt
from .providers import make_provider
from .quality import static_quality_checks


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def inspect_and_plan(archive_dir: Path, output_dir: Path) -> dict[str, Any]:
    bundles = discover_runs(archive_dir)
    promoted, generated, non_promoted = build_seed_pool(bundles)

    audits = []
    for item in generated:
        q = static_quality_checks(item)
        item["static_quality"] = q
        item["quality_gates"].update(q["quality_gates"])
        audits.append({
            "item_id": item["item_id"],
            "candidate_uid": item["provenance"]["candidate_uid"],
            "source_pool": item["provenance"]["source_pool"],
            "decision_subdomain": item["decision_subdomain"],
            "subdomain_review_required": item["decision_subdomain_review_required"],
            "duc_arm": item["duc_arm"],
            "legacy_expected_update": item["provenance"]["legacy_expected_update"],
            "transition_hint": item["expected_transition"],
            "transition_reclassification_required": item["transition_reclassification_required"],
            "static_pass": q["static_pass"],
            "errors": "|".join(q["errors"]),
            "warnings": "|".join(q["warnings"]),
        })

    matrix, queue = compute_plan(generated, total=200, controls=20)
    output_dir.mkdir(parents=True, exist_ok=True)
    dump_yaml({"schema_version": "ducbench-pilot-0.1", "items": generated}, output_dir / "existing_90_normalized.yaml")
    dump_yaml({"schema_version": 1, "seeds": promoted}, output_dir / "promoted_160_seed_pool.yaml")
    dump_yaml({"schema_version": 1, "candidates": non_promoted}, output_dir / "non_promoted_113_audit_pool.yaml")
    dump_yaml({"target_total": 200, "formal_duc_items": 180, "no_conflict_controls": 20, "cells": matrix}, output_dir / "target_matrix.yaml")
    dump_yaml({"note": "Controls are not a fourth DUC arm.", "queue": queue}, output_dir / "generation_queue.yaml")
    write_csv(audits, output_dir / "existing_90_audit.csv")
    write_csv(matrix, output_dir / "target_matrix.csv")

    stats = {
        "runs": len(bundles),
        "candidate_records": sum(len(b.candidates) for b in bundles),
        "promoted_seed_specs": len(promoted),
        "successfully_generated_existing": len(generated),
        "non_promoted_or_unresolved": len(non_promoted),
        "existing_proposal_statuses": {},
        "arm_counts_in_existing_90": {},
        "legacy_update_counts_in_existing_90": {},
        "balanced_target_deficit_after_existing_90": sum(x["needed"] for x in queue),
    }
    from collections import Counter
    stats["existing_proposal_statuses"] = dict(Counter(s["original_proposal_status"] for s in promoted))
    stats["arm_counts_in_existing_90"] = dict(Counter(i["duc_arm"] for i in generated))
    stats["legacy_update_counts_in_existing_90"] = dict(Counter(i["provenance"]["legacy_expected_update"] for i in generated))
    (output_dir / "archive_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


def _seed_index(promoted: list[dict]) -> dict[tuple[str, str | None], list[dict]]:
    idx: dict[tuple[str, str | None], list[dict]] = {}
    for seed in promoted:
        idx.setdefault((seed.get("decision_subdomain"), seed.get("duc_arm")), []).append(seed)
    return idx


def generate_from_queue(
    archive_dir: Path,
    output_dir: Path,
    provider_name: str,
    model: str,
    max_workers: int = 6,
    limit: int | None = None,
) -> list[dict]:
    """Generate/re-generate balanced candidate items from validator-promoted source seeds.

    This stage will not silently invent source material. If a target cell has no suitable
    promoted seed (especially uncertainty/control cells), it writes an unmet-source-mining
    requirement rather than fabricating one.
    """
    bundles = discover_runs(archive_dir)
    promoted, generated, _ = build_seed_pool(bundles)
    matrix, queue = compute_plan(generated, total=200, controls=20)
    idx = _seed_index(promoted)
    provider = make_provider(provider_name, model)

    tasks = []
    unmet = []
    used = set()
    for q in queue:
        sub, arm, control, needed = q["decision_subdomain"], q["duc_arm"], q["control_condition"], q["needed"]
        # Existing promoted runs have no explicit control arm. Never repurpose a conflicting
        # source pair into a control without a grounded support/irrelevance source.
        if control:
            unmet.append({**q, "reason": "requires_control_source_mining; no fourth-arm fabrication"})
            continue
        pool = [s for s in idx.get((sub, arm), []) if s["candidate_uid"] not in used]
        # Prioritize failed generations so the 90 already completed aren't duplicated.
        pool.sort(key=lambda s: 0 if s.get("original_proposal_status") != "generated" else 1)
        take = pool[:needed]
        for seed in take:
            used.add(seed["candidate_uid"])
            tasks.append((seed, sub, arm, control))
        if len(take) < needed:
            unmet.append({**q, "reason": f"only_{len(take)}_promoted_source_seeds_available; run_source_mining_for_remainder"})

    if limit is not None:
        tasks = tasks[:limit]

    results = []
    def work(task):
        seed, sub, arm, control = task
        draft = provider.generate_json(GENERATOR_SYSTEM, generation_prompt(seed, sub, arm, control))
        if not draft.get("constructible", True):
            return {"status": "not_constructible", "seed": seed["candidate_uid"], "draft": draft}
        item = {
            "item_id": f"DUC-GEN-{seed['candidate_uid'].split('_')[-1].upper()}",
            "item_version": "0.1-draft",
            "clinical_domain": "healthcare",
            "decision_subdomain": draft.get("decision_subdomain", sub),
            "decision_question": draft.get("decision_question") or seed.get("decision_question"),
            "source_group_id": seed["candidate_uid"],
            "control_condition": bool(draft.get("control_condition", control)),
            "duc_arm": draft.get("duc_arm") if not control else None,
            "evidence_validity": draft.get("evidence_validity", "valid"),
            "evidence_framing": draft.get("evidence_framing", "neutral_source_grounded"),
            "stage_1": {
                **(draft.get("stage_1") or {}),
                "expected_recommendation": draft.get("expected_initial_recommendation"),
                "acceptable_recommendations": draft.get("acceptable_initial_recommendations") or [],
                "expected_confidence": None,
            },
            "stage_2": draft.get("stage_2") or {},
            "expected_transition": draft.get("expected_transition"),
            "expected_revised_recommendation": draft.get("expected_revised_recommendation"),
            "acceptable_revised_recommendations": draft.get("acceptable_revised_recommendations") or [],
            "confidence_direction": draft.get("confidence_direction"),
            "warrant_packet": draft.get("warrant_packet"),
            "safe_response": draft.get("safe_response") or {},
            "claim_source_map": draft.get("claim_source_map") or [],
            "unresolved_questions": draft.get("unresolved_questions") or [],
            "source_facts": seed.get("fact_packet") or [],
            "source_records": [seed.get("baseline_source"), seed.get("modifier_source")],
            "validation_status": "draft",
            "quality_gates": {f"G{i}": "not_assessed" for i in range(1, 11)},
            "provenance": {
                "construction_method": "source_grounded_llm_regeneration",
                "run_id": seed["run_id"],
                "candidate_uid": seed["candidate_uid"],
                "source_pool": seed["source_pool"],
                "legacy_expected_update": seed.get("legacy_expected_update"),
            },
        }
        static = static_quality_checks(item)
        item["static_quality"] = static
        item["quality_gates"].update(static["quality_gates"])
        structural = provider.generate_json(STRUCTURAL_REVIEW_SYSTEM, structural_review_prompt(item, item["source_facts"]))
        item["structural_model_review"] = structural
        return {"status": "generated", "item": item}

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(work, t) for t in tasks]
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"status": "error", "error": f"{type(e).__name__}: {e}"})

    output_dir.mkdir(parents=True, exist_ok=True)
    dump_yaml({"items": [r["item"] for r in results if r.get("status") == "generated"]}, output_dir / "new_generated_candidates.yaml")
    dump_yaml({"unmet": unmet}, output_dir / "unmet_source_mining_requirements.yaml")
    dump_yaml({"results": results}, output_dir / "generation_run_log.yaml")
    return results


def remine_non_promoted(
    archive_dir: Path,
    output_dir: Path,
    provider_name: str,
    model: str,
    max_workers: int = 6,
    limit: int | None = None,
) -> list[dict]:
    """Re-screen the 113 rejected/unresolved source pairs under current DUC semantics.

    This is especially useful because an older validator that preferred determinate
    updates can systematically under-represent uncertainty-inducing cases.
    """
    from .prompts import REMINER_SYSTEM, remine_prompt
    bundles = discover_runs(archive_dir)
    _, _, non_promoted = build_seed_pool(bundles)
    provider = make_provider(provider_name, model)

    uncertainty_terms = ("uncertain", "insufficient", "not established", "unknown", "inconclusive", "limited evidence", "low certainty")
    def priority(seed):
        text = ((seed.get("baseline_source") or {}).get("quote") or "") + " " + ((seed.get("modifier_source") or {}).get("quote") or "")
        return 0 if any(x in text.lower() for x in uncertainty_terms) else 1
    seeds = sorted(non_promoted, key=priority)
    if limit is not None:
        seeds = seeds[:limit]

    def work(seed):
        verdict = provider.generate_json(REMINER_SYSTEM, remine_prompt(seed))
        out = {"seed": seed, "current_review": verdict}
        if verdict.get("eligible") and verdict.get("classification") != "unusable":
            classification = verdict.get("classification")
            out["reminted_seed"] = {
                **seed,
                "decision_subdomain": verdict.get("decision_subdomain"),
                "subdomain_routing_method": "current_taxonomy_llm_rescreen",
                "subdomain_routing_confidence": None,
                "duc_arm": None if classification == "no_conflict_control" else classification,
                "control_condition": classification == "no_conflict_control",
                "suggested_transition_hint": verdict.get("expected_transition"),
                "suggested_confidence_direction": verdict.get("confidence_direction"),
                "fact_packet": [
                    {"fact_id": "F1", "stage": "stage_1", "quote": (seed.get("baseline_source") or {}).get("quote"), "meaning": (verdict.get("stage_1_fact") or {}).get("meaning")},
                    {"fact_id": "F2", "stage": "stage_2", "quote": (seed.get("modifier_source") or {}).get("quote"), "meaning": (verdict.get("stage_2_fact") or {}).get("meaning")},
                ],
                "rescreen_reason": verdict.get("reason"),
                "rescreen_concerns": verdict.get("safety_or_applicability_concerns") or [],
            }
        return out

    rows = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(work, s) for s in seeds]
        for fut in as_completed(futs):
            try:
                rows.append(fut.result())
            except Exception as e:
                rows.append({"error": f"{type(e).__name__}: {e}"})

    eligible = [r["reminted_seed"] for r in rows if r.get("reminted_seed")]
    output_dir.mkdir(parents=True, exist_ok=True)
    dump_yaml({"seeds": eligible}, output_dir / "reminted_source_seeds.yaml")
    dump_yaml({"reviews": rows}, output_dir / "remine_review_log.yaml")
    return rows
