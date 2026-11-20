"""Single-writer workspace controller. SQLite intents survive pod and API restarts."""
import fcntl
import hmac
import json
import os
import re
import sqlite3
import ssl
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cluster import Cluster, Failure


def identity(prefix):
    return prefix + "-" + uuid.uuid4().hex


def required(row):
    if row is None:
        raise Failure("workspace_not_found", 404)
    return row


def profile(options, default="small"):
    if not isinstance(options, dict) or options.keys() - {"podProfile"}:
        raise Failure("invalid_workspace_request", 400)
    value = options.get("podProfile", default)
    if value not in ("small", "medium"):
        raise Failure("invalid_workspace_request", 400)
    return value


class Controller:
    def __init__(self):
        # Recreate deployment plus storage lock prevents simultaneous controllers.
        self.lock_file = open("/state/writer.lock", "a")
        fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lock = threading.Lock()
        self.inflight = {}
        self.db = sqlite3.connect("/state/workspaces.sqlite", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS installation(id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS data(id TEXT PRIMARY KEY, uid TEXT, deleting INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS keys(key TEXT PRIMARY KEY, data TEXT NOT NULL REFERENCES data ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS compute(id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL REFERENCES keys,
                profile TEXT NOT NULL, uid TEXT, stopping INTEGER DEFAULT 0, idle REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attachments(workspace TEXT REFERENCES compute ON DELETE CASCADE,
                session TEXT, active INTEGER NOT NULL, PRIMARY KEY(workspace,session));
        """)
        with self.db:
            if not self.row("SELECT id FROM installation"):
                self.db.execute("INSERT INTO installation VALUES(?)", (uuid.uuid4().hex,))
        self.cluster = Cluster(self.row("SELECT id FROM installation")["id"])
        self.idle = int(os.getenv("IDLE_SECONDS", "600"))

    def row(self, sql, *parameters):
        return self.db.execute(sql, parameters).fetchone()

    def compute(self, workspace):
        return required(self.row("""SELECT c.*,k.data FROM compute c JOIN keys k ON k.key=c.key
             WHERE c.id=?""", workspace))

    def provision(self, row):
        if row["stopping"]:
            raise Failure("workspace_in_use", 409)
        data = required(self.row("SELECT * FROM data WHERE id=?", row["data"]))
        if data["deleting"]:
            raise Failure("workspace_in_use", 409)
        uid = self.cluster.claim(data["id"], data["uid"])
        if not data["uid"]:
            with self.db:
                self.db.execute("UPDATE data SET uid=? WHERE id=?", (uid, data["id"]))
        previous = row["uid"]
        if previous and self.cluster.resource("pod", row["id"], previous) is None:
            # The replacement intent is durable before asking Kubernetes to create.
            with self.db:
                self.db.execute("UPDATE compute SET uid=NULL WHERE id=?", (row["id"],))
            previous = None
        pod = self.cluster.pod(row["id"], data["id"], row["profile"], previous)
        if pod.metadata.uid != previous:
            with self.db:
                self.db.execute("UPDATE compute SET uid=? WHERE id=?", (pod.metadata.uid, row["id"]))
        return pod

    def open(self, req):
        key = req.get("key")
        if not isinstance(key, str) or not 0 < len(key.encode()) <= 512 or any(ord(c) < 32 for c in key):
            raise Failure("invalid_workspace_request", 400)
        selected = profile(req.get("options", {}), profile({"podProfile": req["defaultProfile"]}))
        requested = req.get("dataId")
        with self.db:
            bound = self.row("SELECT data FROM keys WHERE key=?", key)
            if bound:
                data_id = bound["data"]
                if requested is not None and requested != data_id:
                    raise Failure("workspace_data_mismatch", 409)
            else:
                data_id = requested if requested is not None else identity("data")
                if requested is None:
                    self.db.execute("INSERT INTO data(id) VALUES(?)", (data_id,))
                self.db.execute("INSERT INTO keys VALUES(?,?)", (key, self.live_data(data_id)))
            self.live_data(data_id)
            current = self.row("SELECT * FROM compute WHERE key=?", key)
            if current:
                if current["profile"] != selected:
                    raise Failure("workspace_profile_mismatch", 409)
                workspace = current["id"]
            else:
                if (self.cluster.mode == "ReadWriteOncePod" or os.getenv("ALLOW_SHARED_COMPUTE") == "false") and self.row(
                    "SELECT 1 FROM compute c JOIN keys k ON k.key=c.key WHERE k.data=?", data_id):
                    raise Failure("workspace_shared_mount_unsupported", 409)
                workspace = identity("ws")
                self.db.execute("INSERT INTO compute(id,key,profile,idle) VALUES(?,?,?,?)",
                                (workspace, key, selected, time.time()))
        self.provision(self.compute(workspace))
        with self.db:
            self.db.execute("UPDATE compute SET idle=? WHERE id=?", (time.time(), workspace))
        return {"workspaceId": workspace, "dataId": data_id}

    def live_data(self, data):
        row = required(self.row("SELECT deleting FROM data WHERE id=?", data))
        if row["deleting"]:
            raise Failure("workspace_in_use", 409)
        return data

    def drain(self, kind, name, uid):
        # Persist intent before dispatch; return busy on timeout so retry operates
        # on the original UID. Background reconciliation resumes interrupted deletes.
        deadline = time.monotonic() + 6
        while not self.cluster.remove(kind, name, uid):
            if time.monotonic() >= deadline:
                raise Failure("workspace_in_use", 409)
            time.sleep(.2)

    def stop(self, workspace):
        row = self.row("SELECT * FROM compute WHERE id=?", workspace)
        if row is None:
            return
        if self.row("SELECT 1 FROM attachments WHERE workspace=? AND active=1", workspace):
            raise Failure("workspace_in_use", 409)
        with self.db:
            self.db.execute("UPDATE compute SET stopping=1 WHERE id=?", (workspace,))
        self.drain("pod", workspace, row["uid"])
        with self.db:
            self.db.execute("DELETE FROM compute WHERE id=?", (workspace,))

    def delete(self, data):
        row = self.row("SELECT * FROM data WHERE id=?", data)
        if row is None:
            return
        if self.row("SELECT 1 FROM compute c JOIN keys k ON k.key=c.key WHERE k.data=?", data):
            raise Failure("workspace_in_use", 409)
        with self.db:
            self.db.execute("UPDATE data SET deleting=1 WHERE id=?", (data,))
        self.drain("pvc", data, row["uid"])
        with self.db:
            self.db.execute("DELETE FROM data WHERE id=?", (data,))

    def reconcile(self):
        while True:
            time.sleep(3)
            with self.lock:
                pending = list(self.db.execute("""SELECT id FROM compute c WHERE stopping=1 OR
                    (? > 0 AND idle < ? AND NOT EXISTS(
                        SELECT 1 FROM attachments a WHERE a.workspace=c.id AND a.active=1))""",
                    (self.idle,time.time()-self.idle)))
                deleting = list(self.db.execute("SELECT id FROM data WHERE deleting=1"))
                for fn, rows in [(self.stop, pending), (self.delete, deleting)]:
                    for row in rows:
                        try:
                            fn(row["id"])
                        except Exception as exc:
                            print("cleanup retry", row["id"], type(exc).__name__, flush=True)

    def call(self, action, req):
        with self.lock:
            if action == "start":
                return {"profile": profile(req.get("options", {}))}
            if action == "open":
                return self.open(req)
            if action == "delete":
                self.delete(req["dataId"])
                return {}
            workspace = req["workspaceId"]
            if action == "stop":
                self.stop(workspace)
                return {}
            if action == "detach":
                if self.inflight.get((workspace, req["sessionId"]), 0):
                    raise Failure("workspace_in_use", 409)
                with self.db:
                    self.db.execute("""INSERT INTO attachments
                        SELECT ?,?,0 WHERE EXISTS(SELECT 1 FROM compute WHERE id=?)
                        ON CONFLICT(workspace,session) DO UPDATE SET active=0""",
                        (workspace, req["sessionId"], workspace))
                    self.db.execute("UPDATE compute SET idle=? WHERE id=?", (time.time(), workspace))
                return {}
            row = self.compute(workspace)
            if row["stopping"]:
                raise Failure("workspace_in_use", 409)
            if action == "recover":
                self.provision(row)
                return {"profile": row["profile"]}
            session = req["sessionId"]
            if not isinstance(session, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", session):
                raise Failure("invalid_workspace_request", 400)
            if action == "attach":
                self.provision(row)
                with self.db:
                    self.db.execute("INSERT OR IGNORE INTO attachments VALUES(?,?,1)", (workspace, session))
            active = self.row("SELECT active FROM attachments WHERE workspace=? AND session=?", workspace, session)
            if not active or not active["active"]:
                raise Failure("workspace_not_found", 404)
            if action == "attach":
                return {}
            if action != "file":
                raise Failure("invalid_workspace_request", 400)
            pod = self.provision(row)
            deadline = time.monotonic() + 15
            while not any(c.type == "Ready" and c.status == "True" for c in pod.status.conditions or []):
                if time.monotonic() > deadline:
                    raise Failure()
                time.sleep(.2)
                pod = self.provision(self.compute(workspace))
            attachment = (workspace, session)
            self.inflight[attachment] = self.inflight.get(attachment, 0) + 1
        # Admission is fenced by the session attachment; independent operations
        # may now run concurrently. The application coordinates shared edits.
        try:
            return self.cluster.file(workspace, req["operation"])
        finally:
            with self.lock:
                self.inflight[attachment] -= 1
                if self.inflight[attachment] == 0:
                    del self.inflight[attachment]


class Server(ThreadingHTTPServer):
    # A slow TLS client must not hold the accept loop or create unbounded workers.
    def process_request(self, request, address):
        if not self.capacity.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.capacity.release()
            raise

    def process_request_thread(self, request, address):
        try:
            request.settimeout(15)
            with self.tls.wrap_socket(request, server_side=True) as secure:
                super().process_request_thread(secure, address)
        except OSError:
            request.close()
        finally:
            self.capacity.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_json(200 if self.path == "/health" else 404, {})

    def do_POST(self):
        try:
            if not hmac.compare_digest(self.headers.get("Authorization", ""), self.server.auth):
                raise Failure("forbidden", 403)
            action = self.path.removeprefix("/v1/")
            if self.path != "/v1/" + action or action not in {"start","open","recover","attach","detach","file","stop","delete"}:
                raise Failure("invalid_workspace_request", 404)
            length = int(self.headers.get("content-length", 0))
            if not 0 < length <= 1_048_576:
                raise Failure("invalid_workspace_request", 400)
            req = json.loads(self.rfile.read(length))
            if not isinstance(req, dict):
                raise Failure("invalid_workspace_request", 400)
            result = self.server.controller.call(action, req)
            self.send_json(200, result)
        except Failure as exc:
            self.send_json(exc.status, {"error": exc.code})
        except (KeyError, ValueError, TypeError):
            self.send_json(400, {"error": "invalid_workspace_request"})
        except Exception as exc:
            print("workspace request failed", type(exc).__name__, flush=True)
            self.send_json(503, {"error": "workspace_provider_unavailable"})


if __name__ == "__main__":
    controller = Controller()
    token = os.environ["WORKSPACE_SERVICE_TOKEN"]
    if len(token) < 32:
        raise ValueError("Workspace service requires a secret token")
    server = Server(("", 8443), Handler)
    server.capacity = threading.BoundedSemaphore(64)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain("/tls/tls.crt", "/tls/tls.key")
    server.tls = tls
    server.auth = "Bearer " + token
    server.controller = controller
    threading.Thread(target=controller.reconcile, daemon=True).start()
    server.serve_forever()
