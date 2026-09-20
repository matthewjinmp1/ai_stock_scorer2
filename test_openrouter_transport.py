import io
import json
import threading
import time
import unittest
from unittest import mock
import openrouter_transport as transport
import server


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/event-stream"}


class TransportTests(unittest.TestCase):
    def test_provider_candidates_are_distinct_and_respect_blocklist(self):
        def endpoint(name, tag, cost):
            return {"provider_name": name, "tag": tag, "status": 0,
                    "supported_parameters": ["max_tokens", "reasoning", "reasoning_effort", "temperature"],
                    "pricing": {"prompt": "0.001", "completion": str(cost)}}
        endpoints = [endpoint("First", "first/a", 1), endpoint("First", "first/b", 2),
                     endpoint("Second", "second", 3), endpoint("Blocked", "blocked", 0)]
        with mock.patch.object(server, "openrouter_model_endpoints", return_value=endpoints), mock.patch.object(server, "provider_blocklist", return_value=["blocked"]):
            candidates = server.connection_providers("deepseek/deepseek-v4-flash-0731", "high", 200)
        self.assertEqual([c["tag"] for c in candidates], ["first/a", "second"])

    def test_stream_keeps_reasoning_content_finish_and_usage(self):
        chunks = [
            {"provider": "Example", "choices": [{"delta": {"reasoning": "Thinking"}}]},
            {"choices": [{"delta": {"content": "Score: "}}]},
            {"choices": [{"delta": {"content": "77"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"completion_tokens": 10}},
        ]
        data = b": heartbeat\n\n" + b"".join(("data: " + json.dumps(c) + "\n\n").encode() for c in chunks) + b"data: [DONE]\n\n"
        result = transport.read_completion(Response(data))
        self.assertEqual(result["choices"][0]["message"]["content"], "Score: 77")
        self.assertEqual(result["choices"][0]["message"]["reasoning"], "Thinking")
        self.assertEqual(result["usage"]["completion_tokens"], 10)

    def test_live_progress_reports_headers_then_stream_activity(self):
        request = transport.urllib.request.Request("https://example.com")
        events = []
        request.progress_callback = events.append
        response = Response(b'data: {"choices":[{"delta":{"content":"77"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        with mock.patch.object(transport.urllib.request, "urlopen", return_value=response):
            transport.fetch_completion(request, 1, {})
        self.assertEqual(events[0], "connected")
        self.assertIn("activity", events[1:])

    def test_truncated_stream_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "before the response completed"):
            transport.read_completion(Response(b'data: {"choices":[{"delta":{"content":"Score: 7"}}]}\n\n'))

    def test_stream_error_is_not_accepted_as_answer(self):
        result = transport.read_completion(Response(b'data: {"error":{"message":"upstream failed"}}\n\n'))
        self.assertEqual(result["error"]["message"], "upstream failed")

    def test_connection_deadline_includes_stalled_headers(self):
        release = threading.Event()
        def connect(*args, **kwargs):
            release.wait(1)
            return Response()
        timing = {}
        with mock.patch.object(transport.urllib.request, "urlopen", side_effect=connect):
            try:
                with self.assertRaises(transport.ConnectionDeadline):
                    transport.fetch_completion(None, 1, timing, connection_timeout=0.02)
            finally:
                release.set()
        self.assertGreaterEqual(timing["connection_ms"], 15)
        self.assertNotIn("response_ms", timing)

    def test_generation_can_exceed_connection_limit(self):
        response = Response()
        def generate(_response, on_activity=None):
            time.sleep(0.06)
            return {"answer": "done"}
        timing = {}
        with mock.patch.object(transport.urllib.request, "urlopen", return_value=response), mock.patch.object(transport, "read_completion", side_effect=generate):
            payload, _, _ = transport.fetch_completion(None, 1, timing, connection_timeout=0.03)
        self.assertEqual(payload, {"answer": "done"})
        self.assertGreaterEqual(timing["response_ms"], 50)

    def test_active_generation_outlives_inactivity_limit(self):
        def generate(response, on_activity):
            for _ in range(8):
                time.sleep(0.015)
                on_activity()
            return {"answer": "done"}
        with mock.patch.object(transport.urllib.request, "urlopen", return_value=Response()), mock.patch.object(transport, "read_completion", side_effect=generate):
            payload, _, _ = transport.fetch_completion(None, 0.08, {})
        self.assertEqual(payload, {"answer": "done"})

    def test_stall_after_content_times_out_and_records_activity(self):
        release = threading.Event()
        timing = {}
        def generate(response, on_activity):
            on_activity()
            release.wait(1)
        with mock.patch.object(transport.urllib.request, "urlopen", return_value=Response()), mock.patch.object(transport, "read_completion", side_effect=generate):
            try:
                with self.assertRaisesRegex(TimeoutError, "no new answer"):
                    transport.fetch_completion(None, 0.03, timing)
            finally:
                release.set()
        self.assertTrue(timing["received_content"])
        self.assertGreaterEqual(timing["content_idle_ms"], 25)

    def test_heartbeats_and_empty_chunks_do_not_count_as_content(self):
        activity = mock.Mock()
        data = b': heartbeat\n\ndata: {"choices":[{"delta":{"content":""}}]}\n\ndata: {"choices":[{"delta":{"reasoning":"thinking"}}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        transport.read_completion(Response(data), activity)
        activity.assert_called_once()

    def test_generation_deadline_is_separate(self):
        release = threading.Event()
        with mock.patch.object(transport.urllib.request, "urlopen", return_value=Response()), mock.patch.object(transport, "read_completion", side_effect=lambda r, on_activity=None: release.wait(1)):
            try:
                with self.assertRaisesRegex(TimeoutError, "no new answer or reasoning content"):
                    transport.fetch_completion(None, 0.02, {}, connection_timeout=1)
            finally:
                release.set()
