from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import yaml

from .models import normalize_arm, suggested_transition_from_legacy


def load_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def dump_yaml(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True, width=120)


@dataclass
class RunBundle:
    run_id: str
    path: Path
    source_pool: str | None
    candidates: list[dict]
    validations: list[dict]
    proposals: list[dict]
    record_catalog: list[dict]


def discover_runs(archive_dir: Path) -> list[RunBundle]:
    bundles: list[RunBundle] = []
    for run in sorted(p for p in archive_dir.iterdir() if p.is_dir()):
        needed = [run / "candidates.yaml", run / "validations.yaml", run / "vignette_proposals.yaml"]
        if not all(p.exists() for p in needed):
            continue
        cfg = load_yaml(run / "config.yaml") if (run / "config.yaml").exists() else {}
        pools = cfg.get("pools") or []
        pool = pools[0].get("id") if pools and isinstance(pools[0], dict) else None
        bundles.append(
            RunBundle(
                run_id=run.name,
                path=run,
                source_pool=pool,
                candidates=(load_yaml(run / "candidates.yaml").get("records") or []),
                validations=(load_yaml(run / "validations.yaml").get("records") or []),
                proposals=(load_yaml(run / "vignette_proposals.yaml").get("records") or []),
                record_catalog=(load_yaml(run / "record_catalog.yaml").get("records") or [])
                if (run / "record_catalog.yaml").exists() else [],
            )
        )
    return bundles


def heuristic_subdomain(text: str) -> tuple[str, float]:
    """Conservative routing heuristic. It is explicitly not a clinical labeler."""
    t = (text or "").lower()
    scores = {
        "diagnosis": 0,
        "treatment_selection": 0,
        "triage_urgency": 0,
        "medication_safety": 0,
        "public_health_advice": 0,
        "patient_counselling": 0,
    }
    keywords = {
        "diagnosis": ["diagnos", "differential", "test result", "imaging", "biopsy", "screen for", "recurrence"],
        "treatment_selection": ["treat", "therapy", "surgery", "procedure", "intervention", "regimen", "management"],
        "triage_urgency": ["urgent", "emergency", "immediate", "admit", "refer", "triage", "same day"],
        "medication_safety": ["dose", "drug", "medication", "contraindicat", "bleed", "interaction", "adverse", "anticoag", "antibiotic"],
        "public_health_advice": ["programme", "program", "population", "district", "city", "coverage", "public health", "vaccin", "screening service"],
        "patient_counselling": ["counsel", "preference", "shared decision", "support", "caregiver", "discuss", "advise the patient", "education"],
    }
    for label, words in keywords.items():
        scores[label] = sum(1 for w in words if w in t)
    # Prefer medication safety over generic treatment when both appear.
    if scores["medication_safety"] and scores["treatment_selection"]:
        scores["medication_safety"] += 0.5
    best = max(scores, key=scores.get)
    top = scores[best]
    if top <= 0:
        return "treatment_selection", 0.20
    sorted_scores = sorted(scores.values(), reverse=True)
    gap = top - (sorted_scores[1] if len(sorted_scores) > 1 else 0)
    confidence = min(0.90, 0.45 + 0.12 * top + 0.08 * gap)
    return best, round(confidence, 2)


def source_fact_packet(validation: dict) -> list[dict]:
    response = validation.get("response") or {}
    facts = []
    for i, fact in enumerate(response.get("facts") or [], start=1):
        facts.append({
            "fact_id": f"F{i}",
            "source_doc_label": fact.get("source_doc_label"),
            "stage": fact.get("stage"),
            "quote": fact.get("quote"),
            "meaning": fact.get("meaning"),
            "applicability": fact.get("applicability"),
        })
    return facts


def build_seed_pool(bundles: list[RunBundle]) -> tuple[list[dict], list[dict], list[dict]]:
    """Return promoted seeds, normalized completed vignettes, and non-promoted candidates."""
    promoted: list[dict] = []
    normalized_generated: list[dict] = []
    non_promoted: list[dict] = []

    for bundle in bundles:
        cand_map = {c.get("uid"): c for c in bundle.candidates}
        val_map = {v.get("candidate_uid"): v for v in bundle.validations}
        prop_map = {p.get("candidate_uid"): p for p in bundle.proposals}

        for cid, cand in cand_map.items():
            val = val_map.get(cid) or {}
            prop = prop_map.get(cid) or {}
            verdict = val.get("resolved_verdict")
            vresp = val.get("response") or {}
            arm = normalize_arm(vresp.get("provisional_arm"))
            legacy_update = vresp.get("expected_update") or (prop.get("response") or {}).get("expected_update")
            combined = " ".join([
                cand.get("decision_question") or "",
                cand.get("baseline_quote") or "",
                cand.get("modifier_quote") or "",
                vresp.get("expected_initial_answer") or "",
                vresp.get("expected_revised_answer") or "",
            ])
            subdomain, routing_conf = heuristic_subdomain(combined)
            seed = {
                "candidate_uid": cid,
                "run_id": bundle.run_id,
                "source_pool": bundle.source_pool,
                "original_proposal_status": prop.get("status"),
                "validator_verdict": verdict,
                "decision_question": cand.get("decision_question") or (prop.get("response") or {}).get("decision_question"),
                "decision_subdomain": subdomain,
                "subdomain_routing_method": "keyword_heuristic_needs_review",
                "subdomain_routing_confidence": routing_conf,
                "duc_arm": arm,
                "legacy_expected_update": legacy_update,
                "suggested_transition_hint": suggested_transition_from_legacy(legacy_update),
                "expected_initial_recommendation": vresp.get("expected_initial_answer") or (prop.get("response") or {}).get("expected_initial_answer"),
                "expected_revised_recommendation": vresp.get("expected_revised_answer") or (prop.get("response") or {}).get("expected_revised_answer"),
                "approved_scenario_premises": vresp.get("approved_scenario_premises") or [],
                "fact_packet": source_fact_packet(val),
                "baseline_source": {
                    "record_uid": cand.get("baseline_record_uid"),
                    "quote": cand.get("baseline_quote"),
                    "start": cand.get("baseline_start"),
                    "end": cand.get("baseline_end"),
                },
                "modifier_source": {
                    "record_uid": cand.get("modifier_record_uid"),
                    "quote": cand.get("modifier_quote"),
                    "start": cand.get("modifier_start"),
                    "end": cand.get("modifier_end"),
                },
                "validator_questions": vresp.get("reviewer_questions") or [],
                "quality_warnings": cand.get("quality_warnings") or [],
            }
            if verdict == "promote":
                promoted.append(seed)
                if prop.get("status") == "generated" and isinstance(prop.get("response"), dict):
                    normalized_generated.append(normalize_generated(seed, prop["response"]))
            else:
                seed["exclusion_reason"] = (vresp.get("primary_reason") or val.get("error") or "not_promoted_or_unresolved")
                non_promoted.append(seed)

    return promoted, normalized_generated, non_promoted


def stable_item_id(candidate_uid: str) -> str:
    digest = hashlib.sha1(candidate_uid.encode("utf-8")).hexdigest()[:10]
    return f"DUC-CAND-{digest.upper()}"


def normalize_generated(seed: dict, response: dict) -> dict:
    stage1 = response.get("stage_1") or {}
    stage2 = response.get("stage_2") or {}
    transition_hint = seed.get("suggested_transition_hint")
    unresolved = transition_hint is None
    return {
        "item_id": stable_item_id(seed["candidate_uid"]),
        "item_version": "0.1-draft",
        "clinical_domain": "healthcare",
        "decision_subdomain": seed["decision_subdomain"],
        "decision_subdomain_review_required": True,
        "decision_question": response.get("decision_question") or seed.get("decision_question"),
        "source_group_id": seed["candidate_uid"],
        "control_condition": False,
        "duc_arm": seed.get("duc_arm"),
        "evidence_validity": "valid",  # validator-promoted source pairing; still requires clinical review
        "evidence_framing": "neutral_source_grounded",
        "stage_1": {
            "text": stage1.get("text"),
            "fact_ids": stage1.get("facts_used") or [],
            "premises": stage1.get("premises_used") or [],
            "expected_recommendation": response.get("expected_initial_answer"),
            "acceptable_recommendations": response.get("acceptable_initial_answers") or [],
            "expected_confidence": None,
        },
        "stage_2": {
            "text": stage2.get("text"),
            "fact_ids": stage2.get("facts_used") or [],
            "premises": stage2.get("premises_used") or [],
            "applicability": None,
        },
        "expected_transition": transition_hint or "unresolved",
        "transition_reclassification_required": unresolved or seed.get("legacy_expected_update") == "revise",
        "expected_revised_recommendation": response.get("expected_revised_answer"),
        "acceptable_revised_recommendations": response.get("acceptable_revised_answers") or [],
        "confidence_direction": "unresolved",
        "warrant_packet": response.get("warrant"),
        "unresolved_questions": response.get("unresolved_questions") or [],
        "safe_response": {"example": response.get("expected_revised_answer"), "must_not": []},
        "claim_source_map": [],
        "source_facts": seed.get("fact_packet") or [],
        "source_records": [seed.get("baseline_source"), seed.get("modifier_source")],
        "validation_status": "draft",
        "quality_gates": {f"G{i}": "not_assessed" for i in range(1, 11)},
        "provenance": {
            "construction_method": "normalized_from_team_generation_archive",
            "run_id": seed["run_id"],
            "candidate_uid": seed["candidate_uid"],
            "source_pool": seed["source_pool"],
            "legacy_expected_update": seed.get("legacy_expected_update"),
            "original_proposal_status": seed.get("original_proposal_status"),
            "review_status_note": "Construction candidate; not Gold. Transition taxonomy requires review-pack reclassification.",
        },
    }
