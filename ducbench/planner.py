from __future__ import annotations
from collections import Counter
from .models import SUBDOMAINS, DUC_ARMS


def target_matrix(total: int = 200, controls: int = 20) -> list[dict]:
    if total != 200 or controls != 20:
        # Generalized below, but the default has a particularly clean 10/cell design.
        formal = total - controls
        base = formal // (len(SUBDOMAINS) * len(DUC_ARMS))
        rem = formal % (len(SUBDOMAINS) * len(DUC_ARMS))
    else:
        base, rem = 10, 0
    rows = []
    k = 0
    for sub in SUBDOMAINS:
        for arm in DUC_ARMS:
            n = base + (1 if k < rem else 0)
            rows.append({"decision_subdomain": sub, "duc_arm": arm, "control_condition": False, "target": n})
            k += 1
    # Controls are not a fourth DUC arm. Spread them across subdomains.
    cbase, crem = divmod(controls, len(SUBDOMAINS))
    for i, sub in enumerate(SUBDOMAINS):
        rows.append({"decision_subdomain": sub, "duc_arm": None, "control_condition": True, "target": cbase + (1 if i < crem else 0)})
    return rows


def compute_plan(items: list[dict], total: int = 200, controls: int = 20) -> tuple[list[dict], list[dict]]:
    counts = Counter()
    for item in items:
        if item.get("control_condition"):
            key = (item.get("decision_subdomain"), None, True)
        else:
            key = (item.get("decision_subdomain"), item.get("duc_arm"), False)
        counts[key] += 1

    matrix = target_matrix(total, controls)
    queue = []
    for row in matrix:
        key = (row["decision_subdomain"], row["duc_arm"], row["control_condition"])
        observed = counts[key]
        usable = min(observed, row["target"])
        deficit = row["target"] - usable
        row.update({"observed_candidates": observed, "usable_toward_balanced_target": usable, "deficit": deficit})
        if deficit:
            queue.append({
                "decision_subdomain": row["decision_subdomain"],
                "duc_arm": row["duc_arm"],
                "control_condition": row["control_condition"],
                "needed": deficit,
            })
    return matrix, queue
