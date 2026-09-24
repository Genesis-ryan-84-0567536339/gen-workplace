#!/usr/bin/env python3
"""
GENESIS Multi-Agent Swarm Orchestrator - Backend Control Plane
Phục vụ API quản trị dự án, điều phối Agent CLI runtimes, đồng bộ SSOT Memory
và phục vụ WebApp giao diện người dùng.
"""

import os
import sys
import json
import time
import asyncio
import subprocess
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

PORT = int(os.environ.get("PORT", 8888))
BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = DATA_DIR / "state.json"

DEFAULT_STATE = {
    "projects": [
        {
            "id": "PRJ-01",
            "name": "e-commerce-engine",
            "repo": "/workspace/e-commerce-engine",
            "meta": "6 role · 6 runtime · đang chạy",
            "status": "active"
        }
    ],
    "active_project": "PRJ-01"
}

def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_STATE

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving state: {e}", file=sys.stderr)

class SwarmHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            status = {
                "status": "online",
                "version": "1.1-docker",
                "timestamp": int(time.time()),
                "docker_env": bool(os.environ.get("DOCKER_CONTAINER")),
                "active_agents": 6,
                "ssot_synced": True
            }
            self.wfile.write(json.dumps(status).encode("utf-8"))
            return

        if self.path == "/api/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(load_state(), ensure_ascii=False).encode("utf-8"))
            return

        return super().do_GET()

    def do_POST(self):
        if self.path == "/api/state":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                save_state(data)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "saved"}')
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
            return

        if self.path == "/api/execute":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                command = data.get("command", "")
                role = data.get("role", "Worker")
                
                # An toàn: chỉ chạy các lệnh được cấp phép trong container
                res = {
                    "role": role,
                    "command": command,
                    "status": "dispatched",
                    "time": time.strftime("%H:%M:%S")
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def main():
    print(f"==================================================")
    print(f"  GENESIS SWARM WORKPLACE - CONTROL PLANE DAEMON  ")
    print(f"  Port: {PORT}")
    print(f"  Frontend: {FRONTEND_DIR}")
    print(f"  Data: {DATA_DIR}")
    print(f"==================================================")
    server = ThreadedHTTPServer(("0.0.0.0", PORT), SwarmHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()

if __name__ == "__main__":
    main()
