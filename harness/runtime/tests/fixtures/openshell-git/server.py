"""Controlled Git HTTPS remote for LOCAL qualification only.

Python is a test-fixture dependency, not a shipped harness dependency. Mount the
TLS key and synthetic credential only on the remote pod, never the agent sandbox.
The server never logs request headers, credential values or Git request bodies.
"""
import base64
import json
import os
from pathlib import Path
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path("/tmp/ow-git-fixture")
SECRETS = Path("/fixture-secrets")
LOCK = threading.Lock()
COUNTS = {"authenticated": 0, "unauthenticated": 0, "upload_pack": 0, "receive_pack": 0}


def git(*args, cwd=ROOT):
    result = subprocess.run(
        ["/usr/bin/git", "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid", *args],
        cwd=cwd, capture_output=True, timeout=15,
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
    )
    if result.returncode:
        raise RuntimeError("Fixture repository setup failed")
    return result.stdout.decode().strip()


ROOT.mkdir(mode=0o700)
(ROOT / "org").mkdir()
(ROOT / "work").mkdir()
git("init", "-q", "work")
(ROOT / "work/proof.txt").write_text("original source\n")
git("add", "proof.txt", cwd=ROOT / "work")
git("commit", "-qm", "base", cwd=ROOT / "work")
BASE = git("rev-parse", "HEAD", cwd=ROOT / "work")
git("clone", "--bare", "work", "org/project.git")
git("--git-dir=org/project.git", "config", "http.receivepack", "true")
# Deliberate REMOTE enforcement. OpenShell checks HTTP endpoints/methods; it is
# not claimed to understand or restrict branch names embedded in a Git pack.
hook = ROOT / "org/project.git/hooks/pre-receive"
hook.write_text("#!/bin/sh\nwhile read old new ref; do\n case \"$ref\" in refs/heads/agents/*) ;; *) exit 1;; esac\ndone\n")
hook.chmod(0o700)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def respond(self, code, data=b"", headers=None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.handle_git()

    def do_POST(self):
        self.handle_git()

    def body(self):
        limit = 4 * 1024 * 1024
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            parts, total = [], 0
            while True:
                line = self.rfile.readline(128)
                size = int(line.split(b";", 1)[0].strip(), 16)
                if size == 0:
                    while self.rfile.readline(1024) != b"\r\n":
                        pass
                    return b"".join(parts)
                total += size
                if total > limit:
                    raise ValueError("Fixture request too large")
                parts.append(self.rfile.read(size))
                if self.rfile.read(2) != b"\r\n":
                    raise ValueError("Invalid chunk")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 <= size <= limit:
            raise ValueError("Fixture request too large")
        return self.rfile.read(size)

    def handle_git(self):
        path = urlsplit(self.path)
        if path.path == "/__status" and self.command == "GET":
            with LOCK:
                data = {"base": BASE, "counts": COUNTS.copy(), "refs": git("--git-dir=org/project.git", "show-ref")}
            self.respond(200, json.dumps(data).encode(), {"Content-Type": "application/json"})
            return
        # Only synthetic fixture credentials are accepted. A placeholder reaching
        # upstream unchanged fails authentication, proving rewriting is needed.
        token = SECRETS.joinpath("token").read_text().strip()
        auth = self.headers.get("Authorization", "")
        valid = auth == "Bearer " + token
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:], validate=True).decode()
                valid = decoded.split(":", 1)[1] == token
            except (ValueError, IndexError, UnicodeError):
                valid = False
        if not valid:
            with LOCK:
                COUNTS["unauthenticated"] += 1
            self.close_connection = True
            self.respond(401, b"Authentication required", {"WWW-Authenticate": 'Basic realm="local-git-fixture"', "Connection": "close"})
            return
        if not path.path.startswith("/org/project.git/"):
            self.respond(404)
            return
        data = self.body()
        with LOCK:
            COUNTS["authenticated"] += 1
            if path.path.endswith("/git-upload-pack"):
                COUNTS["upload_pack"] += 1
            if path.path.endswith("/git-receive-pack"):
                COUNTS["receive_pack"] += 1
        env = {
            "PATH": "/usr/bin:/bin", "GIT_PROJECT_ROOT": str(ROOT), "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": path.path, "QUERY_STRING": path.query,
            "REQUEST_METHOD": self.command, "CONTENT_TYPE": self.headers.get("Content-Type", ""),
            "CONTENT_LENGTH": str(len(data)), "REMOTE_USER": "fixture",
            "HTTP_GIT_PROTOCOL": self.headers.get("Git-Protocol", ""),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        }
        result = subprocess.run(["/usr/lib/git-core/git-http-backend"], input=data, env=env, capture_output=True, timeout=20)
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            self.respond(502, b"Fixture Git backend failed")
            return
        header, body = result.stdout.split(b"\r\n\r\n", 1)
        headers, code = {}, 200
        for line in header.decode().splitlines():
            key, value = line.split(":", 1)
            if key.lower() == "status":
                code = int(value.strip().split()[0])
            elif key.lower() != "content-length":
                headers[key] = value.strip()
        self.respond(code, body, headers)


server = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(SECRETS / "server.crt", SECRETS / "server.key")
server.socket = context.wrap_socket(server.socket, server_side=True)
print("Controlled Git TLS fixture ready", flush=True)
server.serve_forever()
