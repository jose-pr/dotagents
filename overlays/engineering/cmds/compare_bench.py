"""`dotagents compare-bench` -- diff two benchmark result files and print a verdict.

    dotagents compare-bench benchmarks/results/old.json new.json
    dotagents compare-bench --threshold 10 old.json new.json

Reads the structured benchmark JSON the repository standard describes
(`$ENGINEERING_OVERLAY_ROOT/flows/REPO.md`: min, median and max ms per metric,
one file per version and interpreter), compares on the median, and flags any
metric that got slower by more than ``--threshold`` percent.

Exit codes: 0 nothing regressed, 1 something did, 2 the two files share no
metric. So it works as a release gate.

Percentage arithmetic done in context is slow and gets slips; this computes
it and prints the answer.
"""

from __future__ import annotations

import json
from pathlib import Path

from duho import Cmd, LoggingArgs

EXIT_OK = 0
EXIT_REGRESSED = 1
EXIT_NOT_COMPARABLE = 2


class CompareBench(LoggingArgs, Cmd):
    """Compare two benchmark result files on the median; exit 1 if anything regressed."""

    _parsername_ = "compare-bench"

    before: Path = Path()
    "The baseline result file."
    ("before",)

    after: Path = Path()
    "The result file to judge against it."
    ("after",)

    threshold: float = 10.0
    "Percent slower before a metric counts as a regression."
    ("--threshold",)

    def __call__(self) -> int:
        old, new = self._load(self.before), self._load(self.after)
        for key in ("python", "processor"):
            was, now = old["raw"].get(key), new["raw"].get(key)
            if was and now and was != now:
                print("!! %s differs (%s vs %s) — numbers are not comparable" % (key, was, now))

        shared = sorted(set(old["metrics"]) & set(new["metrics"]))
        if not shared:
            print("no metrics in common")
            return EXIT_NOT_COMPARABLE

        print("%s -> %s   (median ms/call)" % (old["name"], new["name"]))
        print("%-20s %10s %10s %10s" % ("metric", "before", "after", "change"))
        regressions = []
        for name in shared:
            was, now = old["metrics"][name], new["metrics"][name]
            change = "n/a"
            if was:
                percent = (now - was) / was * 100
                change = "%+.1f%%" % percent
                if percent > self.threshold:
                    regressions.append((name, percent))
            print("%-20s %10.4f %10.4f %10s" % (name, was, now, change))

        added = sorted(set(new["metrics"]) - set(old["metrics"]))
        if added:
            print("\nnew metrics (no baseline): %s" % ", ".join(added))

        print()
        if regressions:
            print("REGRESSED (> %.0f%%):" % self.threshold)
            for name, percent in regressions:
                print("  %s  %+.1f%%" % (name, percent))
            return EXIT_REGRESSED
        print("OK — nothing regressed by more than %.0f%%" % self.threshold)
        return EXIT_OK

    @staticmethod
    def _load(path: Path) -> dict:
        """A result file as {name, metrics: {metric: median ms}, raw}."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            raise SystemExit("error: cannot read %s (%s)" % (path, exc))
        except ValueError as exc:
            raise SystemExit("error: %s is not JSON (%s)" % (path, exc))
        metrics = data.get("metrics") or data.get("metrics_ms") or {}
        flat = {name: (m["median_ms"] if isinstance(m, dict) else float(m))
                for name, m in metrics.items()}
        return {"name": data.get("name", Path(path).stem), "metrics": flat, "raw": data}
