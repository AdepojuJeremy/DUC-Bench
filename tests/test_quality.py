from ducbench.quality import static_quality_checks
from ducbench.planner import target_matrix


def test_target_matrix_default():
    rows = target_matrix()
    assert sum(r["target"] for r in rows) == 200
    assert sum(r["target"] for r in rows if not r["control_condition"]) == 180
    assert sum(r["target"] for r in rows if r["control_condition"]) == 20
    assert all(r["duc_arm"] is None for r in rows if r["control_condition"])


def test_control_is_not_fourth_arm():
    item = {
        "decision_subdomain": "diagnosis",
        "control_condition": True,
        "duc_arm": "complicating",
        "expected_transition": "maintain",
        "evidence_validity": "valid",
        "confidence_direction": "similar",
        "decision_question": "Should treatment A be continued?",
        "stage_1": {"text": "A sufficiently detailed Stage 1 vignette with an initial recommendation.", "expected_recommendation": "Continue A."},
        "stage_2": {"text": "Additional information that does not bear materially on the decision."},
        "expected_revised_recommendation": "Continue A.",
        "warrant_packet": "No material change is warranted.",
        "source_records": [{"quote": "source evidence"}],
    }
    out = static_quality_checks(item)
    assert "control_must_not_be_a_fourth_duc_arm" in out["errors"]
