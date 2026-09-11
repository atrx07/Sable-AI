import tempfile
import unittest
from pathlib import Path

from sable.orchestrator import Orchestrator
from sable.runtime import TerminationReason
from sable.sessions import SessionManager
from sable.tools import ToolExecutor


def verification(status, *, signature="", check_status=None, evidence=True):
    legacy = "pass" if status.startswith("PASS") else "skipped" if status == "SKIPPED" else "fail"
    checks = []
    if check_status:
        checks.append({
            "check": {"name": "unit tests", "target_reasons": ["changed source maps to tests"]},
            "status": check_status,
            "classification": "ASSERTION_FAILURE" if check_status == "FAIL" else "NONE",
            "diagnostic": "one assertion failed" if check_status == "FAIL" else "",
            "failure_signature": signature,
            "result": {"success": check_status == "PASS", "exit_code": 0 if check_status == "PASS" else 1},
        })
    return {
        "status": legacy,
        "overall_status": status,
        "scope": "AFFECTED",
        "summary": f"{status}: structured test result",
        "checks": checks,
        "plan": {"plan_id": "plan-test", "requested_scope": "AFFECTED", "checks": [1] if checks else []},
        "evidence": ({"evidence_id": "evidence-test", "checks": checks} if evidence else {}),
        "duration_ms": 3,
        "budget_exhausted": False,
    }


class EditingMain:
    def __init__(self, executor, *, weaken_test=False):
        self.executor = executor
        self.weaken_test = weaken_test
        self.calls = 0

    def run(self, _message, mode="build"):
        self.calls += 1
        results = [self.executor.write_file("source.py", f"value = {self.calls}\n")]
        if self.weaken_test and self.calls > 1:
            results.append(self.executor.write_file(
                "tests/test_source.py",
                "import unittest\n\n@unittest.skip('make green')\nclass SourceTests(unittest.TestCase):\n    pass\n",
            ))
        changed = []
        for item in results:
            changed.extend(item.changed_files)
        return {
            "chat_reply": "edited",
            "changes_summary": [],
            "tool_results": results,
            "changed_files": changed,
            "steps": 1,
            "tool_calls": len(results),
        }


class StagedVerifier:
    def __init__(self):
        self.scopes = []
        self.failed_calls = 0
        self.last_run = None

    def verify(self, changed_files, run_command=None, *, mode="build", scope=None):
        self.scopes.append(scope.value)
        self.last_run = object()
        if len(self.scopes) == 1:
            return verification("FAIL", signature="sig-original", check_status="FAIL")
        return verification("PASS", check_status="PASS")

    def verify_failed(self, previous_run, changed_files, *, mode="build"):
        self.failed_calls += 1
        self.last_run = object()
        return verification("PASS", check_status="PASS")


class StaticVerifier:
    def __init__(self, result):
        self.result = result
        self.calls = 0
        self.last_run = object()

    def verify(self, changed_files, run_command=None, *, mode="build", scope=None):
        self.calls += 1
        return dict(self.result)


class VerificationOrchestrationTests(unittest.TestCase):
    def make(self, root, verifier, *, main=None, sessions=None, max_fix_loops=2, auto_commit=False):
        executor = ToolExecutor(root, transaction_storage_dir=Path(root) / ".transactions")
        return Orchestrator(
            main or EditingMain(executor), verifier, executor,
            auto_commit=auto_commit, max_fix_loops=max_fix_loops,
            session_manager=sessions,
        )

    def test_repair_runs_quick_failed_checks_and_final_scope(self):
        with tempfile.TemporaryDirectory() as root:
            verifier = StagedVerifier()
            orchestrator = self.make(root, verifier)

            result = orchestrator.handle("fix source")

            self.assertEqual(result["final_status"], "pass")
            self.assertEqual(verifier.scopes, ["AFFECTED", "QUICK", "AFFECTED"])
            self.assertEqual(verifier.failed_calls, 1)
            self.assertEqual([item["stage"] for item in result["verification_loops"]], [
                "initial", "quick", "failed_checks", "final",
            ])
            self.assertEqual(result["runtime_task"]["termination_reason"], "VERIFICATION_PASSED")

    def test_identical_failure_signature_stops_repair_early(self):
        with tempfile.TemporaryDirectory() as root:
            verifier = StaticVerifier(verification("FAIL", signature="same-signature", check_status="FAIL"))
            main = None
            orchestrator = self.make(root, verifier, main=main, max_fix_loops=3)

            result = orchestrator.handle("fix source")

            self.assertEqual(result["final_status"], "repair_no_progress")
            self.assertEqual(verifier.calls, 2)
            self.assertEqual(result["runtime_task"]["termination_reason"], "REPAIR_NO_PROGRESS")
            self.assertIn("REPAIR_NO_PROGRESS", [item["event_type"] for item in result["runtime_task"]["events"]])

    def test_incomplete_blocked_and_timeout_do_not_trigger_repair(self):
        cases = (
            (verification("INCOMPLETE"), "VERIFICATION_INCOMPLETE"),
            (verification("BLOCKED"), "VERIFICATION_BLOCKED"),
            (verification("INCOMPLETE", check_status="TIMEOUT"), "VERIFICATION_TIMEOUT"),
        )
        for verifier_result, reason in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as root:
                verifier = StaticVerifier(verifier_result)
                orchestrator = self.make(root, verifier)
                result = orchestrator.handle("fix source")
                self.assertEqual(verifier.calls, 1)
                self.assertEqual(result["runtime_task"]["termination_reason"], reason)

    def test_required_code_with_no_checks_is_incomplete_and_not_committed(self):
        with tempfile.TemporaryDirectory() as root:
            verifier = StaticVerifier(verification("SKIPPED"))
            orchestrator = self.make(root, verifier, auto_commit=True)

            result = orchestrator.handle("fix source")

            self.assertEqual(result["final_status"], "verification_incomplete")
            self.assertNotIn("commit_sha", result)
            self.assertNotIn("git_commit", result)

    def test_integrity_regression_blocks_success_and_auto_commit(self):
        with tempfile.TemporaryDirectory() as root:
            test_path = Path(root, "tests", "test_source.py")
            test_path.parent.mkdir()
            test_path.write_text(
                "import unittest\n\nclass SourceTests(unittest.TestCase):\n"
                "    def test_one(self):\n        self.assertEqual(1, 1)\n"
                "    def test_two(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            verifier = StagedVerifier()
            executor = ToolExecutor(root, transaction_storage_dir=Path(root) / ".transactions")
            orchestrator = Orchestrator(
                EditingMain(executor, weaken_test=True), verifier, executor,
                auto_commit=True, max_fix_loops=1,
            )

            result = orchestrator.handle("fix source")

            self.assertEqual(result["final_status"], "verification_integrity_blocked")
            self.assertEqual(result["runtime_task"]["termination_reason"], "VERIFICATION_INTEGRITY_BLOCKED")
            self.assertNotIn("commit_sha", result)
            self.assertTrue(result["verification_loops"][-1]["integrity_blocked"])

    def test_runtime_session_and_transaction_retain_evidence_links(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as session_store:
            sessions = SessionManager(root, storage_dir=session_store)
            verifier = StaticVerifier(verification("PASS", check_status="PASS"))
            orchestrator = self.make(root, verifier, sessions=sessions, max_fix_loops=0)

            result = orchestrator.handle("fix source")
            transaction = orchestrator.executor.transactions.get(result["transaction_id"])

            self.assertEqual(result["runtime_task"]["verification"]["evidence_id"], "evidence-test")
            self.assertEqual(transaction.verification["plan_id"], "plan-test")
            self.assertEqual(transaction.verification["evidence_id"], "evidence-test")
            saved = sessions.read_task(result["task_id"])
            self.assertEqual(saved["verification"]["evidence_id"], "evidence-test")


if __name__ == "__main__":
    unittest.main()
