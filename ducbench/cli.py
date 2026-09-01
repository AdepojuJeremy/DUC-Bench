from __future__ import annotations
import argparse
import json
from pathlib import Path
from .pipeline import inspect_and_plan, generate_from_queue, remine_non_promoted
from .virtual import generate_virtual_320, generate_virtual_300
from .virtual450 import generate_virtual_450


def main() -> None:
    p = argparse.ArgumentParser(description="DUC-Bench source-grounded pilot dataset pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("inspect", help="Reverse-engineer archive, normalize the 90 generated items, and create the 200-item balance plan")
    a.add_argument("archive", type=Path)
    a.add_argument("--out", type=Path, default=Path("outputs"))

    g = sub.add_parser("generate", help="Regenerate balanced candidates from validator-promoted source seeds")
    g.add_argument("archive", type=Path)
    g.add_argument("--out", type=Path, default=Path("outputs/generation"))
    g.add_argument("--provider", choices=["openai", "anthropic"], required=True)
    g.add_argument("--model", required=True, help="Pass an explicit model ID; the pipeline intentionally does not hard-code one")
    g.add_argument("--workers", type=int, default=6)
    g.add_argument("--limit", type=int, default=None, help="Useful for a 2-5 item smoke test before a batch run")

    v3 = sub.add_parser("virtual300", help="Generate the curated 300-record virtual-sandbox dataset from 150 clinical source groups (no API key required)")
    v3.add_argument("archive", type=Path)
    v3.add_argument("--out", type=Path, default=Path("outputs/virtual_300"))

    v45 = sub.add_parser("virtual450", help="Generate the 450-record matched draft dataset from 150 curated source routes (no API key required)")
    v45.add_argument("archive", type=Path)
    v45.add_argument("--out", type=Path, default=Path("outputs/virtual_450"))

    v = sub.add_parser("virtual320", help="Generate a local 320-record virtual-sandbox dataset from the 160 promoted source groups (no API key required)")
    v.add_argument("archive", type=Path)
    v.add_argument("--out", type=Path, default=Path("outputs/virtual_320"))

    r = sub.add_parser("remine", help="Re-screen rejected/unresolved source pairs under the current DUC taxonomy")
    r.add_argument("archive", type=Path)
    r.add_argument("--out", type=Path, default=Path("outputs/remine"))
    r.add_argument("--provider", choices=["openai", "anthropic"], required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--workers", type=int, default=6)
    r.add_argument("--limit", type=int, default=None)

    args = p.parse_args()
    if args.cmd == "virtual450":
        summary = generate_virtual_450(args.archive, args.out)
        print(json.dumps(summary, indent=2))
    elif args.cmd == "virtual300":
        summary = generate_virtual_300(args.archive, args.out)
        print(json.dumps(summary, indent=2))
    elif args.cmd == "virtual320":
        summary = generate_virtual_320(args.archive, args.out)
        print(json.dumps(summary, indent=2))
    elif args.cmd == "inspect":
        stats = inspect_and_plan(args.archive, args.out)
        print(json.dumps(stats, indent=2))
    elif args.cmd == "generate":
        results = generate_from_queue(args.archive, args.out, args.provider, args.model, args.workers, args.limit)
        counts = {}
        for r in results:
            counts[r.get("status", "unknown")] = counts.get(r.get("status", "unknown"), 0) + 1
        print(json.dumps(counts, indent=2))
    elif args.cmd == "remine":
        rows = remine_non_promoted(args.archive, args.out, args.provider, args.model, args.workers, args.limit)
        eligible = sum(1 for r in rows if r.get("reminted_seed"))
        print(json.dumps({"reviewed": len(rows), "eligible_under_current_taxonomy": eligible}, indent=2))


if __name__ == "__main__":
    main()
