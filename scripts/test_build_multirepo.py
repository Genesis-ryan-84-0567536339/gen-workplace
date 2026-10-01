#!/usr/bin/env python3
"""
Test bộ lõi đa repo (TSK-33 / Issue #57 mục 1-4, 6):
1. build_repos(): cấu hình repo từ file / env, mục mặc định gen-workplace, tự clone vào gw-repos/<key>.
2. Cột repo + /api/task/assign + assign_task_to_role + MCP assign_task nhận repo; GET /api/build/repos; 400 bad_repo / 409 repo_mismatch.
3. Worktree gw-worktrees/<key>/TSK-n; cleanup + detect_violations theo repo.
4. check_push_diff: chặn .github/.gitea/deploy/Dockerfile/compose/install.sh/.env*/protected_paths/symlink trước push.
5. Push + PR nháp đúng slug/base của repo.
"""
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="gw-test-multirepo-")
DATA_DIR = os.path.join(TMP, "data")
HOME_DIR = os.path.join(TMP, "home")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(HOME_DIR, exist_ok=True)
os.makedirs(FAKEBIN, exist_ok=True)

# Giả lập agy runner đơn giản
AGY_BIN = os.path.join(FAKEBIN, "agy")
with open(AGY_BIN, "w") as f:
    f.write('''#!/usr/bin/env python3
import json, os, re, subprocess, sys
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
m = re.search(r"THÔNG TIN VIỆC (TSK-[0-9]+)", prompt)
tid = m.group(1) if m else "TSK-0"

mode = os.environ.get("FAKE_AGY_MULTI_MODE", "ok")
if mode == "ok":
    with open("feature_ok.txt", "w") as fh:
        fh.write("ok feature")
    subprocess.run(["git", "add", "feature_ok.txt"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"{tid}: thêm feature ok"], check=True)
elif mode == "bad_diff_github":
    os.makedirs(".github/workflows", exist_ok=True)
    with open(".github/workflows/ci.yml", "w") as fh:
        fh.write("name: CI")
    subprocess.run(["git", "add", ".github/workflows/ci.yml"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"{tid}: thêm file cấm github"], check=True)
elif mode == "bad_diff_deploy":
    os.makedirs("deploy", exist_ok=True)
    with open("deploy/app.sh", "w") as fh:
        fh.write("#!/bin/sh")
    subprocess.run(["git", "add", "deploy/app.sh"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"{tid}: thêm deploy cấm"], check=True)
elif mode == "bad_diff_symlink":
    os.symlink("/etc/passwd", "evil_link")
    subprocess.run(["git", "add", "evil_link"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"{tid}: thêm symlink cấm"], check=True)

res = {
    "event": "result",
    "result": {
        "status": "SUCCESS",
        "response": f"Đã làm việc {tid}\\n[KANBAN_UPDATE: {tid} | CHECK: chk-1]"
    }
}
print(json.dumps(res, ensure_ascii=False))
sys.exit(0)
''')
os.chmod(AGY_BIN, 0o755)

# Cấu hình repo chính giả lập (gen-workplace)
GW_MAIN_REMOTE = os.path.join(TMP, "gw_main_origin.git")
GW_MAIN_REPO = os.path.join(TMP, "gw_main_repo")
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", GW_MAIN_REMOTE], check=True)
os.makedirs(GW_MAIN_REPO, exist_ok=True)
subprocess.run(["git", "init", "-q", "-b", "main", GW_MAIN_REPO], check=True)
with open(os.path.join(GW_MAIN_REPO, "README.md"), "w") as f:
    f.write("# gen-workplace\n")
subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@test.com", "add", "."], cwd=GW_MAIN_REPO, check=True)
subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@test.com", "commit", "-q", "-m", "init"], cwd=GW_MAIN_REPO, check=True)
subprocess.run(["git", "remote", "add", "origin", GW_MAIN_REMOTE], cwd=GW_MAIN_REPO, check=True)
subprocess.run(["git", "push", "-q", "origin", "main"], cwd=GW_MAIN_REPO, check=True)

# Cấu hình repo ngoài giả lập: kho-ryan
# URL clone dựng từ slug: <GW_BUILD_CLONE_BASE>/<slug>.git → remote local theo đúng slug (không cần mạng)
CLONE_BASE = os.path.join(TMP, "remotes")
RYAN_REMOTE = os.path.join(CLONE_BASE, "genesis-corp", "kho-ryan.git")
os.makedirs(os.path.dirname(RYAN_REMOTE), exist_ok=True)
subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", RYAN_REMOTE], check=True)
RYAN_INIT = os.path.join(TMP, "ryan_init")
os.makedirs(RYAN_INIT, exist_ok=True)
subprocess.run(["git", "init", "-q", "-b", "develop", RYAN_INIT], check=True)
with open(os.path.join(RYAN_INIT, "README.md"), "w") as f:
    f.write("# Kho Ryan\n")
with open(os.path.join(RYAN_INIT, "core.py"), "w") as f:
    f.write("print('ryan core')\n")
subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@test.com", "add", "."], cwd=RYAN_INIT, check=True)
subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@test.com", "commit", "-q", "-m", "init ryan"], cwd=RYAN_INIT, check=True)
subprocess.run(["git", "remote", "add", "origin", RYAN_REMOTE], cwd=RYAN_INIT, check=True)
subprocess.run(["git", "push", "-q", "origin", "develop"], cwd=RYAN_INIT, check=True)

GW_REPOS_ROOT = os.path.join(TMP, "gw-repos")
GW_WORKTREE_ROOT = os.path.join(TMP, "gw-worktrees")
os.makedirs(GW_REPOS_ROOT, exist_ok=True)
os.makedirs(GW_WORKTREE_ROOT, exist_ok=True)

# GitHub API Mock Server
PR_REQUESTS = []
class MockGitHubHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        ln = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(ln).decode("utf-8")
        data = json.loads(body)
        PR_REQUESTS.append({"path": self.path, "data": data, "headers": dict(self.headers)})
        resp = json.dumps({"html_url": f"https://github.com/test-org/test-repo/pull/{len(PR_REQUESTS)}"}).encode("utf-8")
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *a):
        pass

mock_server = http.server.HTTPServer(("127.0.0.1", 0), MockGitHubHandler)
mock_port = mock_server.server_port
threading.Thread(target=mock_server.serve_forever, daemon=True).start()

# Thiết lập biến môi trường
os.environ["DATA_DIR"] = DATA_DIR
os.environ["HOME"] = HOME_DIR
os.environ["GW_AGY_BIN"] = AGY_BIN
os.environ["GW_DISPATCH_REPO"] = GW_MAIN_REPO
os.environ["GW_REPOS_ROOT"] = GW_REPOS_ROOT
os.environ["GW_WORKTREE_ROOT"] = GW_WORKTREE_ROOT
os.environ["GW_BUILD_GITHUB_REPO"] = "chu-so-huu/gen-workplace"
os.environ["GW_GITHUB_API_URL"] = f"http://127.0.0.1:{mock_port}"
os.environ["GITHUB_TOKEN"] = "fake-github-token-12345"
os.environ["GW_BUILD_CLONE_BASE"] = CLONE_BASE
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")

# Cấu hình multirepo qua GW_BUILD_REPOS
REPOS_CONFIG = [
    {
        "key": "kho-ryan",
        "slug": "genesis-corp/kho-ryan",
        "base": "develop",
        "kind": "python",
        "protected_paths": ["secrets/*", "locked.json"]
    }
]
os.environ["GW_BUILD_REPOS"] = json.dumps(REPOS_CONFIG)

sys.path.insert(0, str(ROOT))
from backend import db, agy_build, main, mcp_core  # noqa: E402

total = 0
failed = 0


def check(label, cond, extra=""):
    global total, failed
    total += 1
    if cond:
        print(f"  ok   {label}")
    else:
        failed += 1
        print(f"  FAIL {label} {extra}")


def main_test():
    global total, failed

    print("\n[1] Kiểm tra build_repos(): cấu hình, repo mặc định, auto clone")
    repos = agy_build.build_repos(auto_clone=True)
    check("build_repos trả list", isinstance(repos, list) and len(repos) >= 2)
    def_repo = next((r for r in repos if r["key"] == "gen-workplace"), None)
    check("repo mặc định gen-workplace có mặt", def_repo is not None)
    check("gen-workplace clone_dir = repo_dir", def_repo and def_repo["clone_dir"] == GW_MAIN_REPO)
    check("gen-workplace kind = python", def_repo and def_repo["kind"] == "python")

    ryan_repo = next((r for r in repos if r["key"] == "kho-ryan"), None)
    check("kho-ryan có trong danh sách", ryan_repo is not None)
    check("kho-ryan slug đúng", ryan_repo and ryan_repo["slug"] == "genesis-corp/kho-ryan")
    check("kho-ryan base đúng develop", ryan_repo and ryan_repo["base"] == "develop")
    expected_clone = os.path.join(GW_REPOS_ROOT, "kho-ryan")
    check("kho-ryan clone_dir nằm dưới gw-repos/", ryan_repo and ryan_repo["clone_dir"] == expected_clone)
    check("kho-ryan tự động clone thành công", os.path.exists(os.path.join(expected_clone, ".git")))

    # Kiểm tra key sai format bị bỏ qua
    bad_cfg = json.dumps([{"key": "BAD_KEY!", "slug": "a/b"}, {"key": "x", "slug": "a/b"}])
    os.environ["GW_BUILD_REPOS"] = bad_cfg
    repos_bad = agy_build.build_repos(auto_clone=False)
    check("key viết hoa / ký tự lạ bị bỏ qua", not any(r["key"] == "BAD_KEY!" for r in repos_bad))
    check("key < 2 ký tự bị bỏ qua", not any(r["key"] == "x" for r in repos_bad))

    # Khôi phục cấu hình hợp lệ
    os.environ["GW_BUILD_REPOS"] = json.dumps(REPOS_CONFIG)

    print("\n[2] Kiểm tra GET /api/build/repos: không lộ đường dẫn, chỉ có key, slug, kind")
    # Giả lập HTTP server của app để test REST API
    server = http.server.HTTPServer(("127.0.0.1", 0), main.SwarmHandler)
    app_port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()

    req = urllib.request.Request(f"http://127.0.0.1:{app_port}/api/build/repos")
    with urllib.request.urlopen(req) as resp:
        check("GET /api/build/repos status 200", resp.status == 200)
        body = json.loads(resp.read().decode("utf-8"))
        check("kết quả là list", isinstance(body, list))
        keys = [x.get("key") for x in body]
        check("chứa gen-workplace và kho-ryan", "gen-workplace" in keys and "kho-ryan" in keys)
        has_path = any("clone_dir" in x or "path" in x or "/" in x.get("key", "") for x in body)
        check("KHÔNG trả đường dẫn nào", not has_path)
        for item in body:
            check(f"item {item.get('key')} chỉ có key, slug, kind", set(item.keys()) == {"key", "slug", "kind"})

    print("\n[3] Kiểm tra cột repo, assign_task_to_role, 400 bad_repo, 409 repo_mismatch")
    # Tạo task mẫu
    conv = db.create_gen_conversation(title="Test Multirepo")
    cid = conv["id"]
    t1 = db.save_gen_session_todo(cid, title="Task 1", description="Task test repo", viec_ref="VIEC-101", priority="high")
    tid1 = t1["id"]

    # 1. Key chứa ký tự lạ / đường dẫn -> 400 bad_repo
    for bad_key in ["../etc", "repo/sub", "invalid@key", "not_exist_repo"]:
        res_bad = db.assign_task_to_role(tid1, "gw-backend-agy", repo=bad_key)
        check(f"repo='{bad_key}' trả bad_repo", res_bad.get("code") == "bad_repo")
        check(f"assign_task_http_status trả 400 cho '{bad_key}'", db.assign_task_http_status(res_bad) == 400)

    # REST API kiểm tra 400 cho bad_repo
    req_bad = urllib.request.Request(
        f"http://127.0.0.1:{app_port}/api/task/assign",
        data=json.dumps({"todo_id": tid1, "session_id": "gw-backend-agy", "repo": "../evil"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req_bad)
        check("REST API 400 cho bad_repo", False)
    except urllib.error.HTTPError as e:
        check("REST API 400 cho bad_repo", e.code == 400)

    # 2. Giao task với repo='kho-ryan' -> thành công, lưu repo vào task
    os.environ["FAKE_AGY_MULTI_MODE"] = "ok"
    res_ok = db.assign_task_to_role(tid1, "gw-backend-agy", repo="kho-ryan", wait=True)
    check("giao task repo='kho-ryan' thành công", res_ok.get("status") == "assigned")
    check("worktree_dir nằm trong gw-worktrees/kho-ryan", f"gw-worktrees/kho-ryan/{tid1}" in res_ok.get("worktree_dir", ""))

    # Kiểm tra task trong DB đã có cột repo='kho-ryan'
    t1_db = db._task_detail(tid1)
    check("cột repo trong DB lưu 'kho-ryan'", t1_db.get("repo") == "kho-ryan")

    # 3. Giao task tid1 sang repo khác (vd gen-workplace) khi đã có worktree ở kho-ryan -> 409 repo_mismatch
    # Mở khóa claim để thử giao lại
    with db.get_connection() as conn:
        conn.execute("UPDATE gen_session_todos SET claimed_by = '', status = 'todo' WHERE id = ?", (tid1,))
        conn.commit()

    res_mismatch = db.assign_task_to_role(tid1, "gw-backend-agy", repo="gen-workplace")
    check("giao sang repo khác bị chặn 409 repo_mismatch", res_mismatch.get("code") == "repo_mismatch")
    check("assign_task_http_status trả 409 cho repo_mismatch", db.assign_task_http_status(res_mismatch) == 409)

    # 4. MCP assign_task tool
    t2 = db.save_gen_session_todo(cid, title="Task 2 MCP", description="Task test MCP repo", viec_ref="VIEC-102", priority="high")
    tid2 = t2["id"]
    mcp_res = mcp_core.execute_tool("assign_task", {"task_id": tid2, "session_id": "backend", "repo": "bad_repo_key"})
    check("MCP assign_task key lạ trả isError=True", mcp_res.get("isError") is True)
    txt = json.loads(mcp_res["content"][0]["text"])
    check("MCP assign_task trả code bad_repo", txt.get("code") == "bad_repo")
    check("MCP assign_task trả http_status 400", txt.get("http_status") == 400)

    print("\n[4] Kiểm tra Worktree theo repo và cleanup_task_worktree")
    # tid1 có worktree ở gw-worktrees/kho-ryan/tid1
    wt_dir_tid1 = agy_build.task_worktree_dir(tid1, "kho-ryan")
    check("worktree tid1 tồn tại trên đĩa", os.path.exists(wt_dir_tid1))

    # Task tid3 chạy trên repo mặc định gen-workplace (repo="")
    t3 = db.save_gen_session_todo(cid, title="Task 3 gen-workplace", description="Task gen-workplace", viec_ref="VIEC-103", priority="high")
    tid3 = t3["id"]
    res3 = db.assign_task_to_role(tid3, "gw-backend-agy", repo="", wait=True)
    wt_dir_tid3 = agy_build.task_worktree_dir(tid3, "")
    check("task repo trống giữ worktree cũ gw-worktrees/TSK-n", wt_dir_tid3 == os.path.join(GW_WORKTREE_ROOT, tid3))
    check("worktree tid3 tồn tại trên đĩa", os.path.exists(wt_dir_tid3))

    # Cleanup tid1 (thuộc kho-ryan)
    c1 = db.cleanup_task_worktree(tid1)
    check("cleanup tid1 thành công", c1.get("removed") is True)
    check("worktree tid1 đã bị xóa", not os.path.exists(wt_dir_tid1))

    # Cleanup tid3 (thuộc gen-workplace)
    c3 = db.cleanup_task_worktree(tid3)
    check("cleanup tid3 thành công", c3.get("removed") is True)
    check("worktree tid3 đã bị xóa", not os.path.exists(wt_dir_tid3))

    print("\n[5] Kiểm tra check_push_diff: chặn file cấm, symlink, protected_paths; cho qua file thường")
    # Chuẩn bị một worktree tạm để test check_push_diff
    test_wt = os.path.join(TMP, "test_diff_wt")
    subprocess.run(["git", "clone", "-q", "-b", "develop", RYAN_REMOTE, test_wt], check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=test_wt, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=test_wt, check=True)

    # 1. File thường -> check_push_diff trả về rỗng (cho qua)
    with open(os.path.join(test_wt, "normal.py"), "w") as f:
        f.write("# normal file\n")
    subprocess.run(["git", "add", "normal.py"], cwd=test_wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add normal"], cwd=test_wt, check=True)
    diffs = agy_build.check_push_diff(test_wt, base="develop", protected_paths=["secrets/*"])
    check("file thường không bị chặn", len(diffs) == 0)

    # 2. File .github/
    os.makedirs(os.path.join(test_wt, ".github", "workflows"), exist_ok=True)
    with open(os.path.join(test_wt, ".github", "workflows", "x.yml"), "w") as f:
        f.write("test")
    subprocess.run(["git", "add", ".github/workflows/x.yml"], cwd=test_wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add github"], cwd=test_wt, check=True)
    diffs = agy_build.check_push_diff(test_wt, base="develop")
    check("chặn .github/workflows/x.yml", any(".github" in d for d in diffs))

    # 3. File deploy/
    os.makedirs(os.path.join(test_wt, "deploy"), exist_ok=True)
    with open(os.path.join(test_wt, "deploy", "a.sh"), "w") as f:
        f.write("echo")
    subprocess.run(["git", "add", "deploy/a.sh"], cwd=test_wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add deploy"], cwd=test_wt, check=True)
    diffs = agy_build.check_push_diff(test_wt, base="develop")
    check("chặn deploy/a.sh", any("deploy" in d for d in diffs))

    # 4. File Dockerfile, docker-compose.yml, install.sh, .env*
    for fname in ["Dockerfile", "docker-compose.yml", "install.sh", ".env.staging"]:
        with open(os.path.join(test_wt, fname), "w") as f:
            f.write("content")
        subprocess.run(["git", "add", fname], cwd=test_wt, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"add {fname}"], cwd=test_wt, check=True)
        diffs = agy_build.check_push_diff(test_wt, base="develop")
        check(f"chặn {fname}", any(fname in d for d in diffs))

    # 5. Khớp protected_paths
    os.makedirs(os.path.join(test_wt, "secrets"), exist_ok=True)
    with open(os.path.join(test_wt, "secrets", "api.key"), "w") as f:
        f.write("secret")
    subprocess.run(["git", "add", "secrets/api.key"], cwd=test_wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add secret"], cwd=test_wt, check=True)
    diffs = agy_build.check_push_diff(test_wt, base="develop", protected_paths=["secrets/*"])
    check("chặn protected_paths secrets/*", any("protected" in d for d in diffs))

    # 6. Symlink
    os.symlink("/etc/hosts", os.path.join(test_wt, "hosts_link"))
    subprocess.run(["git", "add", "hosts_link"], cwd=test_wt, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add symlink"], cwd=test_wt, check=True)
    diffs = agy_build.check_push_diff(test_wt, base="develop")
    check("chặn symlink mode 120000", any("symlink" in d for d in diffs))

    print("\n[6] Kiểm tra End-to-End: chặn push khi vi phạm diff & push/PR đúng repo")
    # Chạy task t4 với agy tạo file cấm .github -> app phát hiện và chặn push
    t4 = db.save_gen_session_todo(cid, title="Task 4 vi phạm", description="Test chặn push", viec_ref="VIEC-104", priority="high")
    tid4 = t4["id"]
    os.environ["FAKE_AGY_MULTI_MODE"] = "bad_diff_github"
    res4 = db.assign_task_to_role(tid4, "gw-backend-agy", repo="kho-ryan", wait=True)
    did4 = res4.get("dispatch_id")
    row4 = db._get_dispatch_row(did4)
    check("dispatch vi phạm diff có status failed", row4.get("status") == "failed")
    check("summary chứa 'Chặn push:'", "Chặn push:" in (row4.get("summary") or ""))
    check("không push nhánh khi bị chặn", not row4.get("build_push"))

    # Chạy task t5 thành công với kho-ryan -> push lên origin của kho-ryan, tạo PR nháp
    PR_REQUESTS.clear()
    t5 = db.save_gen_session_todo(cid, title="Task 5 ok", description="Test push và PR", viec_ref="VIEC-105", priority="high")
    tid5 = t5["id"]
    os.environ["FAKE_AGY_MULTI_MODE"] = "ok"
    res5 = db.assign_task_to_role(tid5, "gw-backend-agy", repo="kho-ryan", wait=True)
    did5 = res5.get("dispatch_id")
    row5 = db._get_dispatch_row(did5)
    check("dispatch ok có status done", row5.get("status") == "done")
    push_info = json.loads(row5.get("build_push") or "{}")
    check("push thành công", push_info.get("ok") is True)
    check("push đúng ref wt/TSK-n", push_info.get("ref") == f"wt/{tid5}")

    # Kiểm tra PR nháp gửi đúng base develop của kho-ryan
    check("có gửi request tạo PR", len(PR_REQUESTS) > 0)
    if PR_REQUESTS:
        last_pr = PR_REQUESTS[-1]
        check("PR head đúng nhánh", last_pr["data"].get("head") == f"wt/{tid5}")
        check("PR base đúng base của kho-ryan ('develop')", last_pr["data"].get("base") == "develop")
        check("PR draft là True", last_pr["data"].get("draft") is True)

    extra_tests(cid)

    # Dọn dẹp
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{total - failed}/{total} test pass\n")
    if failed > 0:
        sys.exit(1)


def _git_t(args, cwd):
    return subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@test.com"] + args, cwd=cwd,
                          capture_output=True, text=True, check=True)


def _fresh_clone(name):
    d = os.path.join(TMP, name)
    subprocess.run(["git", "clone", "-q", "-b", "develop", RYAN_REMOTE, d], check=True)
    return d


def _commit_file(wt, rel, content="x"):
    full = os.path.join(wt, rel)
    os.makedirs(os.path.dirname(full) or wt, exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    _git_t(["add", "--", rel], wt)
    _git_t(["commit", "-q", "-m", f"add {rel}"], wt)


def extra_tests(cid):
    print("\n[7] Review TSK-33: validate cấu hình (slug/base/clone_dir), không nhận clone_url")
    saved = os.environ["GW_BUILD_REPOS"]
    os.environ["GW_BUILD_REPOS"] = json.dumps([
        {"key": "bad-slug", "slug": "--upload-pack=touch /tmp/x/y"},
        {"key": "bad-slug2", "slug": "a/b; rm -rf /"},
        {"key": "no-slug"},
        {"key": "bad-base", "slug": "o/r", "base": "--output=/tmp/x"},
        {"key": "bad-base2", "slug": "o/r", "base": "a..b"},
        {"key": "out-dir", "slug": "o/r", "clone_dir": "/tmp"},
        {"key": "up-dir", "slug": "o/r", "clone_dir": "../escape"},
        {"key": "root-dir", "slug": "o/r", "clone_dir": "."},
        {"key": "url-ignored", "slug": "o/r", "clone_url": "--upload-pack=evil"},
        {"key": "gen-workplace", "slug": "../../evil", "base": "-x"},
    ])
    keys = {r["key"]: r for r in agy_build.build_repos(auto_clone=False)}
    for k in ["bad-slug", "bad-slug2", "no-slug", "bad-base", "bad-base2", "out-dir", "up-dir", "root-dir"]:
        check(f"cấu hình '{k}' bị bỏ qua", k not in keys)
    check("clone_url trong cấu hình bị bỏ qua (URL chỉ dựng từ slug)",
          "url-ignored" in keys and "clone_url" not in keys["url-ignored"])
    check("clone URL dựng từ slug", agy_build.clone_url_for("o/r") == f"{CLONE_BASE}/o/r.git")
    check("slug lạ không dựng URL", agy_build.clone_url_for("-x/y") == "" and agy_build.clone_url_for("a/b/c") == "")
    check("gen-workplace bỏ qua slug/base sai", keys["gen-workplace"]["slug"] != "../../evil"
          and keys["gen-workplace"]["base"] != "-x")

    # get_repo chỉ clone đúng repo được hỏi
    other_remote = os.path.join(CLONE_BASE, "o", "other.git")
    subprocess.run(["git", "clone", "-q", "--bare", RYAN_REMOTE, other_remote], check=True)
    os.environ["GW_BUILD_REPOS"] = json.dumps([{"key": "other-a", "slug": "o/other", "base": "develop"},
                                               {"key": "other-b", "slug": "o/other", "base": "develop"}])
    ra = agy_build.get_repo("other-a", auto_clone=True)
    check("get_repo clone repo được hỏi", ra and os.path.isdir(os.path.join(GW_REPOS_ROOT, "other-a", ".git")))
    check("get_repo KHÔNG clone repo khác", not os.path.exists(os.path.join(GW_REPOS_ROOT, "other-b")))
    os.environ["GW_BUILD_REPOS"] = saved

    print("\n[8] Review TSK-33: check_push_diff — rename, xoá, quote path, hoa thường, lồng, submodule, fail closed")
    wt = _fresh_clone("d_rename_src")
    _commit_file(wt, ".github/workflows/a.yml")
    _git_t(["push", "-q", "origin", "HEAD:refs/heads/develop"], wt)   # base đã có .github
    wt2 = _fresh_clone("d_rename")
    _git_t(["mv", ".github/workflows/a.yml", "moved.yml"], wt2)
    _git_t(["commit", "-q", "-m", "mv"], wt2)
    check("rename ra khỏi .github bị chặn", any(".github" in d for d in agy_build.check_push_diff(wt2, base="develop")))
    wt3 = _fresh_clone("d_delete")
    _git_t(["rm", "-q", ".github/workflows/a.yml"], wt3)
    _git_t(["commit", "-q", "-m", "rm"], wt3)
    check("xoá file .github bị chặn", any(".github" in d for d in agy_build.check_push_diff(wt3, base="develop")))

    wt4 = _fresh_clone("d_quote")
    _commit_file(wt4, ".github/workflows/ế x.yml")
    check("đường dẫn có ký tự lạ (git quote) dưới .github bị chặn",
          any(".github" in d for d in agy_build.check_push_diff(wt4, base="develop")))
    wt5 = _fresh_clone("d_case")
    _commit_file(wt5, ".GitHub/workflows/x.yml")
    _commit_file(wt5, "DockerFile")
    d5 = agy_build.check_push_diff(wt5, base="develop")
    check(".GitHub/ (hoa thường) bị chặn", any(".GitHub" in d for d in d5))
    check("DockerFile (hoa thường) bị chặn", any("DockerFile" in d for d in d5))
    wt6 = _fresh_clone("d_nested")
    _commit_file(wt6, "docs/.github/notes.md")
    _commit_file(wt6, "src/deploy/helper.py")
    check("sub/.github/ và src/deploy/ (không ở gốc) KHÔNG bị chặn",
          agy_build.check_push_diff(wt6, base="develop") == [])
    wt7 = _fresh_clone("d_submodule")
    sha = _git_t(["rev-parse", "HEAD"], wt7).stdout.strip()
    _git_t(["update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/sub"], wt7)
    _git_t(["commit", "-q", "-m", "submodule"], wt7)
    check("submodule mode 160000 bị chặn", any("160000" in d for d in agy_build.check_push_diff(wt7, base="develop")))
    wt8 = _fresh_clone("d_symdel")
    os.symlink("README.md", os.path.join(wt8, "lnk"))
    _git_t(["add", "lnk"], wt8)
    _git_t(["commit", "-q", "-m", "lnk"], wt8)
    _git_t(["push", "-q", "origin", "HEAD:refs/heads/develop"], wt8)
    _git_t(["rm", "-q", "lnk"], wt8)
    _git_t(["commit", "-q", "-m", "rm lnk"], wt8)
    check("xoá symlink sẵn có không bị chặn (chỉ chặn thêm/sửa symlink)",
          agy_build.check_push_diff(wt8, base="develop") == [])
    wt9 = _fresh_clone("d_noref")
    _commit_file(wt9, "ok.py")
    d9 = agy_build.check_push_diff(wt9, base="khong-co-nhanh")
    check("không có origin/<base> → chặn (fail closed)", len(d9) == 1 and "không kiểm được" in d9[0])
    check("base dạng option bị chặn", agy_build.check_push_diff(wt9, base="--output=/tmp/x") != [])
    wt10 = _fresh_clone("d_prot")
    _commit_file(wt10, "config/locked/a.json")
    check("protected_paths dạng thư mục bị chặn",
          any("protected" in d for d in agy_build.check_push_diff(wt10, base="develop", protected_paths=["config/locked/"])))
    wt11 = _fresh_clone("d_stale")
    _commit_file(wt11, "normal2.py")
    other = _fresh_clone("d_stale_other")
    _commit_file(other, ".gitea/x.yml")
    _git_t(["push", "-q", "origin", "HEAD:refs/heads/develop"], other)   # origin/develop tiến lên sau khi tạo nhánh
    _git_t(["fetch", "-q", "origin"], wt11)
    _git_t(["merge", "-q", "--no-edit", "origin/develop"], wt11)
    check("merge base mới vào nhánh không bị chặn nhầm (diff theo merge-base)",
          agy_build.check_push_diff(wt11, base="develop") == [])

    print("\n[9] Review TSK-33: realpath chặn worktree thoát gw-worktrees/<key>/, task bảng todos cũ không nhận repo")
    evil = os.path.join(TMP, "evil-target")
    os.makedirs(evil, exist_ok=True)
    link = os.path.join(GW_WORKTREE_ROOT, "kho-ryan")
    shutil.rmtree(link, ignore_errors=True)
    os.symlink(evil, link)
    r = agy_build.ensure_task_worktree("TSK-99901", "kho-ryan")
    check("gw-worktrees/<key> là symlink ra ngoài → từ chối", not r.get("ok") and "thoát" in (r.get("error") or ""))
    check("không tạo gì ở đích symlink", os.listdir(evil) == [])
    os.unlink(link)
    check("cleanup từ chối repo key sai", agy_build.cleanup_task_worktree("TSK-99901", "../x").get("removed") is False)

    with db.get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO roadmaps (id, project_id, title) VALUES (?, ?, ?)",
                     ("RM-TEST-33", "PRJ-GEN-WORKPLACE", "roadmap test"))
        conn.execute("INSERT INTO todos (id, roadmap_id, project_id, title, assigned_role, status) VALUES (?, ?, ?, ?, ?, ?)",
                     ("TSK-99902", "RM-TEST-33", "PRJ-GEN-WORKPLACE", "todo cũ", "backend", "pending"))
        conn.commit()
    rr = db.assign_task_to_role("TSK-99902", "gw-backend-agy", repo="kho-ryan")
    check("task bảng todos cũ + repo khác → bad_repo (không chạy nhầm gen-workplace)", rr.get("code") == "bad_repo", rr)

    print("\n[10] Review TSK-33: detect_violations kiểm thêm repo app khi Làm ở repo khác")
    app_snap = agy_build._repo_snapshot(GW_MAIN_REPO)
    _git_t(["commit", "-q", "--allow-empty", "-m", "lén sửa main app"], GW_MAIN_REPO)
    wt_r = _fresh_clone("d_viol")
    _git_t(["checkout", "-q", "-b", "wt/TSK-99903"], wt_r)
    v = agy_build.detect_violations(wt_r, agy_build._repo_snapshot(wt_r, base="develop"), wt_r, "wt/TSK-99903",
                                    base="develop", extra=[(GW_MAIN_REPO, app_snap, "main")])
    check("main của repo app đổi bị phát hiện", any("gw_main_repo" in x for x in v), v)
    _git_t(["reset", "-q", "--hard", "HEAD~1"], GW_MAIN_REPO)


if __name__ == "__main__":
    main_test()
