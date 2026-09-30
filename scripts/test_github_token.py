#!/usr/bin/env python3
"""
Test TSK-25: GITHUB_TOKEN ưu tiên biến môi trường, không có thì đọc từ file <DATA_DIR>/secrets/github.token.
- env có → dùng env (source="env")
- env có + file có → ưu tiên env
- env không có + file có → dùng file (source="file")
- file có dòng trống, khoảng trắng, comment → strip và bỏ qua dòng trống/comment
- không có gì (hoặc file rỗng) → "" (source="none")
- /api/status có github_token: "env" | "file" | "none" và TUYỆT ĐỐI không chứa giá trị token
- Tích hợp _verify_github_pr và create_draft_pr với file token
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-gh-token-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
SENT_LOG = os.path.join(TMP, "tmux-sent.log")
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ.pop("GITHUB_TOKEN", None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db, main, agy_build  # noqa: E402

PASSED = 0
FAILED = 0


def check(label, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label} {extra}")


secrets_dir = os.path.join(os.environ["DATA_DIR"], "secrets")
token_file = os.path.join(secrets_dir, "github.token")

server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"


def get_status():
    req = urllib.request.Request(f"{BASE}/api/status", method="GET")
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = r.read().decode("utf-8")
        status = r.status
    return status, json.loads(raw), raw


try:
    print("[1] Không có gì → db.github_token() rỗng, source='none', /api/status github_token='none'")
    os.environ.pop("GITHUB_TOKEN", None)
    if os.path.exists(token_file):
        os.remove(token_file)

    tok = db.github_token()
    src = db.github_token_source()
    check("github_token() là rỗng khi không có env và file", tok == "")
    check("github_token_source() là 'none'", src == "none")

    st, js, raw = get_status()
    check("GET /api/status trả về 200", st == 200)
    check("/api/status có github_token == 'none'", js.get("github_token") == "none", str(js))

    print("[2] Env có → dùng env, source='env', /api/status không lộ giá trị token")
    ENV_SECRET = "ghp_env_secret_token_1234567890abcdef"
    os.environ["GITHUB_TOKEN"] = f"  {ENV_SECRET}  \n"

    tok = db.github_token()
    src = db.github_token_source()
    check("github_token() trả về token env đã strip", tok == ENV_SECRET)
    check("github_token_source() là 'env'", src == "env")

    st, js, raw = get_status()
    check("/api/status có github_token == 'env'", js.get("github_token") == "env", str(js))
    check("/api/status không chứa giá trị token env trong JSON", ENV_SECRET not in raw, raw)

    print("[3] Env có VÀ file có → ưu tiên env")
    os.makedirs(secrets_dir, exist_ok=True)
    FILE_SECRET = "ghp_file_secret_token_9876543210fedcba"
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(f"{FILE_SECRET}\n")

    tok = db.github_token()
    src = db.github_token_source()
    check("ưu tiên env khi cả env và file đều có", tok == ENV_SECRET)
    check("source là 'env'", src == "env")

    st, js, raw = get_status()
    check("/api/status có github_token == 'env'", js.get("github_token") == "env", str(js))
    check("/api/status không chứa token env", ENV_SECRET not in raw)
    check("/api/status không chứa token file", FILE_SECRET not in raw)

    print("[4] Env không có + file có → dùng file, source='file', không lộ giá trị token")
    os.environ.pop("GITHUB_TOKEN", None)

    tok = db.github_token()
    src = db.github_token_source()
    check("dùng token từ file khi không có env", tok == FILE_SECRET)
    check("source là 'file'", src == "file")

    st, js, raw = get_status()
    check("/api/status có github_token == 'file'", js.get("github_token") == "file", str(js))
    check("/api/status không chứa giá trị token file trong raw JSON", FILE_SECRET not in raw, raw)

    print("[5] File có nhiều dòng, dòng trống, khoảng trắng, comment (#) → strip & bỏ dòng trống/comment")
    with open(token_file, "w", encoding="utf-8") as f:
        f.write("\n\n  # Đây là comment ghi chú token  \n   \nghp_token_with_comments_and_spaces   \n\n# dòng sau\n")

    tok = db.github_token()
    src = db.github_token_source()
    check("bỏ dòng trống và comment, đọc đúng token", tok == "ghp_token_with_comments_and_spaces")
    check("source là 'file'", src == "file")

    print("[6] File chỉ có khoảng trắng / dòng trống / comment → coi như rỗng, source='none'")
    with open(token_file, "w", encoding="utf-8") as f:
        f.write("\n   \n# chỉ có comment\n   \n")

    tok = db.github_token()
    src = db.github_token_source()
    check("file chỉ có comment/dòng trống → token rỗng", tok == "")
    check("source là 'none'", src == "none")

    st, js, raw = get_status()
    check("/api/status có github_token == 'none'", js.get("github_token") == "none", str(js))

    print("[7] Env chỉ có khoảng trắng → fallback đọc file")
    os.environ["GITHUB_TOKEN"] = "   \t\n  "
    with open(token_file, "w", encoding="utf-8") as f:
        f.write("ghp_fallback_file_token\n")

    tok = db.github_token()
    src = db.github_token_source()
    check("env rỗng/khoảng trắng → fallback đọc file", tok == "ghp_fallback_file_token")
    check("source là 'file'", src == "file")

    print("[8] Tích hợp create_draft_pr và _verify_github_pr với file token")
    # agy_build.create_draft_pr dùng db.github_token()
    os.environ.pop("GITHUB_TOKEN", None)
    with open(token_file, "w", encoding="utf-8") as f:
        f.write("ghp_draft_pr_token_test\n")

    # Mock urllib.request.urlopen để kiểm tra headers gửi đi trong create_draft_pr
    captured_requests = []
    _orig_urlopen = urllib.request.urlopen

    class DummyResponse:
        def __init__(self, data):
            self.data = data
            self.status = 200

        def read(self):
            return self.data.encode("utf-8")

        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def mock_urlopen(req, *args, **kwargs):
        captured_requests.append(req)
        if isinstance(req, urllib.request.Request):
            if "pulls" in req.full_url:
                if req.get_method() == "POST":
                    return DummyResponse(json.dumps({"html_url": "https://github.com/acme/repo/pull/99"}))
                elif req.get_method() == "GET":
                    return DummyResponse(json.dumps({"number": 99, "html_url": "https://github.com/acme/repo/pull/99"}))
        return _orig_urlopen(req, *args, **kwargs)

    agy_build.urllib.request.urlopen = mock_urlopen
    urllib.request.urlopen = mock_urlopen

    captured_requests.clear()
    url, err = agy_build.create_draft_pr("acme/repo", "wt/TSK-25", "Tiêu đề PR", "Nội dung PR")
    check("create_draft_pr đọc token từ file", url == "https://github.com/acme/repo/pull/99" and err == "")
    check("create_draft_pr gửi header Authorization Bearer token file",
          captured_requests and captured_requests[-1].get_header("Authorization") == "Bearer ghp_draft_pr_token_test")

    captured_requests.clear()
    ok, reason = db._verify_github_pr("acme", "repo", 99)
    check("_verify_github_pr đọc token từ file", ok is True)
    check("_verify_github_pr gửi header Authorization Bearer token file",
          captured_requests and captured_requests[-1].get_header("Authorization") == "Bearer ghp_draft_pr_token_test")

    # Dọn dẹp mock
    agy_build.urllib.request.urlopen = _orig_urlopen
    urllib.request.urlopen = _orig_urlopen

finally:
    server.shutdown()
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{PASSED}/{PASSED + FAILED} test pass")
if FAILED > 0:
    sys.exit(1)
