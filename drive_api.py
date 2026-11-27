#!/usr/bin/env python3
"""The few Google Drive API v3 calls R tidy needs, as the service account in the environment.

Why this exists: `rclone backend moveid` cannot move a native Google Doc server-side. It falls back to copy then
delete, and the delete trashes the original while the copy never arrives (seen on Flo & Frankie, 28 Nov 2026).
A Drive parent change (`files.update` with addParents/removeParents) is metadata only, works for every file type,
and is what the Drive UI does. Nothing here deletes or trashes; `untrash` exists only to undo a trashing, and `rename` is how a shadow corpus goes live.

Key: GDRIVE_SA_JSON_B64 (or GDRIVE_SA_JSON), the same key rclone uses. It is held in memory only and never printed.
Needs `cryptography`, or the `openssl` command, to sign the token request.
"""
import base64, json, os, shutil, ssl, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request

SCOPE = "https://www.googleapis.com/auth/drive"
API = "https://www.googleapis.com/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
FIELDS = "id,name,trashed,parents,mimeType,md5Checksum,size"


def _key():
    raw = os.environ.get("GDRIVE_SA_JSON", "")
    if os.environ.get("GDRIVE_SA_JSON_B64"):
        raw = base64.b64decode(os.environ["GDRIVE_SA_JSON_B64"]).decode("utf-8")
    if not raw:
        raise RuntimeError("no service-account key in the environment (GDRIVE_SA_JSON_B64 or GDRIVE_SA_JSON)")
    return json.loads(raw)


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def _sign(pem, data):
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        d = tempfile.mkdtemp(prefix="k-")
        try:
            path = os.path.join(d, "k")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(pem)
            p = subprocess.run(["openssl", "dgst", "-sha256", "-sign", path], input=data, capture_output=True)
            if p.returncode != 0:
                raise RuntimeError("openssl could not sign the token request")
            return p.stdout
        finally:
            shutil.rmtree(d, ignore_errors=True)
    return serialization.load_pem_private_key(pem.encode("utf-8"), password=None).sign(data, padding.PKCS1v15(), hashes.SHA256())


def _q(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


class DriveApi:
    def __init__(self, drive_id=None, urlopen=None):
        self.drive_id = drive_id or os.environ.get("CLIENTS_DRIVE_ID")      # a shared drive's id is its root folder's id
        self._token, self._exp = None, 0
        self._urlopen = urlopen or self._real_urlopen
        cafile = os.environ.get("SSL_CERT_FILE") or "/root/.ccr/ca-bundle.crt"
        self._ctx = ssl.create_default_context(cafile=cafile if os.path.exists(cafile) else None)

    def _real_urlopen(self, req):
        return urllib.request.urlopen(req, context=self._ctx, timeout=60)

    def _auth(self):
        if self._token and time.time() < self._exp - 60:
            return self._token
        key, now = _key(), int(time.time())
        head = _b64u(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        body = _b64u(json.dumps({"iss": key["client_email"], "scope": SCOPE, "aud": key["token_uri"], "iat": now, "exp": now + 3000}).encode())
        sig = _b64u(_sign(key["private_key"], head + b"." + body))
        data = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": (head + b"." + body + b"." + sig).decode()}).encode()
        try:
            r = json.loads(self._urlopen(urllib.request.Request(key["token_uri"], data=data, method="POST")).read())
        except urllib.error.HTTPError as e:
            raise RuntimeError("Drive token request failed (HTTP %d)" % e.code)
        self._token, self._exp = r["access_token"], now + int(r.get("expires_in", 3000))
        return self._token

    def _call(self, method, url, body=None):
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method)
        req.add_header("Authorization", "Bearer " + self._auth())
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            return json.loads(self._urlopen(req).read() or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError("Drive API %s failed (HTTP %d)" % (method, e.code))

    def get(self, fid):
        return self._call("GET", "%s/%s?supportsAllDrives=true&fields=%s" % (API, urllib.parse.quote(fid), FIELDS))

    def move(self, fid, new_parent, old_parent):
        """Change a file's parent. Metadata only: the file keeps its id, content and history."""
        url = "%s/%s?supportsAllDrives=true&addParents=%s&removeParents=%s&fields=%s" % (
            API, urllib.parse.quote(fid), urllib.parse.quote(new_parent), urllib.parse.quote(old_parent), FIELDS)
        return self._call("PATCH", url, {})

    def rename(self, fid, name):
        """Rename a file or folder. Metadata only, so it is atomic and every id inside is kept (promoting a shadow corpus)."""
        return self._call("PATCH", "%s/%s?supportsAllDrives=true&fields=%s" % (API, urllib.parse.quote(fid), FIELDS), {"name": name})

    def untrash(self, fid):
        return self._call("PATCH", "%s/%s?supportsAllDrives=true&fields=%s" % (API, urllib.parse.quote(fid), FIELDS), {"trashed": False})

    def find_folder(self, parent, name):
        q = "'%s' in parents and name = '%s' and mimeType = '%s' and trashed = false" % (parent, _q(name), FOLDER)
        url = "%s?q=%s&supportsAllDrives=true&includeItemsFromAllDrives=true&corpora=drive&driveId=%s&fields=files(id,createdTime)&pageSize=10" % (
            API, urllib.parse.quote(q), urllib.parse.quote(self.drive_id))
        found = sorted(self._call("GET", url).get("files", []), key=lambda f: f.get("createdTime", ""))
        return found[0]["id"] if found else None

    def ensure_path(self, parts):
        """Folder id for a path of folder names under the drive root, creating what is missing."""
        parent = self.drive_id
        for name in [p for p in parts if p]:
            fid = self.find_folder(parent, name)
            if not fid:
                fid = self._call("POST", "%s?supportsAllDrives=true&fields=id" % API, {"name": name, "mimeType": FOLDER, "parents": [parent]})["id"]
            parent = fid
        return parent
