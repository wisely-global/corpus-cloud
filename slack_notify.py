#!/usr/bin/env python3
"""Post ONE alert to the corpus Slack channel as the Slack app (bot), not as a person.

The bot token is never in this repo or this environment's variables. It is a Network secret on the host slack.com:
the platform adds the Authorization header to requests to that host, so this script sends none. If the secret is
missing or the app is not in the channel, Slack answers with an error code and this script exits 3 and says which.

  slack_notify.py --text "message"            (or the message on stdin)
  slack_notify.py --check                     auth.test only: proves the secret works and names the bot; posts nothing

Exit 0 posted / check ok, 3 failed (the reason code is printed; no message content is echoed back).
"""
import argparse, json, os, sys, urllib.request, urllib.error

CHANNEL = os.environ.get("CORPUS_SLACK_CHANNEL", "C0CGZ0K0C14")   # wisley-agent (private); the app must be a member
API = os.environ.get("SLACK_API_BASE", "https://slack.com/api").rstrip("/")
MAX = 39000


def call(method, payload=None):
    req = urllib.request.Request(API + "/" + method, data=json.dumps(payload or {}).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "http_%d" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "request_failed: %s" % type(e).__name__}


HINTS = {
    "not_authed": "no token reached Slack: the Network secret for slack.com is missing or not attached to this environment",
    "invalid_auth": "the token Slack received is not valid (revoked or wrong)",
    "channel_not_found": "the app cannot see the channel: invite it with /invite @appname in that channel",
    "not_in_channel": "the app is not a member of the channel: invite it with /invite @appname",
    "missing_scope": "the app lacks the chat:write scope",
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--text")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        r = call("auth.test")
        if r.get("ok"):
            print(json.dumps({"ok": True, "bot": r.get("user"), "team": r.get("team")}))
            return 0
        print(json.dumps({"ok": False, "error": r.get("error"), "hint": HINTS.get(r.get("error", ""), "")}))
        return 3
    text = a.text if a.text is not None else sys.stdin.read()
    text = text.strip()
    if not text:
        print(json.dumps({"ok": False, "error": "empty message"}))
        return 3
    if len(text) > MAX:
        text = text[:MAX - 40] + "\n[cut: message too long]"
    r = call("chat.postMessage", {"channel": CHANNEL, "text": text, "unfurl_links": False, "unfurl_media": False})
    if r.get("ok"):
        print(json.dumps({"ok": True, "ts": r.get("ts")}))
        return 0
    print(json.dumps({"ok": False, "error": r.get("error"), "hint": HINTS.get(r.get("error", ""), "")}))
    return 3


if __name__ == "__main__":
    sys.exit(main())
