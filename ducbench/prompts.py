from __future__ import annotations
import json

GENERATOR_SYSTEM = """You construct research benchmark vignettes for DUC-Bench, a healthcare decision-update benchmark.
You are a drafting tool, NOT a clinical authority. You MUST use only the supplied source facts and approved premises. Never add a diagnosis, contraindication, test result, dose, timing rule, efficacy claim, safety claim, or population constraint that is not supported by a supplied fact or premise.

DUC-Bench separates two dimensions:
1) Evidence arm: contradictory, complicating, uncertainty_inducing.
2) Decision transition: maintain, modify, replace, suspend.
Do not conflate them.

Definitions:
- contradictory: Stage 2 directly conflicts with a material proposition supporting Stage 1.
- complicating: Stage 2 adds an exception, condition, trade-off, implementation requirement, or contextual factor.
- uncertainty_inducing: Stage 2 reduces certainty or exposes an evidence gap without necessarily supporting a clear alternative.
- maintain: preserve the recommendation.
- modify: retain the core direction but change conditions, implementation, scope, rationale, or confidence.
- replace: substitute a materially different recommendation.
- suspend: withhold/defer commitment because evidence is insufficient, unsafe, or requires clarification.

Hard construction rules:
- Stage 1 must be answerable and must not leak Stage-2 information.
- Stage 2 must add genuinely new evidence while preserving the same fixed decision question.
- The transition must be proportional to the evidence.
- The patient/population, setting, timing, and applicability must be explicit enough to judge.
- Learner-facing text must paraphrase sources rather than copy long guideline wording.
- Do not name a source organization unless `evidence_framing` explicitly asks for source identity.
- Every material claim in the answer/warrant must list source fact IDs.
- If the requested arm cannot be supported by the supplied facts, return `constructible: false` rather than inventing facts.
- Do not turn public-health/economic policy comparisons into patient-level clinical decisions unless the source genuinely supports the requested DUC subdomain.

Return JSON only."""

STRUCTURAL_REVIEW_SYSTEM = """You are a strict DUC-Bench structural reviewer. You do not decide clinical truth beyond the supplied source packet. Audit the candidate against G1-G6 and the DUC taxonomy. Reject invented claims, Stage-2 leakage, changed decision questions, answer-signalling wording, arm/transition conflation, source mismatch, and disproportional updates. Return JSON only."""


def generation_prompt(seed: dict, target_subdomain: str, target_arm: str | None, control: bool = False) -> str:
    target = {
        "decision_subdomain": target_subdomain,
        "duc_arm": None if control else target_arm,
        "control_condition": control,
        "preferred_transition": "maintain" if control else None,
    }
    facts = seed.get("fact_packet") or []
    if not facts:
        facts = [
            {"fact_id": "F1", "stage": "stage_1", "quote": (seed.get("baseline_source") or {}).get("quote")},
            {"fact_id": "F2", "stage": "stage_2", "quote": (seed.get("modifier_source") or {}).get("quote")},
        ]
    payload = {
        "target": target,
        "fixed_decision_question": seed.get("decision_question"),
        "approved_scenario_premises": seed.get("approved_scenario_premises") or [],
        "source_facts": facts,
        "validator_expected_initial_recommendation": seed.get("expected_initial_recommendation"),
        "validator_expected_revised_recommendation": seed.get("expected_revised_recommendation"),
        "legacy_label_for_provenance_only": seed.get("legacy_expected_update"),
        "validator_questions": seed.get("validator_questions") or [],
    }
    schema = {
        "constructible": True,
        "construction_failure_reason": None,
        "decision_question": "same fixed question",
        "decision_subdomain": target_subdomain,
        "control_condition": control,
        "duc_arm": None if control else target_arm,
        "evidence_validity": "valid|weak|invalid|irrelevant",
        "evidence_framing": "neutral_source_grounded",
        "stage_1": {"text": "...", "fact_ids": ["F1"], "premises": []},
        "stage_2": {"text": "...", "fact_ids": ["F2"], "premises": [], "applicability": "..."},
        "expected_initial_recommendation": "...",
        "acceptable_initial_recommendations": [],
        "expected_transition": "maintain|modify|replace|suspend",
        "expected_revised_recommendation": "...",
        "acceptable_revised_recommendations": [],
        "confidence_direction": "increase|similar|decrease|case_dependent",
        "warrant_packet": "...",
        "safe_response": {"example": "...", "must_not": ["..."]},
        "claim_source_map": [{"claim": "...", "source_fact_ids": ["F1", "F2"]}],
        "unresolved_questions": [],
    }
    return "SOURCE-GROUNDED SEED:\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n\nRETURN THIS JSON SHAPE:\n" + json.dumps(schema, indent=2)


def structural_review_prompt(item: dict, source_facts: list[dict]) -> str:
    payload = {"candidate": item, "source_facts": source_facts}
    requested = {
        "verdict": "pass|revise|reject",
        "gates": {
            "G1": {"pass": True, "reason": "Stage 1 coherent and no leakage"},
            "G2": {"pass": True, "reason": "Stage 1 supports a defensible recommendation from supplied facts"},
            "G3": {"pass": True, "reason": "Stage 2 is new and same fixed decision"},
            "G4": {"pass": True, "reason": "transition is appropriate and proportional"},
            "G5": {"pass": True, "reason": "material claims trace to source facts"},
            "G6": {"pass": True, "reason": "population/setting/timing/applicability explicit"},
        },
        "arm_correct": True,
        "transition_correct": True,
        "invented_claims": [],
        "leakage": [],
        "required_repairs": [],
    }
    return "AUDIT INPUT:\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n\nRETURN JSON SHAPE:\n" + json.dumps(requested, indent=2)

REMINER_SYSTEM = """You re-screen archived source-pair candidates under the CURRENT DUC-Bench definitions. The older pipeline was optimized for determinate changes and may have rejected useful uncertainty-inducing or no-conflict controls.

Use only the two supplied source excerpts and existing candidate metadata. Do not invent clinical facts.

Classify into exactly one of:
- contradictory
- complicating
- uncertainty_inducing
- no_conflict_control
- unusable

A no-conflict control is not a fourth DUC arm. It is eligible only when Stage 2 is genuinely new but does not warrant a material clinical decision change for the same fixed question.

An uncertainty-inducing candidate is eligible when Stage 2 genuinely makes the decision less determinate, exposes insufficient evidence, lowers confidence, or makes deferral/information-seeking appropriate. Do NOT reject it merely because there is no unique replacement answer.

Require all of: a coherent fixed clinical decision, answerable Stage 1, genuinely new Stage 2, no invented patient facts, and source excerpts that actually support the proposed construction. Exclude pure health-economic/marketing-authorization/comparator questions unless they clearly instantiate one of the six allowed healthcare decision subdomains.

Return JSON only."""


def remine_prompt(seed: dict) -> str:
    payload = {
        "candidate_uid": seed.get("candidate_uid"),
        "prior_validator_verdict": seed.get("validator_verdict"),
        "prior_exclusion_reason": seed.get("exclusion_reason"),
        "decision_question": seed.get("decision_question"),
        "baseline_source": seed.get("baseline_source"),
        "modifier_source": seed.get("modifier_source"),
        "approved_scenario_premises": seed.get("approved_scenario_premises") or [],
        "prior_quality_warnings": seed.get("quality_warnings") or [],
    }
    shape = {
        "eligible": True,
        "classification": "contradictory|complicating|uncertainty_inducing|no_conflict_control|unusable",
        "decision_subdomain": "diagnosis|treatment_selection|triage_urgency|medication_safety|public_health_advice|patient_counselling",
        "same_fixed_decision": True,
        "stage_1_answerable": True,
        "stage_2_genuinely_new": True,
        "no_invention_needed": True,
        "expected_transition": "maintain|modify|replace|suspend",
        "confidence_direction": "increase|similar|decrease|case_dependent",
        "reason": "...",
        "stage_1_fact": {"fact_id": "F1", "meaning": "faithful paraphrase of baseline excerpt"},
        "stage_2_fact": {"fact_id": "F2", "meaning": "faithful paraphrase of modifier excerpt"},
        "safety_or_applicability_concerns": [],
    }
    return "ARCHIVED SOURCE PAIR:\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n\nRETURN JSON SHAPE:\n" + json.dumps(shape, indent=2)
