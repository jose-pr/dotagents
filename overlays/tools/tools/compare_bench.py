#!/usr/bin/env python3
"""Diff two benchmark result files and print a verdict, so no model has to eyeball JSON.

Consumes the structured benchmark JSON described in `~/.agents/flows/REPO.md`
(min/median/max ms per metric, one file per version+interpreter):

    py -3.12 ~/.agents/tools/compare_bench.py benchmarks/results/old.json new.json
    py -3.12 ~/.agents/tools/compare_bench.py --threshold 10 old.json new.json

Compares on median, flags any metric that regressed by more than --threshold
percent, and exits non-zero if any did — so it works as a release gate.

Why: reading two JSON blobs and doing percentage arithmetic in-context is pure
token burn, and models make arithmetic slips. Compute it, print the answer.
"""
import argparse
import json
import sys
from pathlib import Path


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    metrics = data.get("metrics") or data.get("metrics_ms") or {}
    flat = {}
    for name, m in metrics.items():
        flat[name] = m["median_ms"] if isinstance(m, dict) else float(m)
    return {"name": data.get("name", path.stem), "metrics": flat, "raw": data}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("before", type=Path)
    ap.add_argument("after", type=Path)
    ap.add_argument("--threshold", type=float, default=10.0,
                    help="percent slower before it counts as a regression (default 10)")
    args = ap.parse_args()

    a, b = load(args.before), load(args.after)

    for key in ("python", "processor"):
        va, vb = a["raw"].get(key), b["raw"].get(key)
        if va and vb and va != vb:
            print(f"!! {key} differs ({va} vs {vb}) — numbers are not comparable")

    shared = sorted(set(a["metrics"]) & set(b["metrics"]))
    if not shared:
        print("no metrics in common")
        return 2

    print(f"{a['name']} -> {b['name']}   (median ms/call)")
    print(f"{'metric':20s} {'before':>10s} {'after':>10s} {'change':>10s}")

    regressions = []
    for name in shared:
        before, after = a["metrics"][name], b["metrics"][name]
        if before == 0:
            change = "n/a"
        else:
            pct = (after - before) / before * 100
            change = f"{pct:+.1f}%"
            if pct > args.threshold:
                regressions.append((name, pct))
        print(f"{name:20s} {before:10.4f} {after:10.4f} {change:>10s}")

    only = sorted(set(b["metrics"]) - set(a["metrics"]))
    if only:
        print(f"\nnew metrics (no baseline): {', '.join(only)}")

    print()
    if regressions:
        print(f"REGRESSED (> {args.threshold:.0f}%):")
        for name, pct in regressions:
            print(f"  {name}  {pct:+.1f}%")
        return 1

    print(f"OK — nothing regressed by more than {args.threshold:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
