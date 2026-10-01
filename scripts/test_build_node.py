#!/usr/bin/env python3
"""
Test bộ B2 (TSK-34 / Issue #57 mục 5, 7):
1. build_repos loại key gw-*/tsk-*
2. load_sop(kind) + roles/build.node.md + build_prompt ghi repo/kind/lệnh test
3. node_modules: npm ci --ignore-scripts từ lockfile origin/base, cache theo sha256, symlink + exclude
4. run_tests kind=node: lệnh cố định (test_cmd hoặc node --test tests/*.test.mjs), không đọc scripts package.json
5. deps_changed khi nhánh đổi package.json/lockfile, đưa vào summary/PR
6. Test không mạng (hoàn toàn offline với mock / local)
"""
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.mkdtemp(prefix="gw-test-node-")
DATA_DIR = os.path.join(TMP, "data")
HOME_DIR = os.path.join(TMP, "home")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(HOME_DIR, exist_ok=True)
os.makedirs(FAKEBIN, exist_ok=True)

NPM_LOG_FILE = os.path.join(TMP, "npm_calls.jsonl")
NODE_LOG_FILE = os.path.join(TMP, "node_calls.jsonl")
NODE_MODE_FILE = os.path.join(TMP, "node_mode.txt")
AGY_MODE_FILE = os.path.join(TMP, "agy_mode.txt")

# Fake npm runner ghi lại lời gọi và env (không mạng)
NPM_BIN = os.path.join(FAKEBIN, "npm")
with open(NPM_BIN, "w") as f:
    f.write(f'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
env_dump = {{k: v for k, v in os.environ.items()}}
with open(r"{NPM_LOG_FILE}", "a", encoding="utf-8") as f:
    f.write(json.dumps({{"args": args, "env": env_dump}}) + "\\n")

if "ci" in args:
    # Giả lập npm ci tạo node_modules
    nm = os.path.join(os.getcwd(), "node_modules")
    os.makedirs(os.path.join(nm, "fake-dep"), exist_ok=True)
    with open(os.path.join(nm, "fake-dep", "index.js"), "w") as f:
        f.write("module.exports = {{ ok: true }};")
    sys.exit(0)

sys.exit(0)
''')
os.chmod(NPM_BIN, 0o755)

# Fake node runner để test node --test
NODE_BIN = os.path.join(FAKEBIN, "node")
with open(NODE_BIN, "w") as f:
    f.write(f'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
try:
    with open(r"{NODE_LOG_FILE}", "a", encoding="utf-8") as f:
        f.write(json.dumps({{"args": args, "cwd": os.getcwd()}}) + "\\n")
except Exception:
    pass

mode = "ok"
if os.path.exists(r"{NODE_MODE_FILE}"):
    with open(r"{NODE_MODE_FILE}", "r", encoding="utf-8") as f:
        mode = f.read().strip()

if mode == "fail":
    print("Test failure in test suite", file=sys.stderr)
    sys.exit(1)
elif mode == "timeout":
    import time
    time.sleep(10)
    sys.exit(0)

# Mặc định pass
print("✔ test sample (1.2ms)")
print("ℹ tests 1")
print("ℹ pass 1")
print("ℹ fail 0")
sys.exit(0)
''')
os.chmod(NODE_BIN, 0o755)

# Fake agy binary
AGY_BIN = os.path.join(FAKEBIN, "agy")
with open(AGY_BIN, "w") as f:
    f.write(f'''#!/usr/bin/env python3
import json, os, re, subprocess, sys
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
m = re.search(r"THÔNG TIN VIỆC (TSK-[0-9]+)", prompt)
tid = m.group(1) if m else "TSK-0"

mode = "ok"
if os.path.exists(r"{AGY_MODE_FILE}"):
    with open(r"{AGY_MODE_FILE}", "r", encoding="utf-8") as f:
        mode = f.read().strip()

if mode == "ok":
    with open("feature.js", "w") as fh:
        fh.write("console.log('feature');\\n")
    subprocess.run(["git", "add", "feature.js"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"{{tid}}: thêm feature"], check=True)
elif mode == "deps_changed":
    with open("package.json", "w") as fh:
        fh.write('{{"name": "test-node", "dependencies": {{"fake-dep": "^2.0.0"}}}}')
    subprocess.run(["git", "add", "package.json"], check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"{{tid}}: cập nhật package.json"], check=True)

res = {{
    "event": "result",
    "result": {{
        "status": "SUCCESS",
        "response": f"Đã làm việc {{tid}}\\n[KANBAN_UPDATE: {{tid}} | CHECK: chk-1]"
    }}
}}
print(json.dumps(res, ensure_ascii=False))
sys.exit(0)
''')
os.chmod(AGY_BIN, 0o755)

# Mock GitHub API server
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

# Tạo repo chính gen-workplace
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

# Tạo repo ngoài Node: node-app
CLONE_BASE = os.path.join(TMP, "remotes")
NODE_REMOTE = os.path.join(CLONE_BASE, "genesis-corp", "node-app.git")
os.makedirs(os.path.dirname(NODE_REMOTE), exist_ok=True)
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", NODE_REMOTE], check=True)

NODE_INIT = os.path.join(TMP, "node_init")
os.makedirs(NODE_INIT, exist_ok=True)
subprocess.run(["git", "init", "-q", "-b", "main", NODE_INIT], check=True)
with open(os.path.join(NODE_INIT, "package.json"), "w") as f:
    f.write(json.dumps({"name": "node-app", "version": "1.0.0", "scripts": {"test": "exit 99"}}, indent=2))
sample_lock = json.dumps({"name": "node-app", "version": "1.0.0", "lockfileVersion": 3, "packages": {}}, indent=2)
with open(os.path.join(NODE_INIT, "package-lock.json"), "w") as f:
    f.write(sample_lock)
os.makedirs(os.path.join(NODE_INIT, "tests"), exist_ok=True)
with open(os.path.join(NODE_INIT, "tests", "first.test.mjs"), "w") as f:
    f.write("import test from 'node:test';\ntest('sample', () => {});\n")
with open(os.path.join(NODE_INIT, "tests", "second.test.mjs"), "w") as f:
    f.write("import test from 'node:test';\ntest('sample 2', () => {});\n")

subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@test.com", "add", "."], cwd=NODE_INIT, check=True)
subprocess.run(["git", "-c", "user.name=test", "-c", "user.email=t@test.com", "commit", "-q", "-m", "init node app"], cwd=NODE_INIT, check=True)
subprocess.run(["git", "remote", "add", "origin", NODE_REMOTE], cwd=NODE_INIT, check=True)
subprocess.run(["git", "push", "-q", "origin", "main"], cwd=NODE_INIT, check=True)

GW_REPOS_ROOT = os.path.join(TMP, "gw-repos")
GW_WORKTREE_ROOT = os.path.join(TMP, "gw-worktrees")
os.makedirs(GW_REPOS_ROOT, exist_ok=True)
os.makedirs(GW_WORKTREE_ROOT, exist_ok=True)

# Thiết lập biến môi trường
os.environ["DATA_DIR"] = DATA_DIR
os.environ["HOME"] = HOME_DIR
os.environ["GW_AGY_BIN"] = AGY_BIN
os.environ["GW_DISPATCH_REPO"] = GW_MAIN_REPO
os.environ["GW_REPOS_ROOT"] = GW_REPOS_ROOT
os.environ["GW_WORKTREE_ROOT"] = GW_WORKTREE_ROOT
os.environ["GW_GITHUB_API_URL"] = f"http://127.0.0.1:{mock_port}"
os.environ["GITHUB_TOKEN"] = "fake-github-token-secret-xyz"
os.environ["GW_BUILD_CLONE_BASE"] = CLONE_BASE
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")

# Cấu hình multirepo có repo node
REPOS_CONFIG = [
    {
        "key": "node-app",
        "slug": "genesis-corp/node-app",
        "base": "main",
        "kind": "node"
    },
    {
        "key": "node-custom-cmd",
        "slug": "genesis-corp/node-app",
        "base": "main",
        "kind": "node",
        "test_cmd": "node --test tests/first.test.mjs"
    }
]
os.environ["GW_BUILD_REPOS"] = json.dumps(REPOS_CONFIG)

sys.path.insert(0, str(ROOT))
from backend import db, agy_build  # noqa: E402

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


def test_suite():
    global total, failed

    print("\n[1] Kiểm tra build_repos loại key gw-*/tsk-*")
    bad_repos = [
        {"key": "gw-test", "slug": "a/b"},
        {"key": "gw-worktrees", "slug": "a/b"},
        {"key": "tsk-1", "slug": "a/b"},
        {"key": "tsk-backend", "slug": "a/b"},
        {"key": "repo-valid", "slug": "genesis-corp/node-app", "base": "main", "kind": "node"},
        {"key": "gen-workplace", "slug": "owner/gen-workplace", "base": "main"}
    ]
    os.environ["GW_BUILD_REPOS"] = json.dumps(bad_repos)
    repos = agy_build.build_repos(auto_clone=False)
    keys = [r["key"] for r in repos]
    check("loại bỏ key 'gw-test'", "gw-test" not in keys)
    check("loại bỏ key 'gw-worktrees'", "gw-worktrees" not in keys)
    check("loại bỏ key 'tsk-1'", "tsk-1" not in keys)
    check("loại bỏ key 'tsk-backend'", "tsk-backend" not in keys)
    check("nhận key hợp lệ 'repo-valid'", "repo-valid" in keys)
    check("giữ repo mặc định 'gen-workplace'", "gen-workplace" in keys)

    check("_wt_confined từ chối key gw-x", not agy_build._wt_confined("/tmp", "gw-x"))
    check("_wt_confined từ chối key tsk-1", not agy_build._wt_confined("/tmp", "tsk-1"))
    clean_res = agy_build.cleanup_task_worktree("TSK-99", "gw-evil")
    check("cleanup_task_worktree từ chối key gw-evil", clean_res.get("removed") is False)

    os.environ["GW_BUILD_REPOS"] = json.dumps(REPOS_CONFIG)

    print("\n[2] Kiểm tra load_sop(kind) + roles/build.node.md")
    sop_py = agy_build.load_sop("python")
    sop_node = agy_build.load_sop("node")
    sop_other = agy_build.load_sop("unknown-lang")

    check("load_sop('python') tải roles/build.md", "gen-workplace" in sop_py and "py_compile" in sop_py)
    check("load_sop('node') tải roles/build.node.md", "Node.js" in sop_node and "node --test" in sop_node)
    check("load_sop('node') có lưu ý không chạy npm install", "KHÔNG chạy npm install" in sop_node)
    check("load_sop('unknown') fallback về build.md", sop_other == sop_py)

    print("\n[3] Kiểm tra build_prompt ghi repo/kind/lệnh test")
    repo_node = agy_build.get_repo("node-app", auto_clone=False)
    prompt_node = agy_build.build_prompt("TSK-34", "backend", "/tmp/wt", "wt/TSK-34", base="main", repo_conf=repo_node)
    check("prompt node dùng SOP node", "Node.js" in prompt_node)
    check("prompt node ghi rõ repo 'node-app'", "repo 'node-app'" in prompt_node)
    check("prompt node ghi rõ kind 'node'", "kind 'node'" in prompt_node)
    check("prompt node ghi lệnh test mặc định", "node --test tests/*.test.mjs" in prompt_node)
    check("prompt node cấm node -e trong shell", "node -e" in prompt_node)

    repo_custom = agy_build.get_repo("node-custom-cmd", auto_clone=False)
    prompt_custom = agy_build.build_prompt("TSK-34", "backend", "/tmp/wt", "wt/TSK-34", base="main", repo_conf=repo_custom)
    check("prompt custom ghi đúng test_cmd từ cấu hình", "node --test tests/first.test.mjs" in prompt_custom)

    repo_py = agy_build.get_repo("gen-workplace", auto_clone=False)
    prompt_py = agy_build.build_prompt("TSK-34", "backend", "/tmp/wt", "wt/TSK-34", base="main", repo_conf=repo_py)
    check("prompt python ghi lệnh test python", "python3 -m py_compile" in prompt_py)

    print("\n[4] Kiểm tra ensure_node_modules an toàn (cache sha256, symlink, exclude, env sạch)")
    wt_node = os.path.join(TMP, "wt_test_node")
    subprocess.run(["git", "clone", "-q", "-b", "main", NODE_REMOTE, wt_node], check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=wt_node, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=wt_node, check=True)

    if os.path.exists(NPM_LOG_FILE):
        os.remove(NPM_LOG_FILE)

    res_nm = agy_build.ensure_node_modules(wt_node, repo_node)
    check("ensure_node_modules trả về ok=True", res_nm.get("ok") is True)
    check("cached=True", res_nm.get("cached") is True)

    lock_data = subprocess.run(["git", "show", "main:package-lock.json"], cwd=wt_node, capture_output=True, text=True, check=True).stdout
    expected_sha = hashlib.sha256(lock_data.encode("utf-8")).hexdigest()
    expected_cache_dir = os.path.join(DATA_DIR, "cache", "node_modules", "node-app", expected_sha)
    check("thư mục cache đúng đường dẫn và sha256", res_nm.get("cache_dir") == expected_cache_dir)
    check("thư mục cache tồn tại trên đĩa", os.path.isdir(expected_cache_dir))
    check("thư mục cache chứa package được cài", os.path.exists(os.path.join(expected_cache_dir, "fake-dep", "index.js")))

    wt_nm_link = os.path.join(wt_node, "node_modules")
    check("worktree có symlink node_modules", os.path.islink(wt_nm_link))
    check("symlink trỏ tới cache_dir", os.path.realpath(wt_nm_link) == os.path.realpath(expected_cache_dir))

    r_ex = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=wt_node, capture_output=True, text=True, check=True).stdout.strip()
    ex_path = os.path.abspath(os.path.join(wt_node, r_ex))
    with open(ex_path, "r", encoding="utf-8") as f:
        ex_content = f.read()
    check(".git/info/exclude chứa node_modules", "node_modules" in ex_content)

    st = subprocess.run(["git", "status", "--porcelain"], cwd=wt_node, capture_output=True, text=True, check=True).stdout
    check("git status sạch (node_modules không bị commit)", "node_modules" not in st)

    check("npm ci đã được gọi", os.path.exists(NPM_LOG_FILE))
    if os.path.exists(NPM_LOG_FILE):
        with open(NPM_LOG_FILE, "r", encoding="utf-8") as f:
            logs = [json.loads(line) for line in f if line.strip()]
        check("npm ci có log gọi", len(logs) > 0)
        if logs:
            first_call = logs[0]
            check("npm ci có cờ --ignore-scripts", "--ignore-scripts" in first_call["args"])
            check("npm ci có cờ --no-audit", "--no-audit" in first_call["args"])
            check("npm ci có cờ --no-fund", "--no-fund" in first_call["args"])
            env_keys = list(first_call["env"].keys())
            has_secret = any("TOKEN" in k or "SECRET" in k or "GITHUB" in k for k in env_keys)
            check("env của npm ci KHÔNG có biến TOKEN hay SECRET", not has_secret)

    calls_before = len(logs) if os.path.exists(NPM_LOG_FILE) else 0
    res_nm2 = agy_build.ensure_node_modules(wt_node, repo_node)
    check("gọi lần 2 trả ok=True", res_nm2.get("ok") is True)
    if os.path.exists(NPM_LOG_FILE):
        with open(NPM_LOG_FILE, "r", encoding="utf-8") as f:
            logs2 = [json.loads(line) for line in f if line.strip()]
        check("cache hit: không gọi lại npm ci", len(logs2) == calls_before)

    print("\n[5] Kiểm tra run_tests với kind=node")
    if os.path.exists(NODE_LOG_FILE):
        os.remove(NODE_LOG_FILE)
    if os.path.exists(NODE_MODE_FILE):
        os.remove(NODE_MODE_FILE)

    res_test1 = agy_build.run_tests(wt_node, repo_conf=repo_node)
    check("run_tests mặc định ok=True", res_test1.get("ok") is True)
    check("run_tests total=1", res_test1.get("total") == 1)
    check("run_tests passed=1", res_test1.get("passed") == 1)
    check("py_compile ok=True và output không áp dụng", res_test1["py_compile"]["ok"] is True and "không áp dụng" in res_test1["py_compile"]["output"])
    check("không bị ảnh hưởng bởi scripts trong package.json", res_test1.get("ok") is True)

    if os.path.exists(NODE_LOG_FILE):
        with open(NODE_LOG_FILE, "r", encoding="utf-8") as f:
            node_calls = [json.loads(line) for line in f if line.strip()]
        check("node runner đã được gọi", len(node_calls) > 0)
        if node_calls:
            call_args = node_calls[-1]["args"]
            check("lệnh bắt đầu bằng --test", call_args[0] == "--test")
            check("glob tìm thấy và sắp xếp tests/first.test.mjs trước", "tests/first.test.mjs" in call_args[1])
            check("glob tìm thấy tests/second.test.mjs sau", "tests/second.test.mjs" in call_args[2])

    if os.path.exists(NODE_LOG_FILE):
        os.remove(NODE_LOG_FILE)
    res_test2 = agy_build.run_tests(wt_node, repo_conf=repo_custom)
    check("run_tests với test_cmd ok=True", res_test2.get("ok") is True)
    if os.path.exists(NODE_LOG_FILE):
        with open(NODE_LOG_FILE, "r", encoding="utf-8") as f:
            node_calls2 = [json.loads(line) for line in f if line.strip()]
        if node_calls2:
            check("chạy đúng test_cmd (chỉ first.test.mjs)", len(node_calls2[-1]["args"]) == 2 and "first" in node_calls2[-1]["args"][1])

    # Khi node test fail
    with open(NODE_MODE_FILE, "w") as f:
        f.write("fail")
    res_fail = agy_build.run_tests(wt_node, repo_conf=repo_node)
    check("khi node test fail: ok=False", res_fail.get("ok") is False)
    check("khi node test fail: passed=0", res_fail.get("passed") == 0)
    check("tail chứa lỗi stderr", "Test failure" in res_fail["tests"][0]["tail"])
    if os.path.exists(NODE_MODE_FILE):
        os.remove(NODE_MODE_FILE)

    print("\n[6] Kiểm tra check_deps_changed khi sửa package.json / package-lock.json")
    subprocess.run(["git", "checkout", "-q", "-b", "wt/test-branch"], cwd=wt_node, check=True)

    with open(os.path.join(wt_node, "app.js"), "w") as f:
        f.write("console.log('hello');")
    subprocess.run(["git", "add", "app.js"], cwd=wt_node, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add app.js"], cwd=wt_node, check=True)
    check("sửa app.js: deps_changed == False", agy_build.check_deps_changed(wt_node, base="main") is False)

    with open(os.path.join(wt_node, "package.json"), "w") as f:
        f.write(json.dumps({"name": "node-app", "version": "1.0.1"}, indent=2))
    subprocess.run(["git", "add", "package.json"], cwd=wt_node, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "update package.json"], cwd=wt_node, check=True)
    check("sửa package.json: deps_changed == True", agy_build.check_deps_changed(wt_node, base="main") is True)

    subprocess.run(["git", "checkout", "-q", "main"], cwd=wt_node, check=True)
    subprocess.run(["git", "branch", "-D", "wt/test-branch"], cwd=wt_node, check=True)

    print("\n[7] Kiểm tra End-to-End: assign task node, deps_changed đưa vào summary & PR")
    conv = db.create_gen_conversation(title="Test Node Build")
    cid = conv["id"]
    t1 = db.save_gen_session_todo(cid, title="Node Task 1", description="Test task node deps changed", viec_ref="VIEC-201", priority="high")
    tid1 = t1["id"]

    # Đảm bảo repo được clone vào gw-repos trước khi assign
    agy_build.get_repo("node-app", auto_clone=True)

    with open(AGY_MODE_FILE, "w") as f:
        f.write("deps_changed")
    PR_REQUESTS.clear()
    res_assign = db.assign_task_to_role(tid1, "gw-backend-agy", repo="node-app", wait=True)
    check("assign_task_to_role thành công", res_assign.get("status") == "assigned", res_assign)
    did1 = res_assign.get("dispatch_id")
    row1 = db._get_dispatch_row(did1)

    check("dispatch row tồn tại", row1 is not None)
    if row1:
        check("dispatch node hoàn thành done", row1.get("status") == "done")
        summary = row1.get("summary") or ""
        check("summary chứa cảnh báo [Dependencies]", "[Dependencies]" in summary)
        check("summary ghi rõ deps_changed=True", "deps_changed=True" in summary)

    check("PR nháp được tạo", len(PR_REQUESTS) > 0)
    if PR_REQUESTS:
        last_pr = PR_REQUESTS[-1]
        pr_body = last_pr["data"].get("body") or ""
        check("PR body có ghi chú cảnh báo dependencies", "deps_changed=True" in pr_body)
        check("PR base là main của node-app", last_pr["data"].get("base") == "main")

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{total - failed}/{total} test pass\n")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    test_suite()
