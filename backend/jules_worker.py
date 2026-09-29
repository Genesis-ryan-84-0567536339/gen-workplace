#!/usr/bin/env python3
"""
Worker Google Jules — CHỈ MỞ PR (#43).

Jules nhận 1 task của gen-workplace, đề xuất kế hoạch, NGƯỜI duyệt kế hoạch, rồi Jules làm và tự mở PR
(automationMode AUTO_CREATE_PR). Module này KHÔNG BAO GIỜ merge PR, không bật chế độ tự merge, không push, không gọi GitHub:
Boss hoặc Claude điều phối kiểm và merge PR của Jules qua quy trình PR bình thường.

Ràng buộc an toàn:
- Mặc định TẮT: chỉ giao được khi có API key (env JULES_API_KEY hoặc DATA_DIR/secrets/jules.key, quyền 600) và repo nằm
  trong allowlist GW_JULES_REPOS (mặc định chỉ "gen-workplace").
- Chỉ giao khi có người bấm (UI) hoặc gọi API / MCP (assign_task). Thread poll chỉ ĐỌC trạng thái phiên đang mở,
  không bao giờ tạo phiên mới và không tự duyệt kế hoạch.
- Luôn gửi requirePlanApproval=true: Jules dừng ở AWAITING_PLAN_APPROVAL, kế hoạch được ghi vào phiên của task,
  chỉ approve_plan() (UI / REST, người bấm) mới cho Jules làm tiếp.
- Tối đa GW_JULES_MAX_CONCURRENT phiên chạy cùng lúc (mặc định 2); cancel() xóa phiên Jules (API không có lệnh hủy riêng).
- Key không bao giờ lộ: không log, không trả qua API (chỉ configured + 4 ký tự cuối), lỗi HTTP được lọc key trước khi trả.
- Client chỉ gọi đúng các endpoint trong _ALLOWED_CALLS (danh sách trắng); gọi khác → JulesError.

Schema API Jules v1alpha đã xác minh (29/09/2026). developers.google.com/jules/api và jules.google/docs bị proxy chặn,
nên đọc qua TRÍCH ĐOẠN kết quả tìm kiếm của 2 trang đó + mã nguồn SDK chính thức github.com/google-labs-code/jules-sdk
(packages/core/src: api.ts, client.ts, mappers.ts, session.ts, types.ts):
- Base URL https://jules.googleapis.com/v1alpha (client.ts), header X-Goog-Api-Key (api.ts).
- GET  /sources → {sources: [{name: "sources/github/<owner>/<repo>", id, githubRepo: {owner, repo, isPrivate,
       defaultBranch: {displayName}}}], nextPageToken}
- POST /sessions {prompt, title, sourceContext: {source, githubRepoContext: {startingBranch}}, requirePlanApproval,
       automationMode: "AUTO_CREATE_PR"} → Session {name, id, title, state, url, outputs, ...}
- GET  /sessions/{id}; state ∈ QUEUED, PLANNING, AWAITING_PLAN_APPROVAL, AWAITING_USER_FEEDBACK, IN_PROGRESS, PAUSED,
       COMPLETED, FAILED (mappers.ts mapRestStateToSdkState); PR ở outputs[].pullRequest.url (mappers.ts, types.ts PullRequest).
- GET  /sessions/{id}/activities → {activities: [{name, createTime, originator, planGenerated: {plan: {id, steps: [{id, title,
       description, index}]}} | planApproved | progressUpdated {title, description} | agentMessaged {agentMessage} |
       sessionCompleted | sessionFailed {reason}}], nextPageToken}
- POST /sessions/{id}:approvePlan {} ; POST /sessions/{id}:sendMessage {prompt} (không dùng ở đây)
- DELETE /sessions/{id} (session.ts) — API KHÔNG có lệnh hủy / lưu trữ riêng, nên "Hủy" = xóa phiên.
Mọi chỗ đọc dữ liệu trả về nằm trong các hàm parse_* bên dưới để sửa một chỗ nếu schema đổi.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from backend import db
except ImportError:
    import db

DEFAULT_BASE_URL = "https://jules.googleapis.com/v1alpha"
ENGINE = "jules"
WORKER_ID = "jules"                 # session_id của dòng dispatch_log và người giữ (claimed_by) task
KEY_ENV = "JULES_API_KEY"
KEY_RE = re.compile(r"^[A-Za-z0-9_\-.]{16,256}$")
AUTOMATION_MODE = "AUTO_CREATE_PR"  # Jules tự mở PR; KHÔNG có chế độ merge nào được dùng

STATE_AWAITING_PLAN = "AWAITING_PLAN_APPROVAL"
STATE_FEEDBACK = "AWAITING_USER_FEEDBACK"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"
STATE_CANCELLED = "CANCELLED"       # trạng thái nội bộ khi người hủy (xóa phiên Jules)

# Danh sách trắng endpoint được gọi. Không có endpoint merge / push nào (Jules API cũng không có).
_SESSION_ID = r"[A-Za-z0-9_\-]{1,128}"
_ALLOWED_CALLS = [
    ("GET", re.compile(r"^/sources(\?.*)?$")),
    ("POST", re.compile(r"^/sessions$")),
    ("GET", re.compile(rf"^/sessions/{_SESSION_ID}$")),
    ("GET", re.compile(rf"^/sessions/{_SESSION_ID}/activities(\?.*)?$")),
    ("POST", re.compile(rf"^/sessions/{_SESSION_ID}:approvePlan$")),
    ("DELETE", re.compile(rf"^/sessions/{_SESSION_ID}$")),
]

_ASSIGN_LOCK = threading.Lock()
_REFRESH_LOCK = threading.Lock()    # thread poll và wait_worker_result không cùng làm mới (tránh ghi kế hoạch 2 lần)
_LAST_REFRESH = {}                  # dispatch_id → time.time() lần làm mới gần nhất (hạn chế gọi API khi wait)
_STATUS_CACHE = {"at": 0.0, "value": None}
_POLLER = {"thread": None}


class JulesError(Exception):
    """Lỗi gọi Jules: code ∈ not_configured | unauthorized | forbidden | rate_limited | not_found | http_error | network |
    bad_response | not_allowed_call. http = mã HTTP nên trả cho người gọi app."""

    def __init__(self, code, message, http=502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http

    def as_dict(self):
        return {"error": self.message, "code": self.code}


# ---------------------------------------------------------------- cấu hình

def base_url():
    return (os.environ.get("GW_JULES_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _env_int(name, default, lo, hi):
    try:
        v = int(os.environ.get(name, "") or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def request_timeout():
    try:
        return max(1.0, min(30.0, float(os.environ.get("GW_JULES_TIMEOUT_SEC", "10") or 10)))
    except ValueError:
        return 10.0


def max_concurrent():
    return _env_int("GW_JULES_MAX_CONCURRENT", 2, 1, 20)


def poll_interval():
    """Chu kỳ thread poll: 30–60 giây (mặc định 45)."""
    return _env_int("GW_JULES_POLL_SEC", 45, 30, 60)


def wait_refresh_sec():
    """wait_worker_result làm mới phiên Jules tối đa 1 lần / N giây (mặc định 15; 0 = mỗi lần, dùng cho test)."""
    return _env_int("GW_JULES_WAIT_REFRESH_SEC", 15, 0, 300)


def allowed_repos():
    """Allowlist repo (GW_JULES_REPOS, phân tách bằng dấu phẩy): 'repo' hoặc 'owner/repo'. Mặc định chỉ gen-workplace."""
    raw = os.environ.get("GW_JULES_REPOS")
    if raw is None:
        raw = "gen-workplace"
    return [r.strip() for r in raw.split(",") if r.strip()]


def repo_allowed(repo):
    """repo ('name' hoặc 'owner/name') có trong allowlist? So không phân biệt hoa thường; mục chỉ có tên khớp mọi owner."""
    repo = (repo or "").strip().strip("/").lower()
    if not repo or not re.match(r"^[a-z0-9_.\-]+(/[a-z0-9_.\-]+)?$", repo):
        return False
    name = repo.split("/")[-1]
    for a in allowed_repos():
        a = a.lower().strip("/")
        if a == repo or ("/" not in a and a == name):
            return True
    return False


# ---------------------------------------------------------------- key (không bao giờ trả ra ngoài)

def _key_path():
    return Path(db.DATA_DIR) / "secrets" / "jules.key"


def _read_key():
    """(key, source) — source ∈ env | file | ''."""
    env = (os.environ.get(KEY_ENV) or "").strip()
    if env:
        return env, "env"
    try:
        p = _key_path()
        if p.is_file():
            k = p.read_text(encoding="utf-8").strip()
            if k:
                return k, "file"
    except OSError:
        pass
    return "", ""


def key_info():
    """Chỉ trả configured, nguồn và 4 ký tự cuối — không bao giờ trả key."""
    k, src = _read_key()
    return {"configured": bool(k), "key_source": src, "key_last4": k[-4:] if k else ""}


def save_key(key):
    key = (key or "").strip() if isinstance(key, str) else ""
    if not KEY_RE.match(key):
        return {"error": "Key không hợp lệ (16–256 ký tự chữ, số, '_', '-', '.')", "code": "bad_request"}
    p = _key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    tmp = p.with_name(".jules.key.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    _STATUS_CACHE["value"] = None
    res = {"status": "saved", **key_info()}
    if res["key_source"] == "env":
        res["note"] = f"Đang dùng key từ biến môi trường {KEY_ENV}; key vừa lưu chỉ dùng khi bỏ biến đó."
    return res


def delete_key():
    p = _key_path()
    existed = p.is_file()
    try:
        if existed:
            p.unlink()
    except OSError as e:
        return {"error": f"Không xóa được file key ({type(e).__name__})", "code": "io_error"}
    _STATUS_CACHE["value"] = None
    res = {"status": "deleted" if existed else "not_found", **key_info()}
    if res["key_source"] == "env":
        res["note"] = f"Vẫn còn key trong biến môi trường {KEY_ENV}; bỏ biến đó rồi khởi động lại app để tắt hẳn."
    return res


# ---------------------------------------------------------------- client HTTP

def _scrub(text, key):
    text = str(text or "")
    if key:
        text = text.replace(key, "***")
    return text[:300]


def _call(method, path, body=None):
    """Gọi Jules API. Chỉ endpoint trong _ALLOWED_CALLS. Lỗi → JulesError có thông báo rõ (không chứa key)."""
    if not any(m == method and rx.match(path) for m, rx in _ALLOWED_CALLS):
        raise JulesError("not_allowed_call", f"Không cho phép gọi {method} {path.split('?')[0]} tới Jules", 500)
    key, _ = _read_key()
    if not key:
        raise JulesError("not_configured", "Chưa cấu hình Jules API key (MCP & Kết nối → Jules)", 400)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method,
                                 headers={"X-Goog-Api-Key": key, "Content-Type": "application/json",
                                          "Accept": "application/json", "User-Agent": "gen-workplace-jules"})
    try:
        with urllib.request.urlopen(req, timeout=request_timeout()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            b = json.loads(e.read().decode("utf-8", errors="replace") or "{}")
            detail = (b.get("error") or {}).get("message", "") if isinstance(b.get("error"), dict) else str(b.get("error") or "")
        except Exception:
            pass
        detail = _scrub(detail, key)
        tail = f": {detail}" if detail else ""
        if e.code == 401:
            raise JulesError("unauthorized", f"Jules từ chối API key (HTTP 401{tail}) — key sai hoặc đã bị thu hồi, tạo key mới ở jules.google → Settings", 502)
        if e.code == 403:
            raise JulesError("forbidden", f"Jules không cho phép thao tác này (HTTP 403{tail}) — key thiếu quyền hoặc repo chưa cài GitHub App Jules", 502)
        if e.code == 429:
            raise JulesError("rate_limited", f"Jules báo vượt giới hạn (HTTP 429{tail}) — hết lượt task trong 24 giờ hoặc gọi quá nhanh, thử lại sau", 429)
        if e.code == 404:
            raise JulesError("not_found", f"Jules không tìm thấy {path.split('?')[0]} (HTTP 404{tail})", 404)
        raise JulesError("http_error", f"Jules lỗi HTTP {e.code}{tail}", 502)
    except Exception as e:
        reason = _scrub(getattr(e, "reason", None) or e, key)
        raise JulesError("network", f"Không gọi được Jules API ({type(e).__name__}: {reason})", 502)
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        raise JulesError("bad_response", "Jules trả dữ liệu không phải JSON", 502)


# ---------------------------------------------------------------- parse (schema đã xác minh, xem docstring đầu file)

def parse_sources(resp):
    """GET /sources → [{name, full_name, owner, repo, default_branch, is_private}]."""
    out = []
    for s in (resp or {}).get("sources") or []:
        if not isinstance(s, dict):
            continue
        gh = s.get("githubRepo") or {}
        owner, repo = str(gh.get("owner") or ""), str(gh.get("repo") or "")
        if not repo:  # nguồn không phải GitHub
            continue
        db_ = gh.get("defaultBranch") or {}
        out.append({"name": str(s.get("name") or ""), "owner": owner, "repo": repo,
                    "full_name": f"{owner}/{repo}" if owner else repo,
                    "default_branch": str(db_.get("displayName") or "") if isinstance(db_, dict) else str(db_ or ""),
                    "is_private": bool(gh.get("isPrivate"))})
    return out


def parse_session(resp):
    """Session → {id, name, state, url, title, pr_url, pr_title}. PR: outputs[].pullRequest.url."""
    resp = resp if isinstance(resp, dict) else {}
    name = str(resp.get("name") or "")
    sid = str(resp.get("id") or (name.split("/", 1)[1] if name.startswith("sessions/") else ""))
    pr_url, pr_title = "", ""
    for o in resp.get("outputs") or []:
        pr = o.get("pullRequest") if isinstance(o, dict) else None
        if isinstance(pr, dict) and pr.get("url"):
            pr_url, pr_title = str(pr["url"]), str(pr.get("title") or "")
            break
    return {"id": sid, "name": name, "state": str(resp.get("state") or ""), "url": str(resp.get("url") or ""),
            "title": str(resp.get("title") or ""), "pr_url": pr_url, "pr_title": pr_title}


def parse_activities(resp):
    """→ {plan: {id, steps: [(index, title, description)]} | None (kế hoạch MỚI NHẤT), failed_reason, last_agent_message}."""
    plan, failed, agent_msg = None, "", ""
    for a in (resp or {}).get("activities") or []:
        if not isinstance(a, dict):
            continue
        pg = a.get("planGenerated")
        if isinstance(pg, dict) and isinstance(pg.get("plan"), dict):
            p = pg["plan"]
            steps = []
            for st in p.get("steps") or []:
                if isinstance(st, dict):
                    steps.append((st.get("index"), str(st.get("title") or ""), str(st.get("description") or "")))
            steps.sort(key=lambda x: (x[0] is None, x[0] if isinstance(x[0], int) else 0))
            plan = {"id": str(p.get("id") or a.get("name") or ""), "steps": steps}
        sf = a.get("sessionFailed")
        if isinstance(sf, dict):
            failed = str(sf.get("reason") or "")
        am = a.get("agentMessaged")
        if isinstance(am, dict) and am.get("agentMessage"):
            agent_msg = str(am["agentMessage"])
    return {"plan": plan, "failed_reason": failed, "last_agent_message": agent_msg}


def pr_number(pr_url):
    m = re.search(r"/pull/(\d+)", pr_url or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- API mức cao

def list_sources():
    out, token = [], ""
    for _ in range(5):
        q = "?pageSize=100" + (f"&pageToken={urllib.parse.quote(token)}" if token else "")
        resp = _call("GET", "/sources" + q)
        out.extend(parse_sources(resp))
        token = str((resp or {}).get("nextPageToken") or "")
        if not token:
            break
    return out


def _find_source(repo, sources):
    repo = repo.strip().strip("/").lower()
    for s in sources:
        if s["full_name"].lower() == repo or ("/" not in repo and s["repo"].lower() == repo):
            return s
    return None


def _running_rows():
    with db.get_connection() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM dispatch_log WHERE kind = ? AND status = 'running' ORDER BY id", (ENGINE,)).fetchall()]


def status(check=False):
    """Trạng thái cho UI / API. check=True: gọi GET /sources để kiểm key (cache 60s). Không bao giờ chứa key."""
    info = key_info()
    running = _running_rows()
    res = {"engine": ENGINE, **info, "enabled": info["configured"] and bool(allowed_repos()),
           "allowed_repos": allowed_repos(), "max_concurrent": max_concurrent(), "running": len(running),
           "poll_interval_sec": poll_interval(), "require_plan_approval": True, "automation_mode": AUTOMATION_MODE,
           "base_url_default": base_url() == DEFAULT_BASE_URL,
           "state": "no_key" if not info["configured"] else "unchecked", "error": "", "sources": [],
           "github_token_for_pr_check": bool((os.environ.get("GITHUB_TOKEN") or "").strip())}
    if not info["configured"] or not check:
        return res
    cached = _STATUS_CACHE["value"]
    if cached and time.time() - _STATUS_CACHE["at"] < 60 and cached.get("last4") == info["key_last4"]:
        res.update(cached["data"])
        return res
    try:
        srcs = list_sources()
        data = {"state": "connected", "error": "",
                "sources": [dict(s, allowed=repo_allowed(s["full_name"])) for s in srcs]}
    except JulesError as e:
        data = {"state": "error", "error": e.message, "error_code": e.code, "sources": []}
    _STATUS_CACHE.update(at=time.time(), value={"last4": info["key_last4"], "data": data})
    res.update(data)
    return res


def build_prompt(task):
    lines = [f"Task {task['id']}" + (f" ({task['viec_ref']})" if task.get("viec_ref") else "") + f": {task['title']}"]
    if task.get("description"):
        lines.append("")
        lines.append(task["description"][:3000])
    chk = [c for c in task.get("checklist") or [] if isinstance(c, dict)]
    if chk:
        lines.append("")
        lines.append("Checklist:")
        lines.extend(f"- [{'x' if c.get('done') else ' '}] {c.get('text', '')}" for c in chk[:30])
    lines += ["", "Quy tắc:",
              "- Chỉ mở 1 pull request cho thay đổi này. Không merge, không push lên nhánh main.",
              "- Viết mô tả PR bằng tiếng Việt, nêu rõ đã kiểm những gì.",
              f"- Ghi mã {task['id']} trong tiêu đề PR."]
    return "\n".join(lines)


def assign_task(todo_id, repo="", branch="", author="Ryan (Owner)", project_id="PRJ-GEN-WORKPLACE"):
    """
    Giao 1 task cho Jules (chỉ khi người bấm / gọi API / MCP). Trả {status: assigned, dispatch_id, jules_session_id, ...}
    hoặc {error, code, http}. Thứ tự kiểm: key → task → allowlist repo → giới hạn concurrent → nguồn Jules → tạo phiên.
    """
    project_id = db.normalize_project_id(project_id)
    todo_id = (todo_id or "").strip() if isinstance(todo_id, str) else ""
    if not key_info()["configured"]:
        return {"error": "Jules chưa bật: chưa có API key (MCP & Kết nối → Jules)", "code": "not_configured", "http": 400}
    if not todo_id:
        return {"error": "Thiếu todo_id (vd TSK-12)", "code": "bad_request", "http": 400}
    task = db._task_detail(todo_id, project_id)
    if not task:
        return {"error": "Task not found", "code": "not_found", "task_id": todo_id, "http": 404}
    if task["status"] == "done":
        return {"error": f"Task {todo_id} đã done — không giao lại", "code": "already_done", "task_id": todo_id, "http": 409}
    repo = (repo or "").strip() or (allowed_repos()[0] if allowed_repos() else "")
    if not repo_allowed(repo):
        return {"error": f"Repo '{repo}' không nằm trong allowlist GW_JULES_REPOS ({', '.join(allowed_repos()) or 'rỗng'})",
                "code": "repo_not_allowed", "http": 403}
    branch = (branch or "").strip()
    if branch and not re.match(r"^[A-Za-z0-9._/\-]{1,200}$", branch):
        return {"error": "Tên nhánh không hợp lệ", "code": "bad_request", "http": 400}

    with _ASSIGN_LOCK:
        running = _running_rows()
        if any((r.get("task_id") or "") == todo_id for r in running):
            return {"error": f"{todo_id} đang có phiên Jules chạy", "code": "already_running", "task_id": todo_id, "http": 409}
        if len(running) >= max_concurrent():
            return {"error": f"Đã có {len(running)} phiên Jules đang chạy (tối đa GW_JULES_MAX_CONCURRENT={max_concurrent()}); "
                             "chờ xong hoặc hủy bớt", "code": "too_many_sessions", "http": 429}
        holder, prev_status = (task.get("holder") or "").strip(), task.get("status") or "todo"
        claim = db.claim_task(WORKER_ID, todo_id, project_id)
        if "error" in claim:
            claim.setdefault("code", "locked")
            claim["http"] = 404 if claim.get("error") == "Task not found" else 409
            return claim
        did = db.start_dispatch_log(WORKER_ID, kind=ENGINE, channel_id=ENGINE, task_id=todo_id,
                                    command=f"jules: tạo phiên cho {todo_id} ({repo})", viec_ref=task.get("viec_ref") or "")
        with db.get_connection() as conn:
            conn.execute("UPDATE dispatch_log SET engine = ? WHERE id = ?", (ENGINE, did))
            conn.commit()

    try:
        source = _find_source(repo, list_sources())
        if not source:
            raise JulesError("source_not_connected",
                             f"Repo '{repo}' chưa kết nối với Jules — cài GitHub App Jules và chọn repo này ở jules.google", 409)
        if not repo_allowed(source["full_name"]):
            raise JulesError("repo_not_allowed", f"Repo '{source['full_name']}' không nằm trong allowlist GW_JULES_REPOS", 403)
        start_branch = branch or source["default_branch"] or "main"
        body = {"prompt": build_prompt(task), "title": f"{todo_id}: {task['title']}"[:200],
                "sourceContext": {"source": source["name"], "githubRepoContext": {"startingBranch": start_branch}},
                "requirePlanApproval": True, "automationMode": AUTOMATION_MODE}
        sess = parse_session(_call("POST", "/sessions", body))
        if not sess["id"]:
            raise JulesError("bad_response", "Jules không trả id phiên", 502)
    except JulesError as e:
        _finish(did, "failed", f"Không tạo được phiên Jules: {e.message}", ext_state=STATE_FAILED)
        _release_task(todo_id, project_id, back_to=prev_status, restore_holder=holder)
        return {**e.as_dict(), "dispatch_id": did, "task_id": todo_id, "http": e.http}

    with db.get_connection() as conn:
        conn.execute("""UPDATE dispatch_log SET ext_session_id = ?, ext_state = ?, ext_url = ?, command = ?, summary = ?
                        WHERE id = ?""",
                     (sess["id"], sess["state"] or "QUEUED", sess["url"],
                      f"jules: sessions/{sess['id']} · {source['full_name']}@{start_branch} · requirePlanApproval=true · {AUTOMATION_MODE}",
                      "Jules đang lập kế hoạch", did))
        conn.commit()
    _log_to_task(task, f"Đã giao {todo_id} cho Jules (dispatch:{did}, phiên Jules {sess['id']}) · repo {source['full_name']} "
                       f"nhánh {start_branch} · người giao: {author}.\nJules sẽ đề xuất kế hoạch; kế hoạch hiện ở đây và phải được "
                       "người duyệt (nút Duyệt trên thẻ task) thì Jules mới làm. Jules chỉ mở PR, không merge."
                       + (f"\nPhiên Jules: {sess['url']}" if sess["url"] else ""))
    db._notify_dispatch_change()
    return {"status": "assigned", "engine": ENGINE, "task_id": todo_id, "dispatch_id": did, "session_id": WORKER_ID,
            "jules_session_id": sess["id"], "jules_state": sess["state"] or "QUEUED", "jules_url": sess["url"],
            "repo": source["full_name"], "branch": start_branch, "require_plan_approval": True,
            "viec_ref": task.get("viec_ref") or ""}


def _finish(did, st, summary, ext_state=None, pr_url=None):
    with db.get_connection() as conn:
        conn.execute(f"""UPDATE dispatch_log SET status = ?, finished_at = ?, summary = ?,
                         exit_code = ?{', ext_state = ?' if ext_state is not None else ''}{', pr_url = ?' if pr_url is not None else ''}
                         WHERE id = ? AND status = 'running'""",
                     [st, time.strftime("%Y-%m-%d %H:%M:%S"), db._shorten_output(summary), 0 if st == "done" else 1]
                     + ([ext_state] if ext_state is not None else []) + ([pr_url] if pr_url is not None else []) + [did])
        conn.commit()
    db._notify_dispatch_change()


def _release_task(todo_id, project_id, back_to="todo", restore_holder=""):
    """Nhả khóa 'jules' trên task (không đụng task người khác đang giữ). back_to: trạng thái mới cho task Kanban phiên."""
    with db.get_connection() as conn:
        conn.execute("""UPDATE gen_session_todos SET status = ?, claimed_by = ?, assigned_agent = CASE WHEN ? = '' THEN assigned_agent ELSE ? END,
                        locked_at = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND project_id = ? AND claimed_by = ? AND status != 'done'""",
                     (back_to, restore_holder, restore_holder, restore_holder, todo_id, project_id, WORKER_ID))
        conn.execute("""UPDATE todos SET assigned_session_id = ?, locked_at = NULL
                        WHERE id = ? AND project_id = ? AND assigned_session_id = ? AND status != 'done'""",
                     (restore_holder, todo_id, project_id, WORKER_ID))
        conn.commit()


def _log_to_task(task, content):
    conv = (task or {}).get("conversation_id") or ""
    if not conv:
        return None
    res = db.log_gen_message(conv, content[:db.LOG_MESSAGE_MAX_LEN], "assistant", "Jules (Google)")
    return res.get("message_id")


def _row_for(dispatch_id=None, task_id=""):
    did = db._resolve_dispatch_id(dispatch_id, (task_id or "").strip(), "")
    row = db._get_dispatch_row(did) if did is not None else None
    if not row or row.get("kind") != ENGINE:
        return None
    return row


def format_plan(plan):
    lines = []
    for i, (idx, title, desc) in enumerate(plan["steps"], 1):
        n = idx + 1 if isinstance(idx, int) else i
        lines.append(f"{n}. {title}" + (f" — {desc[:400]}" if desc else ""))
    return "\n".join(lines) or "(kế hoạch rỗng)"


def refresh_dispatch(dispatch_id, project_id="PRJ-GEN-WORKPLACE"):
    """
    Đọc trạng thái 1 phiên Jules đang chạy và cập nhật dispatch_log + phiên của task. CHỈ ĐỌC phía Jules
    (GET session / activities); không bao giờ duyệt kế hoạch hay tạo phiên. Trả status dispatch sau khi làm mới.
    """
    with _REFRESH_LOCK:
        return _refresh_locked(dispatch_id, project_id)


def _refresh_locked(dispatch_id, project_id):
    row = db._get_dispatch_row(dispatch_id)
    if not row or row.get("kind") != ENGINE:
        return None
    if db._row_status(row) != "running" or not row.get("ext_session_id"):
        return db._row_status(row)
    _LAST_REFRESH[row["id"]] = time.time()
    sess = parse_session(_call("GET", f"/sessions/{row['ext_session_id']}"))
    state = sess["state"] or row.get("ext_state") or ""
    task = db._task_detail(row.get("task_id") or "", project_id)
    acts = None
    if state in (STATE_AWAITING_PLAN, STATE_FAILED, STATE_FEEDBACK):
        acts = parse_activities(_call("GET", f"/sessions/{row['ext_session_id']}/activities?pageSize=100"))

    updates = {"ext_state": state}
    if sess["url"] and not row.get("ext_url"):
        updates["ext_url"] = sess["url"]
    # Kế hoạch mới → ghi vào phiên của task (1 lần mỗi kế hoạch), chờ người duyệt
    if state == STATE_AWAITING_PLAN and acts and acts["plan"] and acts["plan"]["id"] != (row.get("ext_plan_id") or ""):
        updates["ext_plan_id"] = acts["plan"]["id"]
        updates["summary"] = "Chờ duyệt kế hoạch"
        _log_to_task(task, f"Jules đề xuất kế hoạch cho {row.get('task_id')} (dispatch:{row['id']}) — CHỜ DUYỆT:\n"
                           f"{format_plan(acts['plan'])}\n"
                           "Duyệt: nút Duyệt trên thẻ task hoặc POST /api/jules/approve {\"dispatch_id\": " + str(row["id"]) + "}. "
                           "Không duyệt thì bấm Hủy.")
    elif state == STATE_FEEDBACK and state != row.get("ext_state"):
        msg = (acts or {}).get("last_agent_message") or ""
        _log_to_task(task, f"Jules đang chờ phản hồi (dispatch:{row['id']}): {msg[:1500] or '(không có nội dung)'}"
                           + (f"\nTrả lời trong phiên Jules: {sess['url'] or row.get('ext_url')}" if (sess["url"] or row.get("ext_url")) else ""))
        updates["summary"] = "Jules chờ phản hồi"
    elif state not in (STATE_AWAITING_PLAN, STATE_FEEDBACK, STATE_COMPLETED, STATE_FAILED):
        updates["summary"] = "Jules đang làm…" if state in ("IN_PROGRESS",) else f"Jules: {state or 'đang chờ'}"

    new_pr = sess["pr_url"] and sess["pr_url"] != (row.get("pr_url") or "")
    if sess["pr_url"]:
        updates["pr_url"] = sess["pr_url"]
    sets = ", ".join(f"{k} = ?" for k in updates)
    with db.get_connection() as conn:
        conn.execute(f"UPDATE dispatch_log SET {sets} WHERE id = ? AND status = 'running'", list(updates.values()) + [row["id"]])
        conn.commit()

    if new_pr:
        n = pr_number(sess["pr_url"])
        note = "" if (os.environ.get("GITHUB_TOKEN") or "").strip() else \
            " (repo private: app cần GITHUB_TOKEN để kiểm URL PR khi nghiệm thu; chưa có thì kiểm tay rồi đóng bằng SHA merge)"
        _log_to_task(task, f"Jules đã mở PR{f' #{n}' if n else ''}: {sess['pr_url']}"
                           + (f" — {sess['pr_title']}" if sess["pr_title"] else "")
                           + f"\nJules KHÔNG merge. Boss hoặc Claude điều phối kiểm và merge PR theo quy trình bình thường, "
                             f"xong mới nghiệm thu {row.get('task_id')}.\nBằng chứng nghiệm thu gợi ý: {sess['pr_url']}{note}")

    if state == STATE_COMPLETED:
        if sess["pr_url"]:
            _finish(row["id"], "done", f"Đã mở PR: {sess['pr_url']}", ext_state=state, pr_url=sess["pr_url"])
            _release_task(row.get("task_id") or "", project_id, back_to="review")
        else:
            _finish(row["id"], "failed", "Jules báo xong nhưng không có PR trong outputs", ext_state=state)
            _release_task(row.get("task_id") or "", project_id, back_to="todo")
            _log_to_task(task, f"Jules báo xong dispatch:{row['id']} nhưng KHÔNG mở PR — kiểm phiên Jules {sess['url'] or row.get('ext_url')}")
        _mark_reported(row["id"])
    elif state == STATE_FAILED:
        reason = (acts or {}).get("failed_reason") or "không rõ lý do"
        _finish(row["id"], "failed", f"Jules thất bại: {reason}", ext_state=state)
        _release_task(row.get("task_id") or "", project_id, back_to="todo")
        _log_to_task(task, f"Jules THẤT BẠI ở dispatch:{row['id']}: {reason[:1500]}")
        _mark_reported(row["id"])
    else:
        db._notify_dispatch_change()
    return db._row_status(db._get_dispatch_row(row["id"]) or row)


def _mark_reported(did):
    # report_dispatch_to_task (luồng agy) không ghi thêm tin cho dòng Jules
    with db.get_connection() as conn:
        conn.execute("UPDATE dispatch_log SET task_msg_id = -1 WHERE id = ? AND task_msg_id IS NULL", (did,))
        conn.commit()


def refresh_for_wait(dispatch_id):
    """Hook cho db.wait_worker_result: làm mới tối đa 1 lần / wait_refresh_sec() giây; lỗi API không làm hỏng wait."""
    if time.time() - _LAST_REFRESH.get(int(dispatch_id), 0) < wait_refresh_sec():
        return "running"
    _LAST_REFRESH[int(dispatch_id)] = time.time()
    try:
        return refresh_dispatch(dispatch_id) or "running"
    except JulesError as e:
        print(f"[jules] làm mới dispatch:{dispatch_id} lỗi: {e.code}")
        return "running"


db.DISPATCH_POLLERS[ENGINE] = refresh_for_wait


def approve_plan(dispatch_id=None, task_id="", author="Ryan (Owner)", project_id="PRJ-GEN-WORKPLACE"):
    """Người duyệt kế hoạch Jules đang chờ (AWAITING_PLAN_APPROVAL). Gọi POST /sessions/{id}:approvePlan."""
    row = _row_for(dispatch_id, task_id)
    if not row:
        return {"error": "Không tìm thấy lần giao Jules khớp dispatch_id / task_id", "code": "not_found", "http": 404}
    if db._row_status(row) != "running":
        return {"error": f"dispatch:{row['id']} đã kết thúc ({db._row_status(row)})", "code": "not_running", "http": 409}
    # Chỉ duyệt kế hoạch ĐÃ ghi vào phiên của task (người đã thấy); Jules đổi kế hoạch → phải xem lại
    shown_plan = row.get("ext_plan_id") or ""
    if row.get("ext_state") != STATE_AWAITING_PLAN or not shown_plan:
        return {"error": f"Chưa có kế hoạch chờ duyệt (trạng thái {row.get('ext_state') or '?'}) — đợi kế hoạch hiện trong phiên của task",
                "code": "not_awaiting_approval", "dispatch_id": row["id"], "http": 409}
    try:
        refresh_dispatch(row["id"], project_id)
        row = db._get_dispatch_row(row["id"]) or row
        if row.get("ext_state") != STATE_AWAITING_PLAN or (row.get("ext_plan_id") or "") != shown_plan:
            return {"error": f"Kế hoạch đã thay đổi hoặc phiên không còn chờ duyệt (trạng thái {row.get('ext_state') or '?'}) — xem lại phiên của task",
                    "code": "not_awaiting_approval", "dispatch_id": row["id"], "http": 409}
        _call("POST", f"/sessions/{row['ext_session_id']}:approvePlan", {})
    except JulesError as e:
        return {**e.as_dict(), "dispatch_id": row["id"], "http": e.http}
    with db.get_connection() as conn:
        conn.execute("UPDATE dispatch_log SET ext_state = 'IN_PROGRESS', summary = ? WHERE id = ? AND status = 'running'",
                     (f"Kế hoạch đã duyệt bởi {author}; Jules đang làm…", row["id"]))
        conn.commit()
    _log_to_task(db._task_detail(row.get("task_id") or "", project_id),
                 f"{author} đã duyệt kế hoạch Jules (dispatch:{row['id']}). Jules đang làm; xong sẽ mở PR và ghi link ở đây.")
    db._notify_dispatch_change()
    return {"status": "approved", "dispatch_id": row["id"], "task_id": row.get("task_id") or "",
            "jules_session_id": row["ext_session_id"], "approved_by": author}


def cancel(dispatch_id=None, task_id="", author="Ryan (Owner)", project_id="PRJ-GEN-WORKPLACE"):
    """Hủy phiên Jules đang chạy: DELETE /sessions/{id} (API không có lệnh hủy riêng). Phiên đã mất (404) vẫn tính là hủy."""
    row = _row_for(dispatch_id, task_id)
    if not row:
        return {"error": "Không tìm thấy lần giao Jules khớp dispatch_id / task_id", "code": "not_found", "http": 404}
    if db._row_status(row) != "running":
        return {"error": f"dispatch:{row['id']} đã kết thúc ({db._row_status(row)})", "code": "not_running", "http": 409}
    if row.get("ext_session_id"):
        try:
            _call("DELETE", f"/sessions/{row['ext_session_id']}")
        except JulesError as e:
            if e.code != "not_found":
                return {**e.as_dict(), "dispatch_id": row["id"], "http": e.http}
    with db.get_connection() as conn:
        conn.execute("""UPDATE dispatch_log SET status = 'cancelled', finished_at = ?, exit_code = 1, ext_state = ?, summary = ?
                        WHERE id = ? AND status = 'running'""",
                     (time.strftime("%Y-%m-%d %H:%M:%S"), STATE_CANCELLED, f"Đã hủy bởi {author}", row["id"]))
        conn.commit()
    _mark_reported(row["id"])
    _release_task(row.get("task_id") or "", project_id, back_to="todo")
    _log_to_task(db._task_detail(row.get("task_id") or "", project_id),
                 f"{author} đã hủy phiên Jules (dispatch:{row['id']}); phiên Jules đã bị xóa, task trả về Cần làm.")
    db._notify_dispatch_change()
    return {"status": "cancelled", "dispatch_id": row["id"], "task_id": row.get("task_id") or "", "cancelled_by": author}


def poll_once(project_id="PRJ-GEN-WORKPLACE"):
    """Làm mới mọi phiên Jules đang chạy (thread poll gọi). Chỉ đọc phía Jules. Trả {checked, errors}."""
    if not key_info()["configured"]:
        return {"checked": 0, "errors": 0, "skipped": "no_key"}
    checked = errors = 0
    for r in _running_rows():
        if not r.get("ext_session_id"):
            continue
        checked += 1
        try:
            refresh_dispatch(r["id"], project_id)
        except JulesError as e:
            errors += 1
            print(f"[jules] poll dispatch:{r['id']} lỗi: {e.code}")
            if e.code == "rate_limited":
                break
    return {"checked": checked, "errors": errors}


def start_poller():
    """Thread daemon poll mỗi poll_interval() giây (30–60). Không làm gì khi không có key hoặc không có phiên đang chạy."""
    if _POLLER["thread"] and _POLLER["thread"].is_alive():
        return _POLLER["thread"]

    def _loop():
        while True:
            time.sleep(poll_interval())
            try:
                poll_once()
            except Exception as e:
                print(f"[jules] poll lỗi: {type(e).__name__}")

    t = threading.Thread(target=_loop, daemon=True, name="JulesPoller")
    t.start()
    _POLLER["thread"] = t
    info = key_info()
    print(f"  Jules (chỉ mở PR): {'đã có key' if info['configured'] else 'TẮT (chưa có key)'} · "
          f"allowlist {', '.join(allowed_repos()) or 'rỗng'} · tối đa {max_concurrent()} phiên · poll {poll_interval()}s")
    return t
