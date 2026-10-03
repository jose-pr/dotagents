"""`dotagents compare-bench` compares on the median and its exit code is the verdict."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

duho = pytest.importorskip("duho")

MODULE = Path(__file__).resolve().parents[1] / "cmds" / "compare_bench.py"
_spec = importlib.util.spec_from_file_location("compare_bench_cmd", MODULE)
compare_bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = compare_bench  # duho resolves field annotations through sys.modules
_spec.loader.exec_module(compare_bench)


def result(tmp_path, name, metrics, **extra):
    path = tmp_path / (name + ".json")
    path.write_text(json.dumps({"name": name, "metrics": metrics, **extra}), encoding="utf-8")
    return path


def run(capsys, *argv):
    code = duho.parse(compare_bench.CompareBench, [str(a) for a in argv])()
    return code, capsys.readouterr().out


def test_within_the_threshold_is_ok(tmp_path, capsys):
    old = result(tmp_path, "old", {"parse": {"median_ms": 1.00}, "encode": 2.0})
    new = result(tmp_path, "new", {"parse": {"median_ms": 1.05}, "encode": 1.5})
    code, out = run(capsys, old, new)
    assert code == compare_bench.EXIT_OK
    assert "+5.0%" in out and "-25.0%" in out
    assert out.rstrip().endswith("nothing regressed by more than 10%")


def test_a_regression_past_the_threshold_is_exit_1_and_named(tmp_path, capsys):
    old = result(tmp_path, "old", {"parse": {"median_ms": 1.0}, "encode": {"median_ms": 2.0}})
    new = result(tmp_path, "new", {"parse": {"median_ms": 1.3}, "encode": {"median_ms": 2.0}})
    code, out = run(capsys, old, new)
    assert code == compare_bench.EXIT_REGRESSED
    assert "REGRESSED (> 10%):" in out and "parse  +30.0%" in out
    code, _ = run(capsys, "--threshold", "50", old, new)
    assert code == compare_bench.EXIT_OK


def test_no_shared_metric_is_exit_2(tmp_path, capsys):
    old = result(tmp_path, "old", {"parse": 1.0})
    new = result(tmp_path, "new", {"encode": 1.0})
    code, out = run(capsys, old, new)
    assert code == compare_bench.EXIT_NOT_COMPARABLE
    assert "no metrics in common" in out


def test_a_different_interpreter_is_called_out_and_new_metrics_listed(tmp_path, capsys):
    old = result(tmp_path, "old", {"parse": 1.0}, python="3.9.10")
    new = result(tmp_path, "new", {"parse": 1.0, "encode": 2.0}, python="3.14.7")
    _, out = run(capsys, old, new)
    assert "python differs (3.9.10 vs 3.14.7)" in out
    assert "new metrics (no baseline): encode" in out


def test_an_unreadable_or_malformed_file_is_a_one_line_error(tmp_path, capsys):
    good = result(tmp_path, "good", {"parse": 1.0})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit, match="is not JSON"):
        run(capsys, good, bad)
    with pytest.raises(SystemExit, match="cannot read"):
        run(capsys, good, tmp_path / "missing.json")
