"""Offline checks for API handoff limits, log isolation and file-version guards."""
import contextlib
import errno
import io
import json
from pathlib import Path
import tempfile
import socket
from types import SimpleNamespace as Args
import unittest
from unittest.mock import patch

import deepseek_collab as app


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "project"
        self.workspace.mkdir()
        self.root = self.base / "sessions"
        self.patcher = patch.object(app, "state_root", return_value=self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.key_patch = patch.object(app, "api_key", return_value="FAKE_TEST_KEY_123")
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        self.task = self.base / "task.txt"
        self.task.write_text("Review arithmetic and correct it.", encoding="utf-8")
        self.message = self.base / "message.txt"
        self.message.write_text("Check denominator and edge cases.", encoding="utf-8")
        self.source = self.workspace / "sample.py"
        self.source.write_text("def mean(xs): return sum(xs) / (len(xs) - 1)\n", encoding="utf-8")

    def quiet(self, fn, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = fn(args)
        return code, json.loads(output.getvalue())

    def init(self, max_turns=3):
        _, result = self.quiet(app.do_init, Args(workspace=str(self.workspace), task_file=str(self.task), max_turns=max_turns))
        return Path(result["session"])

    def turn_args(self, session, files=None):
        return Args(session=str(session), message_file=str(self.message), file=files or ["sample.py"],
                    model="deepseek-flash", effort="high", max_tokens=2048)

    def reply(self):
        return {"choices": [{"message": {"content": "Use len(xs).", "reasoning_content": "PRIVATE_NOT_FOR_LOGS"},
                             "finish_reason": "stop"}], "usage": {"total_tokens": 99}}

    def test_real_handoff_shape_and_only_public_answer_saved(self):
        session = self.init()
        with patch.object(app, "request_json", return_value=self.reply()) as request:
            _, result = self.quiet(app.do_turn, self.turn_args(session))
        payload = request.call_args.args[2]
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"]["type"], "enabled")
        packet = json.loads(payload["messages"][1]["content"])
        self.assertEqual(packet["current_files"][0]["content"], self.source.read_bytes().decode("utf-8"))
        self.assertEqual(result["reply"], "Use len(xs).")
        raw = (session / "session.json").read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_NOT_FOR_LOGS", raw)
        self.assertNotIn("FAKE_TEST_KEY_123", raw)
        self.assertFalse((session / ".lock").exists())

    def test_changed_source_detected_before_write(self):
        session = self.init()
        with patch.object(app, "request_json", return_value=self.reply()):
            self.quiet(app.do_turn, self.turn_args(session))
        code, before = self.quiet(app.do_verify, Args(session=str(session)))
        self.assertEqual(code, 0)
        self.assertEqual(before["checked_files"], 1)
        self.source.write_text("changed by another participant", encoding="utf-8")
        code, after = self.quiet(app.do_verify, Args(session=str(session)))
        self.assertEqual(code, 2)
        self.assertEqual(after["changed_files"], ["sample.py"])

    def test_failures_count_toward_limit_and_do_not_retry(self):
        session = self.init(max_turns=1)
        with patch.object(app, "request_json", side_effect=app.CollabError("DeepSeek HTTP 402.")) as request:
            with self.assertRaises(app.CollabError):
                app.do_turn(self.turn_args(session))
            with self.assertRaisesRegex(app.CollabError, "limit reached"):
                app.do_turn(self.turn_args(session))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(app.load_state(session)["attempts"], 1)
        self.assertFalse((session / ".lock").exists())

    def test_outside_credentials_binary_and_large_attachments_rejected(self):
        outside = self.base / "outside.py"
        outside.write_text("unrelated private text", encoding="utf-8")
        for name, data in [(".env", b"PRIVATE=1"), ("blob.bin", b"x\x00y"), ("large.py", b"x" * 65537)]:
            (self.workspace / name).write_bytes(data)
            with self.assertRaises(app.CollabError):
                app.snapshot(self.workspace, name)
        with self.assertRaises(app.CollabError):
            app.snapshot(self.workspace, "../outside.py")

    def test_session_isolation_history_and_finish(self):
        first, second = self.init(), self.init()
        with patch.object(app, "request_json", return_value=self.reply()) as request:
            self.quiet(app.do_turn, self.turn_args(first))
            self.quiet(app.do_turn, self.turn_args(first))
        packet = json.loads(request.call_args.args[2]["messages"][1]["content"])
        self.assertEqual(len(packet["previous_public_exchanges"]), 1)
        self.assertEqual(app.load_state(second)["turns"], [])
        summary = self.base / "summary.txt"
        summary.write_text("Verified corrected denominator.", encoding="utf-8")
        self.quiet(app.do_finish, Args(session=str(first), summary_file=str(summary)))
        with self.assertRaisesRegex(app.CollabError, "finished"):
            app.do_turn(self.turn_args(first))

    def test_secret_and_oversized_input_stop_before_request(self):
        session = self.init()
        self.message.write_text("accidental FAKE_TEST_KEY_123", encoding="utf-8")
        with patch.object(app, "request_json") as request:
            with self.assertRaises(app.CollabError):
                app.do_turn(self.turn_args(session))
        request.assert_not_called()
        self.assertEqual(app.load_state(session)["attempts"], 0)
        self.assertNotIn("FAKE_TEST_KEY_123", (session / "discussion.md").read_text(encoding="utf-8"))
        with self.assertRaises(app.CollabError):
            app.build_payload(app.load_state(session), "x" * 120001, [], "deepseek-flash", "high", 8192)

    def test_session_lock_prevents_concurrent_turns(self):
        session = self.init()
        with app.session_lock(session):
            with self.assertRaisesRegex(app.CollabError, "in use"):
                app.do_turn(self.turn_args(session))

    def test_windows_mapped_folder_fallback(self):
        target = self.base / "state.json"
        with patch.object(app.os, "replace", side_effect=OSError(errno.EXDEV, "mapped folder")):
            app.atomic_json(target, {"valid": True})
        self.assertEqual(json.loads(target.read_text()), {"valid": True})
        self.assertEqual(list(self.base.glob("*.tmp")), [])

    def test_empty_reply_preserves_finish_usage_without_private_reasoning(self):
        session = self.init()
        reply = self.reply()
        reply['choices'][0]['message']['content'] = ''
        reply['choices'][0]['finish_reason'] = 'length'
        with patch.object(app, 'request_json', return_value=reply):
            with self.assertRaisesRegex(app.CollabError, 'finish_reason=length'):
                app.do_turn(self.turn_args(session))
        turn = app.load_state(session)['turns'][0]
        self.assertEqual(turn['finish_reason'], 'length')
        self.assertEqual(turn['usage']['total_tokens'], 99)
        self.assertTrue(turn['completed_at'])
        self.assertGreater(turn['diagnostics']['reasoning_chars'], 0)
        self.assertNotIn('PRIVATE_NOT_FOR_LOGS', (session / 'session.json').read_text())

    def test_stream_collects_public_text_and_final_usage(self):
        chunks = [
            {'choices': [{'index': 0, 'delta': {'reasoning_content': 'PRIVATE'}}]},
            {'choices': [{'index': 0, 'delta': {'content': 'answer'}}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}],
             'usage': {'total_tokens': 17}},
        ]
        wire = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
        result = app.read_stream(io.BytesIO(wire.encode()))
        self.assertEqual(result['choices'][0]['message']['content'], 'answer')
        self.assertEqual(result['usage']['total_tokens'], 17)
        self.assertEqual(result['_reasoning_chars'], 7)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_incomplete_stream_is_not_accepted_as_complete_reply(self):
        wire = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        with self.assertRaisesRegex(app.CollabError, 'without a complete final event'):
            app.read_stream(io.BytesIO(wire))

    def test_network_failure_keeps_exception_kind_without_exception_text(self):
        failure = app.urllib.error.URLError(socket.gaierror('PRIVATE_HOST_OR_SECRET'))
        with patch.object(app.urllib.request.OpenerDirector, 'open', side_effect=failure):
            with self.assertRaises(app.CollabError) as caught:
                app.request_json('/chat/completions', 'FAKE_TEST_KEY_123', {})
        self.assertEqual(caught.exception.diagnostics['reason_type'], 'gaierror')
        self.assertNotIn('PRIVATE', str(caught.exception))


if __name__ == "__main__":
    unittest.main()
