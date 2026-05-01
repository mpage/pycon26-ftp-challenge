# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

from __future__ import annotations

import tempfile
import threading
import unittest
from types import ModuleType
from unittest.mock import MagicMock

from graph import BuildGraph, Target
from harness import (
    _instrument_build,
    _load_submission,
    format_json_results,
    InstrumentedResults,
    run_one,
    validate,
    ValidationResult,
)
from reference import build_all


def _make_diamond_graph() -> BuildGraph:
    """Create a diamond-shaped graph: d depends on b,c; b,c depend on a."""
    a = Target(name="a", deps=[], work=10, seed=1)
    b = Target(name="b", deps=[a], work=10, seed=1)
    c = Target(name="c", deps=[a], work=10, seed=1)
    d = Target(name="d", deps=[b, c], work=10, seed=1)
    return BuildGraph(seed=1, targets={"a": a, "b": b, "c": c, "d": d})


def _make_simple_graph() -> BuildGraph:
    """Create a simple linear graph: b depends on a."""
    a = Target(name="a", deps=[], work=10, seed=1)
    b = Target(name="b", deps=[a], work=10, seed=1)
    return BuildGraph(seed=1, targets={"a": a, "b": b})


def _make_submission_module(build_all_fn) -> ModuleType:
    """Create a fake submission module with the given build_all function."""
    mod = ModuleType("submission")
    mod.build_all = build_all_fn
    return mod


class LoadSubmissionTest(unittest.TestCase):
    def test_load_valid_submission(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("def build_all(graph):\n    return {}\n")
            f.flush()
            module = _load_submission(f.name)
        self.assertTrue(hasattr(module, "build_all"))

    def test_load_missing_build_all_raises(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write("def other_func():\n    pass\n")
            f.flush()
            with self.assertRaises(RuntimeError):
                _load_submission(f.name)

    def test_load_nonexistent_file_raises(self) -> None:
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            _load_submission("/nonexistent/path/submission.py")


class InstrumentBuildTest(unittest.TestCase):
    def test_restores_original_build(self) -> None:
        graph = _make_simple_graph()
        original_build = Target.build
        returned = _instrument_build(InstrumentedResults(), graph)
        self.assertIs(returned, original_build)
        # The instrumented version should be different
        self.assertIsNot(Target.build, original_build)
        # Restore
        Target.build = returned

    def test_detects_duplicate_builds(self) -> None:
        graph = _make_simple_graph()
        instrumented = InstrumentedResults()
        original_build = _instrument_build(instrumented, graph)
        try:
            target_a = graph.targets["a"]
            target_a.build({})
            target_a.build({})
            self.assertIn("a", instrumented.dups)
        finally:
            Target.build = original_build

    def test_no_duplicate_on_single_build(self) -> None:
        graph = _make_simple_graph()
        instrumented = InstrumentedResults()
        original_build = _instrument_build(instrumented, graph)
        try:
            graph.targets["a"].build({})
            self.assertEqual(len(instrumented.dups), 0)
        finally:
            Target.build = original_build

    def test_detects_order_violation(self) -> None:
        graph = _make_simple_graph()
        instrumented = InstrumentedResults()
        original_build = _instrument_build(instrumented, graph)
        try:
            # Build b before a — should record a violation
            graph.targets["b"].build({"a": b"fake"})
            self.assertTrue(len(instrumented.violations) > 0)
            self.assertIn("'b' started before", instrumented.violations[0])
        finally:
            Target.build = original_build

    def test_no_violation_in_correct_order(self) -> None:
        graph = _make_simple_graph()
        instrumented = InstrumentedResults()
        original_build = _instrument_build(instrumented, graph)
        try:
            result_a = graph.targets["a"].build({})
            graph.targets["b"].build({"a": result_a})
            self.assertEqual(len(instrumented.violations), 0)
        finally:
            Target.build = original_build


class ValidateTest(unittest.TestCase):
    def _run_reference(self, graph: BuildGraph) -> dict[str, bytes]:
        return build_all(graph)

    def test_correct_build_passes(self) -> None:
        graph = _make_diamond_graph()
        reference_results = self._run_reference(graph)

        instrumented = InstrumentedResults()
        original_build = _instrument_build(instrumented, graph)
        try:
            # Build in correct order
            for name in ["a", "b", "c", "d"]:
                target = graph.targets[name]
                dep_results = {
                    d.name: target._result or reference_results[d.name]
                    for d in target.deps
                }
                target.build(dep_results)
        finally:
            Target.build = original_build

        result = validate(graph, instrumented, reference_results)
        self.assertTrue(result.passed)
        self.assertEqual(len(result.errors), 0)

    def test_missing_targets_reported(self) -> None:
        graph = _make_simple_graph()
        reference_results = self._run_reference(graph)

        instrumented = InstrumentedResults()
        original_build = _instrument_build(instrumented, graph)
        try:
            # Only build 'a', skip 'b'
            graph.targets["a"].build({})
        finally:
            Target.build = original_build

        result = validate(graph, instrumented, reference_results)
        self.assertFalse(result.passed)
        self.assertTrue(any("Missing" in e for e in result.errors))

    def test_duplicate_builds_reported(self) -> None:
        graph = _make_simple_graph()
        reference_results = self._run_reference(graph)

        instrumented = InstrumentedResults()
        original_build = _instrument_build(instrumented, graph)
        try:
            graph.targets["a"].build({})
            graph.targets["a"].build({})  # duplicate
            result_a = graph.targets["a"]._result
            graph.targets["b"].build({"a": result_a})
        finally:
            Target.build = original_build

        result = validate(graph, instrumented, reference_results)
        self.assertFalse(result.passed)
        self.assertTrue(any("duplicate" in e for e in result.errors))

    def test_wrong_result_reported(self) -> None:
        graph = _make_simple_graph()
        reference_results = self._run_reference(graph)

        instrumented = InstrumentedResults()
        original_build = _instrument_build(instrumented, graph)
        try:
            graph.targets["a"].build({})
            graph.targets["b"].build({"a": b"wrong_data"})
        finally:
            Target.build = original_build

        result = validate(graph, instrumented, reference_results)
        self.assertFalse(result.passed)
        self.assertTrue(any("Wrong result" in e for e in result.errors))

    def test_order_violations_reported(self) -> None:
        graph = _make_simple_graph()
        reference_results = self._run_reference(graph)

        instrumented = InstrumentedResults()
        original_build = _instrument_build(instrumented, graph)
        try:
            # Build b before a
            graph.targets["b"].build({"a": reference_results["a"]})
            graph.targets["a"].build({})
        finally:
            Target.build = original_build

        result = validate(graph, instrumented, reference_results)
        self.assertFalse(result.passed)
        self.assertTrue(any("Order violation" in e for e in result.errors))


class RunOneTest(unittest.TestCase):
    def test_correct_submission_passes(self) -> None:
        graph = _make_diamond_graph()
        submission = _make_submission_module(build_all)
        result = run_one(submission, graph, "test_graph")
        self.assertTrue(result["passed"])
        self.assertEqual(len(result["errors"]), 0)
        self.assertEqual(result["graph"], "test_graph")
        self.assertEqual(result["num_targets"], 4)
        self.assertGreater(result["speedup"], 0)

    def test_incomplete_submission_fails(self) -> None:
        def bad_build_all(graph: BuildGraph) -> dict[str, bytes]:
            # Only build the first target, skip the rest
            target = graph.targets["a"]
            result = target.build({})
            return {"a": result}

        graph = _make_diamond_graph()
        submission = _make_submission_module(bad_build_all)
        result = run_one(submission, graph, "test_graph")
        self.assertFalse(result["passed"])
        self.assertTrue(len(result["errors"]) > 0)

    def test_duplicate_build_submission_fails(self) -> None:
        def dup_build_all(graph: BuildGraph) -> dict[str, bytes]:
            results = {}
            # Build in correct order but build 'a' twice
            for name in ["a", "a", "b", "c", "d"]:
                target = graph.targets[name]
                dep_results = {d.name: results[d.name] for d in target.deps}
                results[name] = target.build(dep_results)
            return results

        graph = _make_diamond_graph()
        submission = _make_submission_module(dup_build_all)
        result = run_one(submission, graph, "test_graph")
        self.assertFalse(result["passed"])
        self.assertTrue(any("duplicate" in e for e in result["errors"]))


class ValidationResultTest(unittest.TestCase):
    def test_passed_true_when_no_errors(self) -> None:
        result = ValidationResult(passed=True, errors=[])
        self.assertTrue(result.passed)

    def test_passed_false_when_errors(self) -> None:
        result = ValidationResult(passed=False, errors=["some error"])
        self.assertFalse(result.passed)


def _make_result(graph="g.json", passed=True, ref_time=1.0, sub_time=0.5) -> dict:
    return {
        "graph": graph,
        "num_targets": 10,
        "reference_time": ref_time,
        "submission_time": sub_time,
        "speedup": ref_time / sub_time if sub_time > 0 else float("inf"),
        "passed": passed,
        "errors": [] if passed else ["some error"],
    }


class FormatJsonResultsTest(unittest.TestCase):
    def test_single_result_returns_flat_dict(self) -> None:
        result = _make_result()
        output = format_json_results([result])
        self.assertEqual(output, result)

    def test_multiple_results_returns_results_and_summary(self) -> None:
        r1 = _make_result(graph="a.json", ref_time=2.0, sub_time=1.0)
        r2 = _make_result(graph="b.json", ref_time=4.0, sub_time=1.0)
        output = format_json_results([r1, r2])
        self.assertEqual(output["results"], [r1, r2])
        summary = output["summary"]
        self.assertAlmostEqual(summary["total_reference_time"], 6.0)
        self.assertAlmostEqual(summary["total_submission_time"], 2.0)
        self.assertAlmostEqual(summary["overall_speedup"], 3.0)
        self.assertTrue(summary["overall_passed"])

    def test_summary_overall_passed_false_when_any_fail(self) -> None:
        r1 = _make_result(graph="a.json", passed=True)
        r2 = _make_result(graph="b.json", passed=False)
        output = format_json_results([r1, r2])
        self.assertFalse(output["summary"]["overall_passed"])

    def test_summary_overall_passed_true_when_all_pass(self) -> None:
        r1 = _make_result(graph="a.json", passed=True)
        r2 = _make_result(graph="b.json", passed=True)
        output = format_json_results([r1, r2])
        self.assertTrue(output["summary"]["overall_passed"])


if __name__ == "__main__":
    unittest.main()
