#!/usr/bin/env python3
"""
Test Issue #43: worker Google Jules CHỈ MỞ PR. Chạy không cần mạng: Jules giả (scripts/fake_jules.py) trỏ qua
GW_JULES_BASE_URL, HTTP server app loopback cổng ngẫu nhiên, DB tạm.
- Chưa có key → assign (REST + MCP) báo lỗi rõ, không gọi Jules; status configured:false.
- Lưu key: file DATA_DIR/secrets/jules.key quyền 600; API chỉ trả configured + 4 ký tự cuối; key sai định dạng → 400.
- Tạo phiên (requirePlanApproval=true, AUTO_CREATE_PR) → poll thấy kế hoạch, ghi về phiên của task, KHÔNG tự duyệt
  → người duyệt (/api/jules/approve) → /api/dispatch/wait trả done kèm pr_url → PR ghi về phiên, task sang review.
- 401 / 429 báo rõ; vượt GW_JULES_MAX_CONCURRENT → từ chối; hủy (/api/jules/cancel) → DELETE phiên, nhả slot.
- Repo ngoài allowlist → từ chối, không tạo phiên.
- Key không lộ ở bất kỳ response API nào, không có trong DB, không có trong log.
- Không có đường code nào merge PR: client chỉ gọi endpoint trong danh sách trắng; mọi request tới Jules giả đều thuộc
  tập đã xác minh; không có endpoint / tool merge; quét backend không có lệnh merge PR / push.
"""
import ast
import contextlib
import io
import json
import os
import re
import stat
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-jules-")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fake_jules  # noqa: E402

FAKE_SRV = fake_jules.start(0)
FAKE = fake_jules.FAKE

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["GW_JULES_BASE_URL"] = f"http://127.0.0.1:{FAKE_SRV.server_address[1]}/v1alpha"
os.environ["GW_JULES_WAIT_REFRESH_SEC"] = "0"
os.environ["GW_JULES_MAX_CONCURRENT"] = "2"
os.environ.pop("GW_JULES_REPOS", None)
os.environ.pop("JULES_API_KEY", None)
os.environ.pop("GITHUB_TOKEN", None)
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])


class Tee(io.TextIOBase):
    """Ghi stdout ra màn hình và giữ lại để kiểm key không lộ trong log."""
    def __init__(self, real):
        self.real, self.buf = real, io.StringIO()

    def write(self, s):
        self.buf.write(s)
        return self.real.write(s)

    def flush(self):
        self.real.flush()


TEE = Tee(sys.stdout)
_redir = contextlib.redirect_stdout(TEE)
_redir.__enter__()

from backend import db, main, mcp_core, jules_worker  # noqa: E402

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


server = main.ThreadedHTTPServer(("127.0.0.1", 0), main.SwarmHandler)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"
RESPONSES = []   # mọi body response của app (kiểm key không lộ)


def call(method, path, payload=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw, status = resp.read().decode("utf-8"), resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8"), e.code
    RESPONSES.append(raw)
    try:
        return status, json.loads(raw)
    except Exception:
        return status, None


def mcp(name, args):
    res = mcp_core.execute_tool(name, args)
    text = res["content"][0]["text"]
    RESPONSES.append(text)
    try:
        return res.get("isError"), json.loads(text)
    except Exception:
        return res.get("isError"), {"raw": text}


def jules_requests(method=None, contains=None):
    with FAKE.lock:
        return [r for r in FAKE.requests if (method is None or r["method"] == method) and (contains is None or contains in r["path"])]


def creates():
    """Các request tạo phiên (POST /v1alpha/sessions)."""
    return [r for r in jules_requests("POST") if r["path"] == "/v1alpha/sessions"]


def conv_text():
    return "\n".join(m["content"] for m in db.get_gen_messages(CONV))


def task_row(tid):
    with db.get_connection() as conn:
        return dict(conn.execute("SELECT * FROM gen_session_todos WHERE id = ?", (tid,)).fetchone())


conv_res = db.create_gen_conversation("PRJ-GEN-WORKPLACE", "VIEC-13: test Jules")
CONV = conv_res.get("id")
TASKS = []
for i in range(4):
    r = db.save_gen_session_todo(CONV, None, f"Việc Jules {i}", "Sửa lỗi nhỏ", "todo", "medium", "Gen Core",
                                 [{"id": "chk-a", "text": "Sửa", "done": False}], viec_ref="VIEC-13")
    TASKS.append(r["id"])
T1, T2, T3, T4 = TASKS
check("tạo phiên + 4 task thử", CONV and all(TASKS), str(TASKS))

# ------------------------------------------------------------------ 1. chưa có key
print("[1] Chưa có key → tắt, lỗi rõ, không gọi Jules")
st, js = call("GET", "/api/jules/status?check=1")
check("status 200, configured:false, state no_key, enabled:false",
      st == 200 and js["configured"] is False and js["state"] == "no_key" and js["enabled"] is False, str(js))
check("allowlist mặc định chỉ gen-workplace, tối đa 2 phiên", js["allowed_repos"] == ["gen-workplace"] and js["max_concurrent"] == 2, str(js))
st, js = call("POST", "/api/task/assign", {"todo_id": T1, "engine": "jules"})
check("assign engine=jules → 400 not_configured, thông báo nhắc key", st == 400 and js.get("code") == "not_configured"
      and "key" in js.get("error", ""), f"{st} {js}")
err, js = mcp("assign_to_jules", {"task_id": T1})
check("MCP assign_to_jules → isError, not_configured", err and js.get("code") == "not_configured", str(js))
check("không có request nào tới Jules", not jules_requests(), str(FAKE.requests))
check("task không bị claim", task_row(T1)["claimed_by"] in ("", None) and task_row(T1)["status"] == "todo")
check("poll_once khi chưa có key: bỏ qua", jules_worker.poll_once().get("skipped") == "no_key")

# ------------------------------------------------------------------ 2. key
print("[2] Lưu / xóa key: file 600, API chỉ trả 4 ký tự cuối")
KEY = "AQ.FakeJulesKey_1234567890abcdefWXYZ"
st, js = call("POST", "/api/jules/key", {"key": "ngắn"})
check("key sai định dạng → 400", st == 400 and "error" in js, f"{st} {js}")
st, js = call("POST", "/api/jules/key", {"key": KEY})
check("lưu key → 200 configured, last4 WXYZ, nguồn file", st == 200 and js["configured"] and js["key_last4"] == "WXYZ"
      and js["key_source"] == "file", str(js))
kp = os.path.join(os.environ["DATA_DIR"], "secrets", "jules.key")
check("file DATA_DIR/secrets/jules.key quyền 600", os.path.isfile(kp) and stat.S_IMODE(os.stat(kp).st_mode) == 0o600,
      oct(os.stat(kp).st_mode) if os.path.exists(kp) else "thiếu")
check("thư mục secrets quyền 700", stat.S_IMODE(os.stat(os.path.dirname(kp)).st_mode) == 0o700)
st, js = call("POST", "/api/jules/key", {"action": "delete"})
check("xóa key → configured:false, file mất", st == 200 and js["configured"] is False and not os.path.exists(kp), str(js))
os.environ["JULES_API_KEY"] = KEY
check("key từ env JULES_API_KEY được nhận", jules_worker.key_info() == {"configured": True, "key_source": "env", "key_last4": "WXYZ"})
os.environ.pop("JULES_API_KEY")
st, js = call("POST", "/api/jules/key", {"key": KEY})
FAKE.expected_key = KEY
st, js = call("GET", "/api/jules/status?check=1")
srcs = {s["full_name"]: s for s in js.get("sources", [])}
check("status check=1 → connected, liệt kê repo Jules truy cập được", st == 200 and js["state"] == "connected"
      and f"{fake_jules.OWNER}/gen-workplace" in srcs, str(js))
check("repo ngoài allowlist được đánh dấu allowed:false", srcs.get(f"{fake_jules.OWNER}/other-repo", {}).get("allowed") is False
      and srcs[f"{fake_jules.OWNER}/gen-workplace"]["allowed"] is True, str(srcs))
check("request tới Jules có header X-Goog-Api-Key đúng", all(r["key"] == KEY for r in jules_requests()))

# ------------------------------------------------------------------ 3. allowlist
print("[3] Repo ngoài allowlist → từ chối, không tạo phiên")
n_before = len(creates())
st, js = call("POST", "/api/task/assign", {"todo_id": T1, "engine": "jules", "repo": "other-repo"})
check("repo other-repo → 403 repo_not_allowed", st == 403 and js.get("code") == "repo_not_allowed", f"{st} {js}")
st, js = call("POST", "/api/task/assign", {"todo_id": T1, "engine": "jules", "repo": f"{fake_jules.OWNER}/other-repo"})
check("owner/other-repo → 403", st == 403 and js.get("code") == "repo_not_allowed", f"{st} {js}")
check("không có POST /sessions nào", len(creates()) == n_before)
os.environ["GW_JULES_REPOS"] = ""
st, js = call("GET", "/api/jules/status")
check("allowlist rỗng → enabled:false", js["enabled"] is False and js["allowed_repos"] == [], str(js))
st, js = call("POST", "/api/task/assign", {"todo_id": T1, "engine": "jules"})
check("allowlist rỗng → assign bị từ chối", st == 403 and js.get("code") == "repo_not_allowed", f"{st} {js}")
os.environ.pop("GW_JULES_REPOS")

# ------------------------------------------------------------------ 4. luồng đầy đủ
print("[4] Tạo phiên → kế hoạch → người duyệt → PR → ghi về phiên")
st, js = call("POST", "/api/task/assign", {"todo_id": T1, "engine": "jules", "author": "Ryan (Owner)"})
check("assign → 200 assigned, có dispatch_id + jules_session_id", st == 200 and js.get("status") == "assigned"
      and js.get("dispatch_id") and js.get("jules_session_id"), f"{st} {js}")
D1 = js.get("dispatch_id")
body = creates()[-1]["body"]
check("POST /sessions: requirePlanApproval=true, automationMode=AUTO_CREATE_PR", body.get("requirePlanApproval") is True
      and body.get("automationMode") == "AUTO_CREATE_PR", str(body))
check("sourceContext.source = sources/github/<owner>/gen-workplace, startingBranch main",
      body["sourceContext"] == {"source": f"sources/github/{fake_jules.OWNER}/gen-workplace", "githubRepoContext": {"startingBranch": "main"}},
      str(body.get("sourceContext")))
check("prompt có mã task, tiêu đề, dặn không merge", T1 in body["prompt"] and "Việc Jules 0" in body["prompt"]
      and "Không merge" in body["prompt"], body["prompt"][:300])
row = db._get_dispatch_row(D1)
check("dispatch_log: kind/engine jules, session_id jules, running, ext_session_id", row["kind"] == "jules" and row["engine"] == "jules"
      and row["session_id"] == "jules" and row["status"] == "running" and row["ext_session_id"] == js["jules_session_id"], str(row))
check("task claim cho jules, in_progress", task_row(T1)["claimed_by"] == "jules" and task_row(T1)["status"] == "in_progress")
check("phiên của task có tin 'Đã giao … cho Jules'", f"Đã giao {T1} cho Jules" in conv_text())
st, js = call("POST", "/api/task/assign", {"todo_id": T1, "engine": "jules"})
check("giao lại task đang chạy → 409", st == 409, f"{st} {js}")
st, js = call("POST", "/api/jules/approve", {"dispatch_id": D1})
check("duyệt khi Jules chưa có kế hoạch → 409 not_awaiting_approval", st == 409 and js.get("code") == "not_awaiting_approval", f"{st} {js}")
check("duyệt sớm không gọi approvePlan", not jules_requests("POST", ":approvePlan"))
for _ in range(3):
    jules_worker.poll_once()
row = db._get_dispatch_row(D1)
check("sau poll: ext_state AWAITING_PLAN_APPROVAL, vẫn running", row["ext_state"] == "AWAITING_PLAN_APPROVAL" and row["status"] == "running", str(row))
txt = conv_text()
check("kế hoạch ghi về phiên của task (đủ 3 bước)", "CHỜ DUYỆT" in txt and "1. Đọc backend/main.py" in txt and "3. Mở pull request" in txt, txt[-600:])
check("kế hoạch chỉ ghi 1 lần dù poll nhiều lần", txt.count("Jules đề xuất kế hoạch") == 1)
check("poll KHÔNG tự duyệt kế hoạch", not jules_requests("POST", ":approvePlan"))
res = db.wait_worker_result(D1, timeout_sec=1)
check("wait_worker_result khi chờ duyệt → running (không bị đánh dấu quá hạn)", res["status"] == "running"
      and res["ext_state"] == "AWAITING_PLAN_APPROVAL", str(res))
st, js = call("GET", "/api/tasks")
t = next(x for x in js["tasks"] if x["id"] == T1)
check("/api/tasks: last_dispatch có engine jules + ext_state", t["last_dispatch"]["engine"] == "jules"
      and t["last_dispatch"]["ext_state"] == "AWAITING_PLAN_APPROVAL", str(t["last_dispatch"]))
st, js = call("POST", "/api/jules/approve", {"task_id": T1, "author": "Ryan (Owner)"})
check("người duyệt → 200 approved", st == 200 and js.get("status") == "approved", f"{st} {js}")
check("đúng 1 lệnh approvePlan tới Jules", len(jules_requests("POST", ":approvePlan")) == 1)
st, js = call("GET", f"/api/dispatch/wait?dispatch_id={D1}&timeout_sec=10")
check("/api/dispatch/wait → done kèm pr_url", st == 200 and js["status"] == "done" and "/pull/" in js.get("pr_url", ""), str(js))
PR1 = js.get("pr_url", "")
txt = conv_text()
check("PR ghi về phiên của task kèm link + bằng chứng gợi ý là URL PR", f"Jules đã mở PR #" in txt and PR1 in txt
      and f"Bằng chứng nghiệm thu gợi ý: {PR1}" in txt, txt[-700:])
check("tin PR nhắc Jules không merge, Boss/Claude merge", "Jules KHÔNG merge" in txt)
check("thiếu GITHUB_TOKEN → tin ghi rõ", "GITHUB_TOKEN" in txt)
check("PR chỉ ghi 1 lần", txt.count("Jules đã mở PR") == 1)
check("task sang review, nhả khóa jules (Boss/Claude kiểm PR rồi mới nghiệm thu)",
      task_row(T1)["status"] == "review" and task_row(T1)["claimed_by"] == "", str(task_row(T1)))
res = mcp_core.execute_tool("wait_worker_result", {"dispatch_id": D1, "timeout_sec": 1})
check("MCP wait_worker_result → done, pr_url", json.loads(res["content"][0]["text"]).get("pr_url") == PR1)
n_msgs = len(db.get_gen_messages(CONV))
db.report_dispatch_to_task(D1)
check("report_dispatch_to_task (luồng agy) không ghi thêm tin cho dòng Jules", len(db.get_gen_messages(CONV)) == n_msgs)

# ------------------------------------------------------------------ 5. 401 / 429
print("[5] 401 và 429 báo rõ")
jules_worker._STATUS_CACHE["value"] = None
FAKE.fail = {"GET /sources": 401}
st, js = call("GET", "/api/jules/status?check=1")
check("status check=1 khi key sai → state error, nhắc 401", js["state"] == "error" and "401" in js["error"]
      and js.get("error_code") == "unauthorized", str(js))
st, js = call("POST", "/api/task/assign", {"todo_id": T2, "engine": "jules"})
check("assign khi 401 → 502 unauthorized, thông báo 'từ chối API key'", st == 502 and js.get("code") == "unauthorized"
      and "401" in js["error"] and "key" in js["error"], f"{st} {js}")
check("dòng dispatch lỗi được đánh failed, task trả về todo", db._get_dispatch_row(js["dispatch_id"])["status"] == "failed"
      and task_row(T2)["status"] == "todo" and task_row(T2)["claimed_by"] == "", str(task_row(T2)))
FAKE.fail = {"POST /sessions": 429}
st, js = call("POST", "/api/task/assign", {"todo_id": T2, "engine": "jules"})
check("assign khi 429 → 429 rate_limited, thông báo giới hạn", st == 429 and js.get("code") == "rate_limited"
      and "429" in js["error"] and "giới hạn" in js["error"], f"{st} {js}")
FAKE.fail = {}
FAKE.expected_key = "khac-key-that-0000000000"
st, js = call("POST", "/api/task/assign", {"todo_id": T2, "engine": "jules"})
check("key không khớp phía Jules → unauthorized", st == 502 and js.get("code") == "unauthorized", f"{st} {js}")
FAKE.expected_key = KEY
jules_worker._STATUS_CACHE["value"] = None

# ------------------------------------------------------------------ 6. concurrent + hủy
print("[6] Giới hạn phiên đồng thời + hủy")
st, a = call("POST", "/api/task/assign", {"todo_id": T2, "engine": "jules"})
st2, b = call("POST", "/api/task/assign", {"todo_id": T3, "engine": "jules"})
check("2 phiên chạy song song được", st == 200 and st2 == 200, f"{a} {b}")
n_before = len(creates())
st, js = call("POST", "/api/task/assign", {"todo_id": T4, "engine": "jules"})
check("phiên thứ 3 → 429 too_many_sessions", st == 429 and js.get("code") == "too_many_sessions", f"{st} {js}")
check("không tạo phiên Jules thứ 3", len(creates()) == n_before)
check("task thứ 3 không bị claim", task_row(T4)["claimed_by"] in ("", None) and task_row(T4)["status"] == "todo")
st, js = call("GET", "/api/jules/status")
check("status running=2", js["running"] == 2, str(js))
sid_b = b["jules_session_id"]
st, js = call("POST", "/api/jules/cancel", {"dispatch_id": b["dispatch_id"], "author": "Ryan (Owner)"})
check("hủy → 200 cancelled", st == 200 and js.get("status") == "cancelled", f"{st} {js}")
check("hủy gọi DELETE /sessions/<id> tới Jules", any(r["path"].endswith(f"/sessions/{sid_b}") for r in jules_requests("DELETE")))
check("dòng dispatch cancelled, task về todo, nhả khóa", db._get_dispatch_row(b["dispatch_id"])["status"] == "cancelled"
      and task_row(T3)["status"] == "todo" and task_row(T3)["claimed_by"] == "", str(task_row(T3)))
check("phiên task ghi 'đã hủy phiên Jules'", "đã hủy phiên Jules" in conv_text())
st, js = call("POST", "/api/jules/cancel", {"dispatch_id": b["dispatch_id"]})
check("hủy lần 2 → 409 not_running", st == 409 and js.get("code") == "not_running", f"{st} {js}")
st, js = call("POST", "/api/task/assign", {"todo_id": T4, "engine": "jules"})
check("sau khi hủy, giao phiên mới được", st == 200 and js.get("status") == "assigned", f"{st} {js}")
st, js = call("POST", "/api/jules/cancel", {"task_id": T4})
st, js = call("POST", "/api/jules/cancel", {"task_id": T2})
check("hủy nốt các phiên thử", st == 200 and jules_worker.status()["running"] == 0, str(js))
st, js = call("POST", "/api/jules/approve", {"dispatch_id": 999999})
check("duyệt dispatch không tồn tại → 404", st == 404)

# ------------------------------------------------------------------ 7. key không lộ
print("[7] Key không lộ ở API, DB, log")
for p in ("/api/jules/status", "/api/jules/status?check=1", "/api/dispatch/log?limit=100", "/api/tasks", "/api/mcp/status",
          "/api/state", f"/api/dispatch/wait?dispatch_id={D1}&timeout_sec=0"):
    call("GET", p)
leaks = [r[:120] for r in RESPONSES if KEY in r]
check(f"không response nào ({len(RESPONSES)}) chứa key", not leaks, str(leaks[:2]))
check("mọi response đều có last4 hoặc không nhắc key đầy đủ", any('"key_last4": "WXYZ"' in r for r in RESPONSES))
blob = b""
for f in os.listdir(os.environ["DATA_DIR"]):
    fp = os.path.join(os.environ["DATA_DIR"], f)
    if os.path.isfile(fp):
        with open(fp, "rb") as fh:
            blob += fh.read()
check("key không nằm trong DB / file dữ liệu ngoài secrets/", KEY.encode() not in blob)
check("key không xuất hiện trong log stdout", KEY not in TEE.buf.getvalue())
try:
    jules_worker._call("GET", "/sessions/không-hợp-lệ!")
    leaked_err = ""
except jules_worker.JulesError as e:
    leaked_err = e.message
check("lỗi client không chứa key", KEY not in leaked_err)

# ------------------------------------------------------------------ 8. không có đường merge
print("[8] Không có đường code nào merge PR")
bad = []
for m, p in (("PUT", "/repos/o/r/pulls/1/merge"), ("POST", "/sessions/1:merge"), ("POST", "/sessions/1:sendMessage"),
             ("PATCH", "/sessions/1"), ("GET", "/../repos/o/r/pulls/1/merge")):
    try:
        jules_worker._call(m, p)
        bad.append(f"{m} {p}")
    except jules_worker.JulesError as e:
        if e.code != "not_allowed_call":
            bad.append(f"{m} {p}: {e.code}")
check("client từ chối mọi endpoint ngoài danh sách trắng (merge, PUT, PATCH, sendMessage)", not bad, str(bad))
check("danh sách trắng chỉ GET/POST/DELETE, không mẫu nào nhận đường dẫn 'merge'",
      {m for m, _ in jules_worker._ALLOWED_CALLS} <= {"GET", "POST", "DELETE"}
      and not any(rx.match("/sessions/1:merge") or rx.match("/merge") for _, rx in jules_worker._ALLOWED_CALLS))
VERIFIED = [("GET", r"^/v1alpha/sources(\?.*)?$"), ("POST", r"^/v1alpha/sessions$"), ("GET", r"^/v1alpha/sessions/\d+$"),
            ("GET", r"^/v1alpha/sessions/\d+/activities(\?.*)?$"), ("POST", r"^/v1alpha/sessions/\d+:approvePlan$"),
            ("DELETE", r"^/v1alpha/sessions/\d+$")]
odd = [f"{r['method']} {r['path']}" for r in jules_requests() if not any(r["method"] == m and re.match(rx, r["path"]) for m, rx in VERIFIED)]
check(f"mọi request tới Jules giả ({len(jules_requests())}) thuộc tập endpoint đã xác minh", not odd, str(odd))
check("không request nào có 'merge'", not any("merge" in r["path"].lower() for r in jules_requests()))
check("mọi POST /sessions: AUTO_CREATE_PR + requirePlanApproval", all(r["body"].get("automationMode") == "AUTO_CREATE_PR"
      and r["body"].get("requirePlanApproval") is True for r in creates()))
src = open(os.path.join(ROOT, "backend", "jules_worker.py"), encoding="utf-8").read()
tree = ast.parse(src)
imports = set()
for n in ast.walk(tree):
    if isinstance(n, ast.Import):
        imports |= {a.name.split(".")[0] for a in n.names}
    elif isinstance(n, ast.ImportFrom):
        imports.add((n.module or "").split(".")[0])
check("jules_worker không import subprocess / git / shell", not ({"subprocess", "shutil", "pty", "git"} & imports), str(imports))
urlopens = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "urlopen"]
check("jules_worker chỉ có 1 lệnh urlopen (trong _call có danh sách trắng)", len(urlopens) == 1)
consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
check("jules_worker không gọi GitHub API", not any("api.github.com" in c for c in consts))
check("automationMode duy nhất là AUTO_CREATE_PR", jules_worker.AUTOMATION_MODE == "AUTO_CREATE_PR"
      and not any(re.search(r"AUTO_MERGE|automerge|auto_merge", c, re.I) for c in consts))
MERGE_RX = [r"/pulls/[^\"'\s]*/merge", r"\bgh\s+pr\s+merge", r"merge_pull_request", r"enable_pr_auto_merge", r"auto[-_]?merge",
            r"\[\s*['\"]git['\"][^\]]*['\"]push['\"]", r"_git\(\s*\w+\s*,\s*['\"]push['\"]", r"['\"]git push"]
hits = []
for fn in os.listdir(os.path.join(ROOT, "backend")):
    if fn.endswith(".py"):
        text = open(os.path.join(ROOT, "backend", fn), encoding="utf-8").read()
        hits += [f"{fn}: {rx}" for rx in MERGE_RX if re.search(rx, text, re.I)]
check("backend không có lệnh merge PR / auto-merge / git push", not hits, str(hits))
st, js = call("POST", "/api/jules/merge", {"dispatch_id": D1})
check("không có endpoint /api/jules/merge", st == 404 or (js or {}).get("status") != "merged", f"{st} {js}")
check("MCP không có tool merge / duyệt kế hoạch Jules", not any("merge" in t["name"] or t["name"] in ("jules_approve", "approve_plan")
                                                             for t in mcp_core.TOOLS))
check("assign_to_jules khai báo trong TOOL_META, có tác dụng phụ", mcp_core.TOOL_META["assign_to_jules"]["read_only"] is False
      and "assign_to_jules" not in mcp_core.READ_ONLY_TOOLS)
n_sessions = len(creates())
jules_worker.poll_once()
check("thread poll không bao giờ tạo phiên mới", len(creates()) == n_sessions)
loop_calls = {getattr(n.func, "id", getattr(n.func, "attr", "")) for fnode in ast.walk(tree)
              if isinstance(fnode, ast.FunctionDef) and fnode.name in ("poll_once", "refresh_dispatch", "_refresh_locked", "start_poller")
              for n in ast.walk(fnode) if isinstance(n, ast.Call)}
check("poll / refresh không gọi assign_task hay approve_plan", not ({"assign_task", "approve_plan"} & loop_calls), str(loop_calls))

_redir.__exit__(None, None, None)
server.shutdown()
FAKE_SRV.shutdown()
print(f"\n{PASSED} ok, {FAILED} fail")
sys.exit(0 if FAILED == 0 else 1)
