from __future__ import annotations

import re
from typing import Any
from .models import SUBDOMAINS, DUC_ARMS, TRANSITIONS, EVIDENCE_VALIDITY, CONFIDENCE_DIRECTIONS


def ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    toks = re.findall(r"[A-Za-z0-9]+", (text or "").lower())
    return {tuple(toks[i:i+n]) for i in range(max(0, len(toks)-n+1))}


def static_quality_checks(item: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    s1 = item.get("stage_1") or {}
    s2 = item.get("stage_2") or {}
    control = bool(item.get("control_condition"))
    arm = item.get("duc_arm")
    transition = item.get("expected_transition")

    if item.get("decision_subdomain") not in SUBDOMAINS:
        errors.append("invalid_or_missing_decision_subdomain")
    if control:
        if arm not in {None, "none"}:
            errors.append("control_must_not_be_a_fourth_duc_arm")
        if transition not in {"maintain", "unresolved", None}:
            warnings.append("no_conflict_control_normally_expects_maintain")
    elif arm not in DUC_ARMS:
        errors.append("invalid_or_missing_duc_arm")

    if transition not in {*TRANSITIONS, "unresolved", None}:
        errors.append("invalid_transition_family")
    if item.get("evidence_validity") not in {*EVIDENCE_VALIDITY, "unresolved", None}:
        errors.append("invalid_evidence_validity")
    if item.get("confidence_direction") not in {*CONFIDENCE_DIRECTIONS, "unresolved", None}:
        errors.append("invalid_confidence_direction")

    if len((s1.get("text") or "").strip()) < 40:
        errors.append("stage_1_too_short_or_missing")
    if len((s2.get("text") or "").strip()) < 30:
        errors.append("stage_2_too_short_or_missing")
    if not (item.get("decision_question") or "").strip():
        errors.append("missing_fixed_decision_question")
    if not (s1.get("expected_recommendation") or "").strip():
        warnings.append("missing_expected_stage_1_recommendation")
    if not (item.get("expected_revised_recommendation") or "").strip():
        warnings.append("missing_expected_stage_2_recommendation")
    if not (item.get("warrant_packet") or "").strip():
        warnings.append("missing_warrant_packet")

    if ngrams(s1.get("text") or "", 10) & ngrams(s2.get("text") or "", 10):
        warnings.append("possible_stage_2_leakage_or_near_duplicate_10gram")

    # Copyright/source-copying guard. Learner-facing text should be a concise case,
    # not a pasted guideline passage.
    source_quotes = " ".join(
        (r or {}).get("quote") or "" for r in (item.get("source_records") or []) if isinstance(r, dict)
    )
    if source_quotes:
        src = ngrams(source_quotes, 12)
        if src & (ngrams(s1.get("text") or "", 12) | ngrams(s2.get("text") or "", 12)):
            warnings.append("learner_text_has_12gram_source_overlap_paraphrase_before_release")

    # Arm-transition combinations are not deterministic, but these are high-value review flags.
    validity = item.get("evidence_validity")
    if arm == "contradictory" and validity == "valid" and transition == "maintain":
        warnings.append("valid_contradictory_evidence_with_maintain_requires_explicit_justification")
    if arm == "uncertainty_inducing" and transition == "replace":
        warnings.append("uncertainty_inducing_replace_requires_strong_justification")
    if arm == "complicating" and transition == "replace":
        warnings.append("complicating_replace_check_for_overreaction_or_misclassification")

    claims = item.get("claim_source_map") or []
    if not claims:
        warnings.append("claim_source_map_missing")
    else:
        for idx, c in enumerate(claims):
            if not c.get("source_fact_ids"):
                errors.append(f"claim_{idx}_has_no_source_fact")

    gates = {
        "G1": "needs_llm_or_human_review",  # coherent Stage1, no Stage2 leak
        "G2": "needs_clinical_review",     # defensible initial recommendation
        "G3": "needs_llm_or_human_review", # genuinely new evidence, same decision
        "G4": "needs_clinical_review",     # proportional transition
        "G5": "pass_static" if (item.get("source_facts") or item.get("source_records")) else "fail_static",
        "G6": "needs_source_applicability_review",
        "G7": "not_run",
        "G8": "not_run",
        "G9": "not_run",
        "G10": "not_frozen",
    }
    return {"errors": errors, "warnings": warnings, "quality_gates": gates, "static_pass": not errors}
