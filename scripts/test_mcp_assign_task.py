#!/usr/bin/env python3
"""
Test Issue #61: tool MCP assign_task {task_id, session_id, mode?} — agent chỉ có connector MCP giao được chế độ "Làm".
Chạy không cần agy / tmux / GitHub thật (agy giả qua GW_AGY_BIN, repo dispatch tạm có remote giả như test_agy_build.py).
Kiểm:
- assign_task có trong tools/list (inputSchema, readOnlyHint=false) và TOOL_META (nhóm kanban, scope kanban, read_only false).
- tools/call mode mặc định = build: gọi đúng db.assign_task_to_role, trả dispatch_id + branch + worktree_dir như REST;
  lệnh agy KHÔNG có --mode plan; dispatch_id dùng được với wait_worker_result (done, nhánh wt/TSK-n đã push).
- mode=review: lệnh agy --mode plan, không tạo worktree task.
- Lỗi → isError + code + http_status: 404 không có task, 409 locked / already_done / busy, 400 mode sai / vai sai / engine.
- Quyền: HTTP /mcp bắt buộc token — token scope kanban / all / tên tool gọi được; token swarm / chat → 401 "không có quyền".
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-mcp-assign-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

ARGS_LOG = os.path.join(TMP, "args.log")
REPO = os.path.join(TMP, "repo")
REMOTE = os.path.join(TMP, "remote.git")
HOME = os.path.join(TMP, "home")

AGY = os.path.join(FAKEBIN, "agy")
with open(AGY, "w") as f:
    f.write(f'''#!/usr/bin/env python3
import json, os, re, subprocess, sys, time
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
open({ARGS_LOG!r}, "a").write(json.dumps(args) + "\\n")
if os.environ.get("FAKE_AGY_MODE") == "slow":
    time.sleep(2)
if "--mode" not in args:
    m = re.search(r"THÔNG TIN VIỆC (TSK-[0-9]+)", prompt)
    tid = m.group(1) if m else "TSK-0"
    os.makedirs("backend", exist_ok=True)
    with open("backend/feature.py", "w") as fh:
        fh.write("def tinh_nang():\\n    return %r\\n" % time.time())
    subprocess.run(["git", "add", "backend/feature.py"], capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", tid + ": thêm tinh_nang"], capture_output=True)
print(json.dumps({{"event": "result", "result": {{"status": "SUCCESS", "response": "Đã đổi gì: backend/feature.py\\nRủi ro: thấp"}}}},
                 ensure_ascii=False))
sys.exit(0)
''')
os.chmod(AGY, 0o755)


def sh(*a, cwd=None):
    return subprocess.run(list(a), cwd=cwd, capture_output=True, text=True)


GITC = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]
sh("git", "init", "-q", "--bare", "-b", "main", REMOTE)
os.makedirs(os.path.join(REPO, "scripts"))
os.makedirs(os.path.join(REPO, "backend"))
sh("git", "init", "-q", "-b", "main", REPO)
with open(os.path.join(REPO, "backend", "app.py"), "w") as f:
    f.write("X = 1\n")
with open(os.path.join(REPO, "scripts", "test_ok.py"), "w") as f:
    f.write("import sys\nprint('ok')\nsys.exit(0)\n")
sh(*GITC, "add", ".", cwd=REPO)
sh(*GITC, "commit", "-q", "-m", "init", cwd=REPO)
sh("git", "remote", "add", "origin", REMOTE, cwd=REPO)
sh("git", "push", "-q", "origin", "main", cwd=REPO)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = HOME
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY
os.environ["GW_DISPATCH_REPO"] = REPO
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "gw-worktrees")
os.environ["GW_TMUX_INIT_DIR"] = os.path.join(TMP, "tmux-init")
os.environ["GW_BUILD_GITHUB_REPO"] = "chu-so-huu/gen-workplace"
for k in ("GW_AGY_WRITE_ROLES", "GW_WARROOM_SKIP_PERMISSIONS", "GW_AGY_PLAN_ALLOW", "GW_AGY_NO_STREAM", "FAKE_AGY_MODE",
          "GITHUB_TOKEN", "GW_GITHUB_API_URL", "GW_BUILD_TEST_EXCLUDE", "GW_BUILD_BASE", "GW_PUBLIC_ORIGIN"):
    os.environ.pop(k, None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.path.join(HOME, ".gemini", "antigravity-cli"))
sys.path.insert(0, ROOT)

from backend import db, main, mcp_core  # noqa: E402

db.fetch_live_google_quota = lambda profile_id="owner_default", force=False: None

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


conv = db.create_gen_conversation("PRJ-GEN-WORKPLACE", "VIEC-12: test assign_task MCP")
CONV = conv.get("id") or conv.get("conv_id")
with db.get_connection() as conn:
    conn.execute("UPDATE tmux_sessions SET account_type = 'owner_default', profile_dir = ''")
    conn.commit()


def new_task(title):
    return db.save_gen_session_todo(CONV, None, title, "Thêm hàm tinh_nang", "todo", "high", "Gen Core",
                                    [{"id": "chk-a", "text": "thêm backend/feature.py", "done": False}], viec_ref="VIEC-12")["id"]


def rpc(name, args):
    """tools/call qua handle_jsonrpc (đúng đường /mcp dùng). Trả (isError, payload JSON)."""
    resp = mcp_core.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = resp["result"]
    try:
        payload = json.loads(res["content"][0]["text"])
    except Exception:
        payload = {"_text": res["content"][0]["text"]}
    return res.get("isError"), payload


def last_args():
    lines = [x for x in open(ARGS_LOG).read().splitlines() if x.strip()] if os.path.exists(ARGS_LOG) else []
    return json.loads(lines[-1]) if lines else []


def remote_branch(branch):
    r = sh("git", "rev-parse", "--verify", "-q", f"refs/heads/{branch}", cwd=REMOTE)
    return r.stdout.strip() if r.returncode == 0 else ""


print("[1] tools/list + TOOL_META")
tl = mcp_core.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
tool = next((t for t in tl if t["name"] == "assign_task"), None)
check("assign_task có trong tools/list", tool is not None)
props = (tool or {}).get("inputSchema", {}).get("properties", {})
check("inputSchema: task_id, session_id bắt buộc; mode enum build/review mặc định build; không có engine",
      tool and set(tool["inputSchema"]["required"]) == {"task_id", "session_id"} and props.get("mode", {}).get("enum") == ["build", "review"]
      and props["mode"].get("default") == "build" and "engine" not in props, str(props)[:300])
check("annotations.readOnlyHint = false", tool and tool["annotations"]["readOnlyHint"] is False)
meta = mcp_core.TOOL_META.get("assign_task") or {}
check("TOOL_META: nhóm kanban, scope kanban, read_only false", meta.get("group") == "kanban" and meta.get("scope") == "kanban"
      and meta.get("read_only") is False, str(meta))
check("TOOL_META mô tả nêu mode=build toàn quyền trên máy Fedora", "mode=build" in meta.get("summary", "") and "toàn quyền trên máy Fedora" in meta.get("summary", ""))
check("db.MCP_TOOL_SCOPES có assign_task = kanban", db.MCP_TOOL_SCOPES.get("assign_task") == "kanban")
check("tool_catalog có assign_task", any(t["name"] == "assign_task" for t in mcp_core.tool_catalog()))
check("instructions dặn dùng assign_task cho việc sửa code qua task", "assign_task" in mcp_core.MCP_INSTRUCTIONS
      and "assign_task" in mcp_core.MCP_INSTRUCTIONS)

print("[2] mode mặc định = build: lệnh agy không --mode plan, trả dispatch_id + branch + worktree_dir, chờ bằng wait_worker_result")
T1 = new_task("[THỬ] MCP Làm")
err, res = rpc("assign_task", {"task_id": T1, "session_id": "backend", "author": "Claude (điều phối)"})
check("isError false, http_status 200, mode build", err is False and res.get("http_status") == 200 and res.get("mode") == "build", str(res)[:300])
WT1 = os.path.join(os.environ["GW_WORKTREE_ROOT"], T1)
check("trả dispatch_id + branch wt/TSK-n + worktree_dir", isinstance(res.get("dispatch_id"), int) and res.get("branch") == f"wt/{T1}"
      and res.get("worktree_dir") == WT1, str(res)[:300])
check("session_id = gw-backend-agy, claim cho worker", res.get("session_id") == "gw-backend-agy" and db._task_detail(T1)["holder"] == "gw-backend-agy")
check("gợi ý bước tiếp wait_worker_result", "wait_worker_result" in res.get("next", ""))
werr, w = rpc("wait_worker_result", {"dispatch_id": res.get("dispatch_id"), "timeout_sec": 60})
check("wait_worker_result(dispatch_id) → done, mode build, đúng nhánh", werr is False and w.get("status") == "done" and w.get("mode") == "build"
      and w.get("build_branch") == f"wt/{T1}" and w.get("task_id") == T1, str(w)[:400])
args = last_args()
check("lệnh agy KHÔNG có --mode plan (chế độ Làm)", args and "--mode" not in args and "plan" not in args, str(args)[:200])
check("nhánh wt/TSK-n đã push lên remote giả, đúng commit", w.get("build_commit") and remote_branch(f"wt/{T1}") == w.get("build_commit"))
with db.get_connection() as conn:
    msg = conn.execute("SELECT author, body FROM chat_messages WHERE id = ?", (res.get("request_msg_id"),)).fetchone()
check("tin giao [Làm] trong war-room, tác giả là người giao", msg and msg["author"] == "Claude (điều phối)" and msg["body"].startswith("[Làm] @backend"))

print("[3] cùng hàm với REST: khóa trả về giống POST /api/task/assign")
server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"


def call(method, path, payload=None, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode() if payload is not None else None, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


T2 = new_task("[THỬ] REST Làm")
st, rest = call("POST", "/api/task/assign", {"todo_id": T2, "session_id": "backend"})
check("REST build 200", st == 200 and rest.get("mode") == "build", f"{st} {rest}")
check("MCP trả đủ khóa như REST (+ http_status, next)", set(rest) <= set(res) and set(res) - set(rest) == {"http_status", "next"},
      f"{sorted(set(rest) ^ set(res))}")
db.wait_worker_result(dispatch_id=rest.get("dispatch_id"), timeout_sec=60)

print("[4] mode=review: agy --mode plan, không tạo worktree task")
T3 = new_task("[THỬ] MCP Rà soát")
open(ARGS_LOG, "w").close()
err, res = rpc("assign_task", {"task_id": T3, "session_id": "gw-qa-agy", "mode": "review"})
check("review: isError false, mode review, có dispatch_id", err is False and res.get("mode") == "review" and isinstance(res.get("dispatch_id"), int), str(res)[:300])
werr, w = rpc("wait_worker_result", {"dispatch_id": res.get("dispatch_id"), "timeout_sec": 60})
args = last_args()
check("review: lệnh agy có --mode plan", args and "--mode" in args and args[args.index("--mode") + 1] == "plan", str(args)[:200])
check("review: wait_worker_result done, không phải build", w.get("status") == "done" and w.get("mode") != "build", str(w)[:300])
check("review: không tạo worktree task", not os.path.exists(os.path.join(os.environ["GW_WORKTREE_ROOT"], T3)))

print("[5] lỗi → isError + code + http_status (400 / 404 / 409)")
err, res = rpc("assign_task", {"task_id": "TSK-987654", "session_id": "backend"})
check("task không có → isError, 404 not_found", err is True and res.get("http_status") == 404 and res.get("code") == "not_found", str(res))
T4 = new_task("[THỬ] đang bị giữ")
db.claim_task("claude-dieu-phoi", T4)
err, res = rpc("assign_task", {"task_id": T4, "session_id": "backend"})
check("worker khác đang giữ → isError, 409 locked, có held_by", err is True and res.get("http_status") == 409 and res.get("code") == "locked"
      and res.get("held_by") == "claude-dieu-phoi", str(res))
ok = db.complete_task("gw-backend-agy", T1, remote_branch(f"wt/{T1}"))
err, res = rpc("assign_task", {"task_id": T1, "session_id": "backend"})
check("task đã done → isError, 409 already_done", ok.get("status") == "completed" and err is True and res.get("http_status") == 409
      and res.get("code") == "already_done", f"{ok} {res}")
T5 = new_task("[THỬ] đang chạy")
os.environ["FAKE_AGY_MODE"] = "slow"
err1, r1 = rpc("assign_task", {"task_id": T5, "session_id": "backend"})
time.sleep(0.3)
err, res = rpc("assign_task", {"task_id": T5, "session_id": "backend"})
check("đang có lần Làm chạy → isError, 409 busy, trả dispatch đang chạy", err1 is False and err is True and res.get("http_status") == 409
      and res.get("code") == "busy" and res.get("dispatch_id") == r1.get("dispatch_id"), str(res))
db.wait_worker_result(dispatch_id=r1.get("dispatch_id"), timeout_sec=60)
os.environ.pop("FAKE_AGY_MODE", None)
T6 = new_task("[THỬ] tham số sai")
err, res = rpc("assign_task", {"task_id": T6, "session_id": "backend", "mode": "xoa-het"})
check("mode sai → isError, 400 bad_request", err is True and res.get("http_status") == 400 and res.get("code") == "bad_request", str(res))
err, res = rpc("assign_task", {"task_id": T6, "session_id": "hacker"})
check("vai sai → isError, 400", err is True and res.get("http_status") == 400, str(res))
err, res = rpc("assign_task", {"task_id": T6, "session_id": "security"})
check("vai đã bỏ (@security) → isError, 400 retired_role", err is True and res.get("http_status") == 400 and res.get("code") == "retired_role", str(res))
err, res = rpc("assign_task", {"task_id": T6, "session_id": "backend", "engine": "".join(["ju", "les"])})
check("engine bất kỳ → isError, 400, không giao", err is True and res.get("http_status") == 400 and "engine" in res.get("error", "")
      and not db._task_detail(T6)["holder"], str(res))
err, res = rpc("assign_task", {"session_id": "backend"})
check("thiếu task_id → isError, 400", err is True and res.get("http_status") == 400, str(res))

print("[6] quyền: /mcp bắt buộc token, scope kanban")
db.set_mcp_strict_auth(True)
toks = {}
for name, perms in (("kanban", ["kanban"]), ("all", ["all"]), ("tool", ["assign_task"]), ("swarm", ["swarm"]), ("chat", ["chat", "files"])):
    st, js = call("POST", "/api/mcp/tokens/create", {"name": f"t-{name}", "permissions": perms})
    toks[name] = js.get("token", "")
BODY = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "assign_task", "arguments": {"task_id": "TSK-987654", "session_id": "backend"}}}
st, js = call("POST", "/mcp", BODY)
check("không token → 401", st == 401, f"{st} {js}")
for name in ("kanban", "all", "tool"):
    st, js = call("POST", "/mcp", BODY, headers={"Authorization": f"Bearer {toks[name]}"})
    check(f"token {name} → qua xác thực, tool chạy (404 isError)", st == 200 and js.get("result", {}).get("isError") is True
          and '"http_status": 404' in js["result"]["content"][0]["text"], f"{st} {str(js)[:200]}")
for name in ("swarm", "chat"):
    st, js = call("POST", "/mcp", BODY, headers={"Authorization": f"Bearer {toks[name]}"})
    check(f"token {name} (không có kanban) → 401 không có quyền assign_task", st == 401
          and "assign_task" in js.get("error", {}).get("message", ""), f"{st} {js}")

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)
