"""Client failure and fee boundaries; real engine HTTP is verified separately."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock


spec = importlib.util.spec_from_file_location(
    "image_engine", Path(__file__).resolve().parents[1] / "scripts/image_engine.py"
)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class ImageEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gofind-engine-")
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.client = engine.EngineClient("http://127.0.0.1:8787", "local-test-only")
        self.job = {"task_id": "content-001", "theme": "a real task", "pages": [{}]}

    def test_token_cannot_be_sent_to_remote_hosts_or_redirect_paths(self):
        for base in ("https://example.com", "http://127.0.0.1.evil.invalid",
                     "http://user:password@127.0.0.1:8787"):
            with self.subTest(base=base), self.assertRaises(ValueError):
                engine.EngineClient(base, "local-test-only")
        with self.assertRaises(ValueError):
            self.client.request("GET", "//example.com/api/engine/status")

    def test_existing_job_approval_does_not_authorize_a_new_submission(self):
        self.job["authorization"] = {"approved": True, "max_image_requests": 9}
        self.client.request = Mock()
        with self.assertRaises(ValueError):
            self.client.submit(self.job, self.output, 0)
        self.client.request.assert_not_called()

    def test_prepare_never_submits_even_when_source_job_is_approved(self):
        self.job["authorization"] = {"approved": True, "max_image_requests": 9}
        self.client.request = Mock(side_effect=[
            {"providers": []},
            {"planner": "local_facts_only", "paid_calls": 0},
            {"initial_image_requests": 1, "paid_calls": 0},
        ])
        self.client.prepare(self.job, self.output)
        calls = self.client.request.call_args_list
        self.assertEqual([call.args[1] for call in calls],
                         ["/api/engine/status", "/api/engine/plan", "/api/engine/estimate"])
        for call in calls[1:]:
            self.assertEqual(call.kwargs["payload"]["authorization"],
                             {"approved": False, "max_image_requests": 0})

    def test_unknown_submission_is_saved_and_never_automatically_retried(self):
        self.client.request = Mock(side_effect=[
            {"initial_image_requests": 1}, engine.EngineError("timeout, result unknown"),
        ])
        with self.assertRaises(engine.EngineError):
            self.client.submit(self.job, self.output, 1)
        receipt = engine.read_json(self.output / "submission.json")
        self.assertEqual(receipt["status"], "unknown")
        self.assertEqual(receipt["task_id"], self.job["task_id"])
        self.assertEqual(self.client.request.call_count, 2)
        self.assertTrue((self.output / "submitted-job.json").is_file())

    def test_insufficient_request_limit_does_not_submit(self):
        self.client.request = Mock(return_value={"initial_image_requests": 3})
        with self.assertRaises(ValueError):
            self.client.submit(self.job, self.output, 2)
        self.assertEqual(self.client.request.call_count, 1)
        self.assertFalse((self.output / "submission.json").exists())

    def test_server_write_failure_is_unknown_not_a_guaranteed_rejection(self):
        self.client.request = Mock(side_effect=[
            {"initial_image_requests": 1}, engine.EngineError("write interrupted", 503),
        ])
        with self.assertRaises(engine.EngineError):
            self.client.submit(self.job, self.output, 1)
        self.assertEqual(engine.read_json(self.output / "submission.json")["status"], "unknown")

    def test_analysis_preserves_business_text_and_page_mapping(self):
        job = {**self.job, "reference_policy": "layout", "pages": [
            {"page_number": 1, "title": "准确标题", "copy": "准确正文", "asset_ids": ["actual-id"]}]}
        self.client.request = Mock(return_value={"analysis": {"pages": [
            {"page_number": 1, "title": "不能采用的改写", "asset_ids": ["fake-id"],
             "visual": "引擎观察到的标题区和区域关系"}]}})
        self.client.analyze(job, self.output, "analysis-001", 1)
        result = engine.read_json(self.output / "analyzed-job.json")
        self.assertEqual(result["pages"][0]["title"], "准确标题")
        self.assertEqual(result["pages"][0]["copy"], "准确正文")
        self.assertEqual(result["pages"][0]["asset_ids"], ["actual-id"])
        self.assertEqual(result["pages"][0]["visual"], "引擎观察到的标题区和区域关系")
        self.assertFalse(result["authorization"]["approved"])
        self.client.request.reset_mock()
        with self.assertRaises(ValueError):
            self.client.analyze(job, self.output, "analysis-002", 0)
        self.client.request.assert_not_called()

    def test_revision_is_explicit_and_keeps_uncertain_results(self):
        revision = {"revision_id": "page-2-fix", "pages": [{"page_number": 2, "visual": "只改本页"}]}
        self.client.request = Mock(side_effect=engine.EngineError("timeout"))
        with self.assertRaises(engine.EngineError):
            self.client.revise("content-001", revision, self.output, 1)
        self.assertEqual(self.client.request.call_count, 1)
        self.assertEqual(engine.read_json(self.output / "revision-state.json")["status"], "unknown")
        self.assertEqual(engine.read_json(self.output / "revision-request.json")["pages"],
                         revision["pages"])
        self.client.request.reset_mock()
        with self.assertRaises(ValueError):
            self.client.revise("content-001", revision, self.output, 0)
        self.client.request.assert_not_called()

    def test_partial_failure_keeps_successful_downloads_without_claiming_a_zip(self):
        png = b"\x89PNG\r\n\x1a\n" + b"unit-test-payload"
        row = {"page_number": 1, "version": 2, "download_url": "/api/engine/jobs/x/pages/1/image",
               "sha256": hashlib.sha256(png).hexdigest()}
        job = {"status": "partial_failed", "completed_pages": 1, "total_pages": 3,
               "pages": [row, {"page_number": 2, "status": "failed"}]}
        self.client.request = Mock(side_effect=[job, png])
        result = self.client.download("x", self.output)
        self.assertEqual(result["images"], ["01-v2.png"])
        self.assertFalse(result["archive_downloaded"])
        self.assertFalse((self.output / "results.zip").exists())
        self.assertEqual((self.output / "01-v2.png").read_bytes(), png)

    def test_html_or_changed_image_is_not_saved_as_a_result(self):
        job = {"status": "completed", "completed_pages": 1, "total_pages": 1,
               "pages": [{"page_number": 1, "version": 1,
                          "download_url": "/api/engine/jobs/x/pages/1/image", "sha256": "0" * 64}]}
        for data in (b"<html>login</html>", b"\x89PNG\r\n\x1a\nwrong-image"):
            self.client.request = Mock(side_effect=[job, data])
            with self.subTest(data=data), self.assertRaises(engine.EngineError):
                self.client.download("x", self.output)
            self.assertFalse((self.output / "01-v1.png").exists())


if __name__ == "__main__":
    unittest.main()
