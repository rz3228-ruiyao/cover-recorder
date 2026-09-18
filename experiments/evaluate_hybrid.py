"""Evaluate the integrated policy on fresh seeds and optional local real pairs."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from spectral_alignment import Path, SR, cases, load_real_pairs
from cover_syncer.spectral_sync import estimate_offset_hybrid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed-start", type=int, default=3000)
    parser.add_argument("--seeds", type=int, default=20)
    parser.add_argument("--real-pairs", type=Path)
    args = parser.parse_args()
    rows = []

    def evaluate(meta, x, y):
        result = estimate_offset_hybrid(x, y, SR)
        error = abs(result.offset_ms-meta["truth_ms"]) if meta["truth_ms"] is not None else None
        rows.append(dict(**meta, **asdict(result), error_ms=error,
                         correct=error <= 40 if error is not None else None))

    for meta, x, y in cases(range(args.seed_start, args.seed_start+args.seeds)):
        evaluate(meta, x, y)
    if args.real_pairs:
        for meta, x, y in load_real_pairs(args.real_pairs):
            evaluate(meta, x, y)
    summary = []
    for group in dict.fromkeys(row["group"] for row in rows):
        selected = [r for r in rows if r["group"] == group]
        high = [r for r in selected if r["reliability"] == "high"]
        summary.append(dict(group=group, count=len(selected), correct=sum(bool(r["correct"]) for r in selected),
                            high=len(high), wrong_high=sum(not bool(r["correct"]) for r in high)))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/"hybrid-results.json").write_text(json.dumps(dict(summary=summary, rows=rows), indent=2,
                                                           ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
