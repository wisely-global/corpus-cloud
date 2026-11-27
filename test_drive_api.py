#!/usr/bin/env python3
"""Unit tests for drive_api.py. No network: a fake urlopen answers. Run: python3 test_drive_api.py"""
import io, json, os, sys, unittest, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive_api as da


def pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


class Resp:
    def __init__(self, obj):
        self.b = json.dumps(obj).encode()

    def read(self):
        return self.b


class Fake:
    """Answers the token request and records every Drive call."""
    def __init__(self, folders=None):
        self.calls, self.folders, self.n = [], folders or {}, 0

    def __call__(self, req):
        url, method = req.full_url, req.get_method()
        body = json.loads(req.data) if req.data and req.headers.get("Content-type") == "application/json" else None
        if "token" in url and method == "POST" and not body:
            return Resp({"access_token": "t", "expires_in": 3000})
        self.calls.append((method, url, body))
        if method == "GET" and "q=" in url:
            q = urllib.parse.unquote(url.split("q=")[1].split("&")[0])
            key = (q.split("'")[1], q.split("name = '")[1].split("'")[0])
            return Resp({"files": [{"id": self.folders[key], "createdTime": "1"}] if key in self.folders else []})
        if method == "POST":
            self.n += 1
            return Resp({"id": "new%d" % self.n})
        return Resp({"id": "x", "parents": ["P"], "trashed": False})


class Api(unittest.TestCase):
    def setUp(self):
        os.environ["GDRIVE_SA_JSON"] = json.dumps({"client_email": "sa@x", "private_key": pem(), "token_uri": "https://oauth2.example/token"})
        os.environ.pop("GDRIVE_SA_JSON_B64", None)

    def test_move_is_a_parent_change_and_never_a_trash(self):
        f = Fake()
        da.DriveApi("DRIVE", f).move("FILE", "NEW", "OLD")
        method, url, body = f.calls[0]
        self.assertEqual(method, "PATCH")
        self.assertIn("addParents=NEW", url)
        self.assertIn("removeParents=OLD", url)
        self.assertNotIn("trashed", json.dumps(body))

    def test_untrash_is_the_only_call_that_touches_trashed(self):
        f = Fake()
        da.DriveApi("DRIVE", f).untrash("FILE")
        self.assertEqual(f.calls[0][2], {"trashed": False})

    def test_ensure_path_reuses_existing_folders_and_creates_the_rest(self):
        f = Fake({("DRIVE", "Client"): "C1", ("C1", "Archived"): "A1"})
        fid = da.DriveApi("DRIVE", f).ensure_path(["Client", "Archived", "id8"])
        posts = [c for c in f.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["parents"], ["A1"])
        self.assertEqual(posts[0][2]["name"], "id8")
        self.assertEqual(fid, "new1")

    def test_a_quote_in_a_folder_name_is_escaped(self):
        self.assertEqual(da._q("Kid's \\ x"), "Kid\\'s \\\\ x")

    def test_no_key_is_a_clear_error(self):
        os.environ.pop("GDRIVE_SA_JSON")
        with self.assertRaises(RuntimeError):
            da.DriveApi("D", Fake())._auth()


if __name__ == "__main__":
    unittest.main()
