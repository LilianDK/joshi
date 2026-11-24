"""End-to-end smoke test for shellSniffer – no real Ollama, no real data needed.

    python tests/smoke_test.py            (from the project folder, with the .venv Python)

It starts a tiny fake Ollama (chat, streaming chat, embeddings) and the shellSniffer server on free
ports with a temporary data folder, feeds in a few commands through the hook API and checks the main
features: capture, honeypot, BM25 search with time range, SniffMagic, hybrid search and its balance,
documentation hits, ask the shroom, continuous shrooming, batch import and "Ollama disabled".
Exit code 0 = all checks passed.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT = pathlib.Path(__file__).resolve().parent.parent
FAILED: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  ok    " if cond else "  FAIL  ") + name + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILED.append(name)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- fake Ollama
SYNONYMS = {"remove": "rm", "delete": "rm", "container": "docker", "containers": "docker", "upload": "push", "publish": "push"}
fake_calls = {"requests": 0}


def fake_vector(text: str, dim: int = 64) -> list[float]:
    v = [0.0] * dim
    for w in re.findall(r"\w+", text.lower()):
        w = SYNONYMS.get(w, w)
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dim] += 1.0
    return v


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        fake_calls["requests"] += 1
        if self.path == "/api/tags":
            self._json({"models": [{"name": "fake-chat:1b"}, {"name": "nomic-embed-text:latest"}]})
        else:
            self.send_error(404)

    def do_POST(self):
        fake_calls["requests"] += 1
        d = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.path == "/api/embed":
            self._json({"embeddings": [fake_vector(t) for t in d["input"]]})
        elif self.path == "/api/chat" and d.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "application/x-ndjson")
            self.end_headers()
            for w in "You pushed it with `git push -u origin main` [1].".split(" "):
                self.wfile.write((json.dumps({"message": {"content": w + " "}, "done": False}) + "\n").encode())
            self.wfile.write((json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode())
        elif self.path == "/api/chat":
            n = len(re.findall(r"^\[\d+\] ", d["messages"][-1]["content"], flags=re.M))
            answer = {"domain": "versionskontrolle", "topic_id": None,
                      "new_topic": {"name": "Publishing code", "description": "Pushing projects to GitHub."},
                      "title": "Publish the project to GitHub",
                      "summary": f"**Purpose:** Publish the project.\n\n**Procedure:**\n1. Commit the changes. [1]\n"
                                 f"2. Push the branch. [{n}]\n\n**Result:** The push worked."}
            self._json({"message": {"content": json.dumps(answer)}})
        else:
            self.send_error(404)


# ---------------------------------------------------------------- HTTP helpers
class Api:
    def __init__(self, base: str):
        self.base = base

    def call(self, method: str, path: str, body=None, raw: bytes | None = None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"content-type": ctype} if data is not None else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                text = r.read().decode("utf-8")
                return r.status, (json.loads(text) if "json" in r.headers.get("content-type", "") and "ndjson" not in r.headers.get("content-type", "") else text)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body=None):
        return self.call("POST", path, body if body is not None else {})

    def wait_idle(self, timeout=60):
        for _ in range(timeout * 2):
            st = self.get("/api/status")[1]
            if not st["cluster_requested"] and not st["clustering"]:
                return st
            time.sleep(0.5)
        return st


def q(s: str) -> str:
    return urllib.parse.quote(s)


# ---------------------------------------------------------------- the test
def main() -> int:
    ollama_port, app_port = free_port(), free_port()
    fake = ThreadingHTTPServer(("127.0.0.1", ollama_port), FakeOllama)
    threading.Thread(target=fake.serve_forever, daemon=True).start()

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="shellsniffer-test-"))
    env = {**os.environ, "SHELLSNIFFER_DIR": str(tmp / "data"), "SHELLSNIFFER_HONEYPOT_DIR": str(tmp / "honeypot"),
           "SHELLSNIFFER_OLLAMA_URL": f"http://127.0.0.1:{ollama_port}", "SHELLSNIFFER_ATUIN": "0",
           "SHELLSNIFFER_HONEYPOT": "1", "PYTHONIOENCODING": "utf-8"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "shellsniffer.app:app", "--port", str(app_port),
                               "--log-level", "warning"], cwd=PROJECT, env=env)
    api = Api(f"http://127.0.0.1:{app_port}")
    try:
        for _ in range(60):
            try:
                api.get("/api/status")
                break
            except OSError:
                time.sleep(0.3)

        print("capture")
        now_ms = time.time() * 1000
        cmds = [("git add -A", ""), ('git commit -m "first version"', "1 file changed"),
                ("git push -u origin main", "To github.com:me/app.git"), ("docker compose up -d", "Container web Started"),
                ("export API_TOKEN=supersecret123", "")]
        for i, (cmd, out) in enumerate(cmds):
            s, r = api.post("/api/events", {"command": cmd, "output": out, "shell": "pwsh", "host": "TEST",
                                            "session": "s1", "cwd": "/work/app", "exit_code": 0,
                                            "started_at": now_ms - (len(cmds) - i) * 1000})
            check(f"hook reports '{cmd[:20]}'", s == 200)
        check("command without text is rejected", api.post("/api/events", {"shell": "pwsh"})[0] == 400)
        logs = list((tmp / "honeypot").glob("*.log"))
        check("honeypot log written", bool(logs) and "git push" in logs[0].read_text(encoding="utf-8"))
        check("secret masked in honeypot", logs and "supersecret123" not in logs[0].read_text(encoding="utf-8"))

        print("BM25 search")
        s, r = api.get(f"/api/search?q={q('push')}")
        check("finds the command", s == 200 and any("push" in h["command_hl"] for h in r["hits"]))
        s, r = api.get(f"/api/search?q=push&start={time.time() + 3600}")
        check("time range excludes it", s == 200 and not r["hits"])
        check("bad time range -> 400", api.get("/api/search?q=x&start=10&end=5")[0] == 400)

        print("SniffMagic")
        s, p = api.get("/api/cluster/preview")
        check("preview counts the waiting session", s == 200 and p["waiting"] >= 1, str(p))
        s, r = api.post("/api/cluster", {})
        st = api.wait_idle()
        check("documents the session", s == 200 and r["queued"] >= 1 and st["pending"] == 0)
        topics = api.get("/api/topics")[1]
        summary = topics[0]["blocks"][0]["summary"] if topics and topics[0]["blocks"] else ""
        check("ASD-STE100 structure with [n] references", "**Procedure:**" in summary and "[1]" in summary)

        print("hybrid search ('Sniff logs with shroom')")
        s, r = api.get(f"/api/search?mode=hybrid&q={q('remove container')}")
        check("finds docker by meaning", s == 200 and any("docker" in h["command_hl"] for h in r["hits"][:3]))
        s, r = api.get(f"/api/search?mode=hybrid&semantic_weight=0&q={q('push')}")
        check("balance 0 = BM25 only", s == 200 and all(h["bm25_rank"] for h in r["hits"]))
        s, r = api.get(f"/api/search?q={q('publish project')}")
        check("documentation hits (BM25)", s == 200 and r["docs"], f"{len(r.get('docs', []))} docs")
        s, r = api.get(f"/api/search?mode=hybrid&q={q('upload repository')}")
        check("documentation hits (hybrid)", s == 200 and r["docs"])

        print("ask the shroom")
        s, text = api.get(f"/api/ask?q={q('how did I upload the project?')}")
        msgs = [json.loads(line) for line in text.splitlines() if line.strip()] if s == 200 else []
        types = [m["type"] for m in msgs]
        answer = "".join(m["text"] for m in msgs if m["type"] == "token")
        check("streams sources, tokens, done", types[:1] == ["sources"] and "token" in types and types[-1:] == ["done"], str(types[:3]))
        check("answer cites a source", "[1]" in answer, answer[:60])

        print("continuous shrooming")
        api.post("/api/settings", {"vectorize": True})
        api.post("/api/events", {"command": "kubectl get pods", "shell": "pwsh", "host": "TEST", "session": "s2", "cwd": "/k"})
        for _ in range(20):
            v = api.get("/api/status")[1]["vectors"]
            if v["total"] and v["done"] == v["total"]:
                break
            time.sleep(0.5)
        check("new command vectorized right away", v["done"] == v["total"], f"{v['done']}/{v['total']}")
        api.post("/api/settings", {"vectorize": False})

        print("batch import")
        dump = b"#1763000000\nls -la\n#1763000060\nnpm test\n"
        s, r = api.call("POST", "/api/import?name=.bash_history&mtime=1763000000000", raw=dump, ctype="text/plain")
        check("bash history detected and imported", s == 200 and r["format"] == "bash-history" and r["imported"] == 2, str(r))
        s, r = api.call("POST", "/api/import?name=.bash_history&mtime=1763000000000", raw=dump, ctype="text/plain")
        check("re-upload skips duplicates", s == 200 and r["imported"] == 0 and r["duplicates"] == 2)
        s, r = api.call("POST", "/api/import?name=a.png", raw=b"\x89PNG\x00\x00", ctype="text/plain")
        check("binary file rejected", s == 415)

        print("Ollama disabled")
        api.post("/api/settings", {"ollama": False})
        before = fake_calls["requests"]
        check("SniffMagic refused", api.post("/api/cluster", {})[0] == 409)
        check("shroom search refused", api.get("/api/search?mode=hybrid&q=x")[0] == 503)
        check("BM25 still works", api.get("/api/search?q=push")[0] == 200)
        time.sleep(1)
        check("no request reached Ollama", fake_calls["requests"] == before, f"{fake_calls['requests'] - before} requests")
        api.post("/api/settings", {"ollama": True})
    finally:
        server.terminate()
        server.wait(timeout=10)
        fake.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) FAILED: " + "; ".join(FAILED))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
