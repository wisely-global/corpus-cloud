#!/usr/bin/env python3
"""Tests for slack_notify.py against a local fake Slack. Run: python3 test_slack_notify.py"""
import json, os, subprocess, sys, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN = []


class H(BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        SEEN.append((self.path, body, self.headers.get("Authorization")))
        if H.mode == "ok":
            out = {"ok": True, "user": "corpusbot", "team": "Wisely", "ts": "1.2"}
        else:
            out = {"ok": False, "error": H.mode}
        data = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), H); cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def run_cli(self, *args, mode="ok", stdin=None):
        H.mode = mode; SEEN.clear()
        env = dict(os.environ, SLACK_API_BASE="http://127.0.0.1:%d" % self.port)
        p = subprocess.run([sys.executable, os.path.join(HERE, "slack_notify.py")] + list(args), capture_output=True, text=True, env=env, input=stdin)
        return p.returncode, json.loads(p.stdout)

    def test_post_sends_channel_and_text_and_no_token(self):
        rc, out = self.run_cli("--text", "hello")
        self.assertEqual((rc, out["ok"]), (0, True))
        path, body, auth = SEEN[0]
        self.assertEqual(path, "/chat.postMessage"); self.assertEqual(body["channel"], "C0CGZ0K0C14"); self.assertEqual(body["text"], "hello")
        self.assertIsNone(auth)   # the platform adds the Authorization header; the script never sends one

    def test_stdin_and_check(self):
        rc, out = self.run_cli(stdin="from stdin\n"); self.assertEqual(rc, 0); self.assertEqual(SEEN[0][1]["text"], "from stdin")
        rc, out = self.run_cli("--check"); self.assertEqual((rc, out["bot"]), (0, "corpusbot")); self.assertEqual(SEEN[0][0], "/auth.test")

    def test_errors_exit_3_with_hint_and_no_content_echo(self):
        for code in ("not_authed", "not_in_channel", "channel_not_found"):
            rc, out = self.run_cli("--text", "secret-ish body", mode=code)
            self.assertEqual(rc, 3); self.assertEqual(out["error"], code); self.assertTrue(out["hint"]); self.assertNotIn("secret-ish", json.dumps(out))

    def test_empty_and_long(self):
        rc, out = self.run_cli("--text", "   "); self.assertEqual(rc, 3)
        rc, out = self.run_cli("--text", "x" * 50000); self.assertEqual(rc, 0); self.assertLessEqual(len(SEEN[0][1]["text"]), 39000)


if __name__ == "__main__":
    unittest.main()
