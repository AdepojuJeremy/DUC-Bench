from __future__ import annotations

SUBDOMAINS = (
    "diagnosis",
    "treatment_selection",
    "triage_urgency",
    "medication_safety",
    "public_health_advice",
    "patient_counselling",
)
DUC_ARMS = ("contradictory", "complicating", "uncertainty_inducing")
TRANSITIONS = ("maintain", "modify", "replace", "suspend")
EVIDENCE_VALIDITY = ("valid", "weak", "invalid", "irrelevant")
CONFIDENCE_DIRECTIONS = ("increase", "similar", "decrease", "case_dependent")
VALIDATION_STATUSES = ("draft", "structurally_reviewed", "clinically_reviewed", "adjudicated", "gold")

# The July generator used these. They are retained only as provenance; they are
# not the final DUC-Bench transition taxonomy.
LEGACY_UPDATES = ("maintain", "revise", "weaken", "strengthen", "abstain")


def normalize_arm(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "uncertainty": "uncertainty_inducing",
        "uncertaintyinducing": "uncertainty_inducing",
        "qualifying": "complicating",
        "contradicts": "contradictory",
        "qualifies": "complicating",
    }
    value = aliases.get(value, value)
    return value if value in DUC_ARMS else None


def suggested_transition_from_legacy(update: str | None) -> str | None:
    """A non-authoritative hint only; never used as gold without review."""
    if update == "maintain":
        return "maintain"
    if update in {"weaken", "strengthen"}:
        return "modify"
    # `revise` was too coarse: it may be Modify or Replace.
    # `abstain` may be Suspend, but requires case-level adjudication.
    return None
