#!/usr/bin/env python3
"""
Chế độ "Làm" (build) cho worker agy (Issue #45): agy được sửa code thật, nhưng chỉ trong worktree riêng của task.

Luồng 1 lần giao (POST /api/task/assign mode="build", mặc định của nút "Giao cho @vai" trên thẻ task):
  1. Worktree riêng <GW_WORKTREE_ROOT>/TSK-n trên nhánh wt/TSK-n, tạo từ origin/main mới nhất (git fetch trước).
     Giao lại cùng task → dùng lại worktree / nhánh đang có (làm tiếp trên kết quả cũ).
  2. Chọn hồ sơ còn quota (db.select_agy_profile_for_run, hết quota thì chuyển hồ sơ khác), ghi allow + deny chế độ Làm vào
     settings của hồ sơ (db.ensure_agy_build_permissions), chạy `agy --gemini_dir=<hồ sơ> -p <prompt> --output-format stream-json`
     trong worktree: chế độ MẶC ĐỊNH của agy (KHÔNG --mode plan), KHÔNG --model (agy dùng model mặc định), KHÔNG cờ bỏ
     hỏi quyền (skip-permissions). Xong thì gỡ các rule vừa thêm (db.release_agy_build_permissions).
  3. Lớp chặn thứ hai (không phụ thuộc allow-rule của agy), chỉ áp cho tiến trình agy qua biến môi trường git:
     core.hooksPath → hook pre-commit (chỉ commit được trên wt/TSK-n trong đúng worktree) + pre-push (chặn mọi push),
     remote.origin.pushurl → URL hỏng. Tiến trình git của app không mang các biến này.
  4. Sau khi agy xong, APP (không phải agy) làm tiếp: kiểm có commit mới trên wt/TSK-n và main / repo app không bị đổi;
     chạy py_compile + toàn bộ scripts/test_*.py (trừ GW_BUILD_TEST_EXCLUDE) trong worktree; push wt/TSK-n lên origin bằng
     credential git sẵn có (lỗi thì ghi rõ, không crash); ghi nhánh, SHA, kết quả test, link compare vào dispatch_log, tin
     war-room và phiên của task. Có GITHUB_TOKEN thì tạo PR NHÁP; không có thì chỉ ghi link compare. KHÔNG bao giờ merge.
  5. Task done (complete_task) hoặc bị xóa → `git worktree remove` worktree của task; nhánh (local + remote) giữ nguyên.
"""

import fnmatch
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

try:
    from backend import db
except ImportError:
    import db

KIND = "build"
TASK_ID_RE = re.compile(r"^TSK-[0-9]{1,9}$")
REMOTE = "origin"
DEFAULT_SOP = """# SOP chế độ "Làm" (build)
1. Hiểu yêu cầu: đọc tiêu đề, mô tả, checklist, viec_ref; đọc code liên quan trước khi sửa.
2. Sửa code: thay đổi nhỏ nhất đủ làm xong checklist, giữ phong cách code sẵn có.
3. Chạy test liên quan: python3 -m py_compile <file>; python3 scripts/test_<liên quan>.py.
4. Commit với message rõ ràng (git add + git commit). Không push.
5. Báo cáo ngắn: đã đổi gì, test nào pass, rủi ro."""
_GITHUB_URL_RE = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")
_CRED_RE = re.compile(r"(https?://)[^/@\s]+@")


REPO_KEY_RE = re.compile(r"^[a-z0-9-]{2,32}$")


def base_branch():
    return (os.environ.get("GW_BUILD_BASE") or "main").strip() or "main"


def repo_dir():
    return os.environ.get("GW_DISPATCH_REPO") or str(db.BASE_DIR)


def gw_repos_dir():
    return os.environ.get("GW_REPOS_ROOT") or os.path.abspath(os.path.join(str(db.BASE_DIR), "..", "gw-repos"))


SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/(?!\.\.?$)[A-Za-z0-9._-]+$")
BASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def _valid_base(b):
    return bool(BASE_RE.match(b or "")) and ".." not in b and not b.endswith("/") and not b.endswith(".lock")


def clone_url_for(slug):
    """URL clone dựng CHỈ từ slug trong cấu hình (GW_BUILD_CLONE_BASE ghi đè gốc, mặc định https://github.com — test / mirror)."""
    if not SLUG_RE.match(slug or ""):
        return ""
    root = (os.environ.get("GW_BUILD_CLONE_BASE") or "https://github.com").rstrip("/")
    return f"{root}/{slug}.git"


def _ensure_clone(entry):
    """Chưa có clone_dir/.git thì app tự `git clone -- <url từ slug> <clone_dir>`; lỗi ghi vào entry['clone_error']."""
    clone_dir = entry["clone_dir"]
    if os.path.exists(os.path.join(clone_dir, ".git")):
        return entry
    url = clone_url_for(entry.get("slug") or "")
    if not url:
        entry["clone_error"] = f"slug không hợp lệ: '{entry.get('slug')}'"
        return entry
    try:
        os.makedirs(os.path.dirname(clone_dir), exist_ok=True)
    except OSError as e:
        entry["clone_error"] = f"Không tạo được thư mục gw-repos: {e}"
        return entry
    r = _git(["clone", "--", url, clone_dir], cwd=os.path.dirname(clone_dir),
             timeout=_env_int("GW_BUILD_CLONE_TIMEOUT_SEC", 120))
    if r.returncode != 0:
        entry["clone_error"] = _err(r) or f"git clone thoát {r.returncode}"
        print(f"[build] Lỗi khi git clone repo {entry['key']} ({url}): {entry['clone_error']}")
    return entry


def build_repos(auto_clone=False):
    """
    Danh sách repo cho chế độ Làm (Issue #57):
    Đọc env GW_BUILD_REPOS (JSON list) hoặc <DATA_DIR>/config/build_repos.json.
    Luôn có mục mặc định key 'gen-workplace' = repo hiện tại (repo_dir cũ, slug từ origin, base main, kind python).
    Key phải khớp ^[a-z0-9-]{2,32}$, slug khớp SLUG_RE, base là tên nhánh hợp lệ, clone_dir phải nằm hẳn dưới gw-repos/
    (trừ gen-workplace). auto_clone=True: chưa có clone thì app tự git clone https://github.com/<slug>.git <clone_dir>
    (get_repo chỉ clone đúng repo được hỏi).
    """
    default_entry = {
        "key": "gen-workplace",
        "slug": github_repo_slug(repo_dir()),
        "clone_dir": repo_dir(),
        "base": base_branch(),
        "kind": "python",
        "test_cmd": "",
        "protected_paths": []
    }
    repos_map = {"gen-workplace": default_entry}
    order = ["gen-workplace"]

    raw_data = None
    if os.environ.get("GW_BUILD_REPOS"):
        try:
            raw_data = json.loads(os.environ["GW_BUILD_REPOS"])
        except Exception as e:
            print(f"[build] Không đọc được env GW_BUILD_REPOS: {e}")
    else:
        cfg_path = os.path.join(str(db.DATA_DIR), "config", "build_repos.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
            except Exception as e:
                print(f"[build] Không đọc được cấu hình {cfg_path}: {e}")

    if isinstance(raw_data, list):
        repos_root = gw_repos_dir()
        real_repos_root = os.path.realpath(repos_root)

        for item in raw_data:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not REPO_KEY_RE.match(key) or key.startswith("gw-") or key.startswith("tsk-"):
                print(f"[build] Bỏ qua repo cấu hình sai key: '{key}' (phải khớp ^[a-z0-9-]{{2,32}}$ và không bắt đầu bằng gw-/tsk-)")
                continue
            slug = str(item.get("slug") or "").strip()
            base = str(item.get("base") or "").strip()
            if (slug and not SLUG_RE.match(slug)) or (base and not _valid_base(base)):
                print(f"[build] Bỏ qua repo '{key}': slug '{slug}' hoặc base '{base}' không hợp lệ")
                continue
            protected = item.get("protected_paths") or []
            if not isinstance(protected, list):
                protected = []
            protected = [str(x) for x in protected if isinstance(x, str) and x.strip()]

            if key == "gen-workplace":
                if slug:
                    default_entry["slug"] = slug
                if base:
                    default_entry["base"] = base
                if item.get("kind"):
                    default_entry["kind"] = str(item["kind"]).strip()
                if "test_cmd" in item:
                    default_entry["test_cmd"] = str(item.get("test_cmd") or "").strip()
                if "protected_paths" in item:
                    default_entry["protected_paths"] = protected
                continue

            if not slug:
                print(f"[build] Bỏ qua repo '{key}': thiếu slug")
                continue
            base = base or "main"
            kind = str(item.get("kind") or "python").strip() or "python"
            test_cmd = str(item.get("test_cmd") or "").strip()

            specified_clone = str(item.get("clone_dir") or "").strip()
            clone_dir = os.path.abspath(os.path.join(repos_root, specified_clone or key))
            real_clone = os.path.realpath(clone_dir)
            if not real_clone.startswith(real_repos_root + os.sep):
                print(f"[build] Bỏ qua repo '{key}': clone_dir '{clone_dir}' không nằm dưới gw-repos/ ({repos_root})")
                continue

            entry = {
                "key": key,
                "slug": slug,
                "clone_dir": clone_dir,
                "base": base,
                "kind": kind,
                "test_cmd": test_cmd,
                "protected_paths": protected
            }
            if auto_clone:
                _ensure_clone(entry)

            repos_map[key] = entry
            if key not in order:
                order.append(key)

    return [repos_map[k] for k in order]


def get_repo(key="", auto_clone=True):
    """Lấy thông tin cấu hình repo theo key. Rỗng hoặc 'gen-workplace' -> repo mặc định. Chỉ clone đúng repo này."""
    k = (key or "").strip()
    if not k:
        k = "gen-workplace"
    for r in build_repos(auto_clone=False):
        if r["key"] == k:
            if auto_clone and k != "gen-workplace":
                _ensure_clone(r)
            return r
    return None


def _wt_confined(wt, repo_key=""):
    """realpath của worktree phải nằm hẳn dưới gw-worktrees/ (repo khác: dưới gw-worktrees/<key>/), chặn symlink thoát ra ngoài."""
    k = (repo_key or "").strip()
    root = os.path.realpath(db._worktree_root())
    if k and k != "gen-workplace":
        if not REPO_KEY_RE.match(k) or k.startswith("gw-") or k.startswith("tsk-"):
            return False
        root = os.path.join(root, k)
        if os.path.realpath(root) != root:
            return False
    return os.path.realpath(wt).startswith(root + os.sep)


def task_worktree_dir(task_id, repo_key=""):
    k = (repo_key or "").strip()
    if not k or k == "gen-workplace":
        return os.path.join(db._worktree_root(), task_id)
    return os.path.join(db._worktree_root(), k, task_id)


def task_branch(task_id):
    return f"wt/{task_id}"


def hooks_dir(task_id):
    """Hook git chặn commit sai nhánh / push của agy: nằm NGOÀI worktree (không bị commit, agy không ghi được)."""
    return os.path.join(str(db.DATA_DIR), "agy-build-hooks", task_id)


def _env_int(name, default):
    try:
        return max(1, int(os.environ.get(name, str(default)) or default))
    except ValueError:
        return default


def test_exclude():
    raw = os.environ.get("GW_BUILD_TEST_EXCLUDE", "test_mcp_suite.py")
    return {x.strip() for x in re.split(r"[,\s]+", raw) if x.strip()}


def _redact(text):
    return _CRED_RE.sub(r"\1***@", text or "")


def _git(args, cwd, timeout=60, env=None):
    cmd = ["git", "-c", "safe.directory=*"] + list(args)
    e = {**os.environ, "GIT_TERMINAL_PROMPT": "0"} if env is None else env
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=e)
    except (OSError, subprocess.TimeoutExpired) as ex:
        return subprocess.CompletedProcess(cmd, 1, "", str(ex))


def _out(res):
    return (res.stdout or "").strip() if res.returncode == 0 else ""


def _err(res):
    return _redact(((res.stderr or "") + "\n" + (res.stdout or "")).strip())[-600:]


# ---------------------------------------------------------------------------
# Worktree của task
# ---------------------------------------------------------------------------
def ensure_task_worktree(task_id, repo_key=""):
    """
    Worktree <root>/TSK-n (gen-workplace) hoặc <root>/<key>/TSK-n (repo khác) trên nhánh wt/TSK-n.
    Chưa có → git fetch origin <base> rồi `git worktree add --no-track -b wt/TSK-n <dir> origin/<base>`
    (không có origin/<base> thì <base> local, rồi HEAD, kèm ghi chú). Đã có → dùng lại (phải đúng nhánh).
    Trả {ok, dir, branch, base_ref, base_sha, reused, note, error}.
    """
    if not TASK_ID_RE.match(task_id or ""):
        return {"ok": False, "error": f"Mã task không hợp lệ cho worktree: '{task_id}'"}
    k = (repo_key or "").strip()
    repo_conf = get_repo(k, auto_clone=True)
    if not repo_conf:
        return {"ok": False, "error": f"Repo '{k}' không tồn tại trong cấu hình"}
    if repo_conf.get("clone_error"):
        return {"ok": False, "error": f"Không clone được repo '{k}': {repo_conf['clone_error']}"}

    repo = repo_conf["clone_dir"]
    if not os.path.exists(os.path.join(repo, ".git")):
        return {"ok": False, "error": f"Thư mục repo chưa được clone: {repo}"}

    base = repo_conf.get("base") or base_branch()
    wt = task_worktree_dir(task_id, k)
    branch = task_branch(task_id)
    if not _wt_confined(wt, k):
        return {"ok": False, "error": f"Đường dẫn worktree thoát khỏi gw-worktrees/: {wt}"}
    out = {"ok": False, "dir": wt, "branch": branch, "base_ref": "", "base_sha": "", "reused": False, "note": "", "error": ""}
    if os.path.exists(os.path.join(wt, ".git")):
        cur = _out(_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=wt))
        if cur != branch:
            out["error"] = f"Worktree {wt} đang ở nhánh '{cur or '(detached)'}', không phải {branch}"
            return out
        out.update(ok=True, reused=True, base_sha=_out(_git(["rev-parse", "HEAD"], cwd=wt)),
                   note=f"dùng lại worktree đang có của {task_id} (làm tiếp trên nhánh {branch})")
        return out
    notes = []
    f = _git(["fetch", REMOTE, base], cwd=repo, timeout=120)
    if f.returncode != 0:
        notes.append(f"git fetch {REMOTE} {base} lỗi ({_err(f)[-200:]}) — tạo từ bản {REMOTE}/{base} đang có")
    base_ref = ""
    for ref, name in ((f"refs/remotes/{REMOTE}/{base}", f"{REMOTE}/{base}"), (f"refs/heads/{base}", base)):
        if _git(["rev-parse", "--verify", "--quiet", ref], cwd=repo).returncode == 0:
            base_ref = name
            break
    if not base_ref:
        base_ref = "HEAD"
        notes.append(f"không có {REMOTE}/{base} hay {base} — tạo từ HEAD của repo")
    elif base_ref != f"{REMOTE}/{base}":
        notes.append(f"không có {REMOTE}/{base} — tạo từ nhánh local {base}")
    try:
        os.makedirs(os.path.dirname(wt), exist_ok=True)
    except OSError as e:
        out["error"] = f"Không tạo được thư mục worktree: {e}"
        return out
    _git(["worktree", "prune"], cwd=repo)
    has_branch = _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo).returncode == 0
    if has_branch:
        notes.append(f"nhánh {branch} đã có sẵn — làm tiếp trên nhánh đó")
        args = ["worktree", "add", wt, branch]
    else:
        args = ["worktree", "add", "--no-track", "-b", branch, wt, base_ref]
    r = _git(args, cwd=repo, timeout=120)
    if r.returncode != 0:
        out["error"] = f"git worktree add thất bại: {_err(r)[-300:]}"
        return out
    out.update(ok=True, base_ref=base_ref, base_sha=_out(_git(["rev-parse", "HEAD"], cwd=wt)), note="; ".join(notes))
    return out


def running_build(task_id):
    """Dòng dispatch_log build đang running của task (None nếu không có)."""
    with db.get_connection() as conn:
        r = conn.execute("SELECT id FROM dispatch_log WHERE kind = ? AND task_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
                         (KIND, task_id)).fetchone()
    return r["id"] if r else None


def cleanup_task_worktree(task_id, repo_key=""):
    """
    Gỡ worktree của task (`git worktree remove --force`, rồi prune) và hook chặn của task. Nhánh wt/TSK-n (local + remote) giữ
    nguyên để còn review / làm bằng chứng. Task đang có lần build chạy → không gỡ. Trả {removed, dir, branch, error?}.
    """
    if not TASK_ID_RE.match(task_id or ""):
        return {"removed": False, "dir": "", "error": "mã task không hợp lệ"}
    k = (repo_key or "").strip()
    if not k:
        try:
            k = db.get_task_repo(task_id)
        except Exception:
            pass
    if k and (not REPO_KEY_RE.match(k) or k.startswith("gw-") or k.startswith("tsk-")):
        return {"removed": False, "dir": "", "error": f"repo không hợp lệ: '{k}'"}
    repo_conf = get_repo(k, auto_clone=False)
    repo = repo_conf["clone_dir"] if repo_conf else repo_dir()
    wt = task_worktree_dir(task_id, k)
    res = {"removed": False, "dir": wt, "branch": task_branch(task_id)}
    if not os.path.exists(wt):
        if k:
            old_wt = task_worktree_dir(task_id, "")
            if os.path.exists(old_wt):
                wt = old_wt
                repo = repo_dir()
                res["dir"] = wt
            else:
                return res
        else:
            return res
    busy = running_build(task_id)
    if busy:
        res["error"] = f"đang có lần build dispatch:{busy} chạy — không gỡ"
        return res
    r = _git(["worktree", "remove", "--force", wt], cwd=repo, timeout=60)
    if r.returncode != 0:
        res["error"] = _err(r)[-300:]
    else:
        res["removed"] = True
    _git(["worktree", "prune"], cwd=repo)
    shutil.rmtree(hooks_dir(task_id), ignore_errors=True)
    if res["removed"]:
        print(f"[build] Đã gỡ worktree {wt} (giữ nhánh {res['branch']})")
    return res


# ---------------------------------------------------------------------------
# Lớp chặn thứ hai cho tiến trình agy: hook git + pushurl hỏng
# ---------------------------------------------------------------------------
def write_guard_hooks(task_id, worktree, branch):
    """Hook pre-commit (chỉ commit trên đúng nhánh trong đúng worktree), pre-push / pre-rebase (chặn). Trả thư mục hook."""
    d = hooks_dir(task_id)
    os.makedirs(d, exist_ok=True)
    wt = os.path.realpath(worktree)
    pre_commit = f"""#!/bin/sh
# gen-workplace chế độ Làm (#45): agy chỉ được commit trên nhánh {branch} trong worktree {wt}
b=$(git symbolic-ref --short -q HEAD)
top=$(git rev-parse --show-toplevel 2>/dev/null)
top=$(cd "$top" 2>/dev/null && pwd -P)
if [ "$b" != "{branch}" ] || [ "$top" != "{wt}" ]; then
  echo "gen-workplace: chặn commit — chỉ được commit trên nhánh {branch} trong {wt} (đang ở nhánh '$b', thư mục '$top')" >&2
  exit 1
fi
exit 0
"""
    block = "#!/bin/sh\necho \"gen-workplace: chặn {what} từ agy — app tự push nhánh sau khi kiểm test\" >&2\nexit 1\n"
    for name, body in (("pre-commit", pre_commit), ("pre-push", block.format(what="git push")),
                       ("pre-rebase", block.format(what="git rebase"))):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(p, 0o755)
    return d


def agy_git_env(hooks, session_id):
    """Biến môi trường git CHỈ cho tiến trình agy: hook chặn, pushurl hỏng, tác giả commit = vai (agy)."""
    who = f"{session_id} (agy)"
    mail = f"{session_id}@gen-workplace.local"
    return {"GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": hooks,
            "GIT_CONFIG_KEY_1": f"remote.{REMOTE}.pushurl", "GIT_CONFIG_VALUE_1": "gw-push-blocked::agy-khong-duoc-push",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": mail, "GIT_COMMITTER_NAME": who, "GIT_COMMITTER_EMAIL": mail}


def _repo_snapshot(repo, base=None):
    b = (base or base_branch()).strip()
    return {"main": _out(_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{b}"], cwd=repo)),
            "head": _out(_git(["rev-parse", "HEAD"], cwd=repo))}


def detect_violations(repo, before, worktree, branch, base=None, extra=None):
    """
    Sau khi agy chạy: nhánh main local / HEAD của repo app đổi sang commit KHÔNG có trên origin/<base> (auto-update kéo main
    về thì hợp lệ) → vi phạm; worktree không còn ở wt/TSK-n → vi phạm. Trả list mô tả.
    """
    v = []
    for rp, bf, bs in [(repo, before, base)] + list(extra or []):
        b = (bs or base_branch()).strip()
        after = _repo_snapshot(rp, base=b)
        base_ref = f"{REMOTE}/{b}"
        for key, label in (("main", f"nhánh {b} local"), ("head", f"HEAD của repo {os.path.basename(rp)}")):
            old, new = bf.get(key) or "", after.get(key) or ""
            if new and new != old and _git(["merge-base", "--is-ancestor", new, base_ref], cwd=rp).returncode != 0:
                v.append(f"{label} đổi {old[:10] or '(trống)'} → {new[:10]} (commit không có trên {base_ref})")
    cur = _out(_git(["symbolic-ref", "--short", "-q", "HEAD"], cwd=worktree))
    if cur != branch:
        v.append(f"worktree không còn ở {branch} (đang ở '{cur or 'detached'}')")
    return v


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
def load_sop(kind="python"):
    k = (kind or "python").strip().lower()
    if k and k != "python":
        cand = os.path.join(str(db.BASE_DIR), "roles", f"build.{k}.md")
        try:
            with open(cand, "r", encoding="utf-8") as f:
                txt = f.read().strip()
                if txt:
                    return txt
        except OSError:
            pass
    p = os.path.join(str(db.BASE_DIR), "roles", "build.md")
    try:
        with open(p, "r", encoding="utf-8") as f:
            txt = f.read().strip()
            if txt:
                return txt
    except OSError:
        pass
    return DEFAULT_SOP


def build_prompt(task_id, session_id, worktree, branch, project_id="PRJ-GEN-WORKPLACE", pending=None, base=None,
                 repo_conf=None):
    """Prompt chế độ Làm: SOP roles/build.md (hoặc build.<kind>.md) + khối THÔNG TIN VIỆC (tiêu đề, viec_ref, mô tả, checklist) + luật quyền."""
    b = (base or base_branch()).strip()
    r_key = (repo_conf.get("key") if repo_conf else "") or "gen-workplace"
    r_kind = ((repo_conf.get("kind") if repo_conf else "") or "python").strip().lower()
    r_test_cmd = (repo_conf.get("test_cmd") if repo_conf else "").strip()
    if r_kind == "node":
        test_cmd_desc = r_test_cmd if r_test_cmd else "node --test tests/*.test.mjs"
        allowed_test = test_cmd_desc
    else:
        test_cmd_desc = r_test_cmd if r_test_cmd else "python3 -m py_compile <file>; python3 scripts/test_<tên>.py"
        allowed_test = "python3 -m py_compile <file>; python3 scripts/test_<tên>.py"

    task_block = db.build_task_prompt_block(task_id, project_id)
    rules = (
        "[CHẾ ĐỘ LÀM — agy sửa code thật, không ai duyệt quyền giữa chừng]\n"
        f"- Bạn đang ở worktree riêng của việc: {worktree} (nhánh {branch}, tạo từ {REMOTE}/{b}, repo '{r_key}', kind '{r_kind}').\n"
        f"- Lệnh test: {test_cmd_desc}.\n"
        "  CHỈ tạo/sửa file trong thư mục này; không cd ra ngoài; sửa file bằng công cụ sửa file của agy "
        "(không dùng sed -i, echo >, tee, cat >).\n"
        f"- Lệnh shell được phép: lệnh chỉ đọc ({db.agy_plan_allowed_summary()}); {allowed_test}; "
        "git status, git diff, git add <file>, git commit -m \"<message>\".\n"
        "- Cấm (hệ thống TỪ CHỐI): git push / remote / fetch / pull / checkout main / switch / reset --hard / -C / -c / "
        "commit --no-verify / --amend, rm -rf, xóa hay sửa file ngoài worktree, curl / wget / ssh / lệnh mạng, sudo, "
        "pip / npm / apt. Không dùng $(...), dấu `...`, python3 -c, node -e, bash -c, xargs, awk.\n"
        "- Chạy TỪNG lệnh riêng, đơn giản (vd `python3 -m py_compile backend/db.py`, rồi `python3 scripts/test_x.py`); "
        "không nối thêm `&& echo …`, `;`, `||`. Một lệnh bị từ chối là lần chạy dừng luôn, việc chưa commit sẽ mất lượt.\n"
        f"- BẮT BUỘC có ít nhất 1 commit trên {branch} trước khi kết thúc; không commit thì việc bị tính là chưa làm. "
        "KHÔNG push: app tự chạy py_compile + toàn bộ test rồi push nhánh sau khi bạn xong.\n"
        "- Bị chặn quyền: KHÔNG dừng im lặng — làm tiếp phần còn lại, ghi dòng \"CẦN QUYỀN: <lệnh> — <lý do>\" trong báo cáo."
    )
    if pending:
        # Giao lại cùng task: worktree còn thay đổi chưa commit của lần trước (dispatch:15 agy tưởng việc đã có người làm, #49)
        rules += ("\n- LƯU Ý: worktree đang có thay đổi CHƯA COMMIT do chính bạn làm ở lần trước (bị dừng giữa chừng): "
                  + ", ".join(pending[:15]) + ". Đó là việc dang dở của bạn, không phải của người khác: xem `git diff`, "
                  "làm nốt phần còn thiếu, chạy test rồi commit.")
    tail = (f"(Bạn là {session_id}. Làm trọn việc trong một lượt, không hỏi lại, không chỉ nêu kế hoạch. "
            "Kết thúc bằng báo cáo ngắn tiếng Việt theo mục 5 của SOP.)")
    return f"{load_sop(kind=r_kind)}\n\n" + (f"{task_block}\n\n" if task_block else f"[THÔNG TIN VIỆC {task_id}]\n\n") + f"{rules}\n\n{tail}"


# ---------------------------------------------------------------------------
# Node / Dependency Helpers & Exclude
# ---------------------------------------------------------------------------
def _add_git_exclude(worktree, pattern="node_modules"):
    r = _git(["rev-parse", "--git-path", "info/exclude"], cwd=worktree)
    p = _out(r)
    if not p:
        p = os.path.join(worktree, ".git", "info", "exclude")
    elif not os.path.isabs(p):
        p = os.path.abspath(os.path.join(worktree, p))
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        cur = ""
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                cur = f.read()
        lines = [ln.strip() for ln in cur.splitlines()]
        if pattern not in lines and f"{pattern}/" not in lines:
            with open(p, "a", encoding="utf-8") as f:
                if cur and not cur.endswith("\n"):
                    f.write("\n")
                f.write(f"{pattern}\n")
    except Exception as e:
        print(f"[build] Không ghi được git exclude ({pattern}): {e}")


def _get_base_file_content(repo, base, filename):
    for ref in [f"{REMOTE}/{base}", base, f"refs/remotes/{REMOTE}/{base}", f"refs/heads/{base}"]:
        r = _git(["show", f"{ref}:{filename}"], cwd=repo)
        if r.returncode == 0:
            return r.stdout
    return None


def ensure_node_modules(worktree, repo_conf=None, timeout=120):
    """
    Cài node_modules an toàn từ lockfile của base (Issue #57 mục 5):
    - Đọc package-lock.json + package.json TỪ origin/<base> (git show), tính sha256 của lockfile.
    - Cache tại <DATA_DIR>/cache/node_modules/<key>/<sha>/.
    - Nếu chưa có thì dựng thư mục tạm chứa package.json + package-lock.json của base,
      chạy npm ci --ignore-scripts --no-audit --no-fund (timeout, env sạch KHÔNG có token).
    - Liên kết vào worktree bằng symlink node_modules → cache (chỉ khi worktree chưa có node_modules).
    - Thêm node_modules vào .git/info/exclude để không bao giờ bị commit.
    """
    conf = repo_conf or {}
    key = (conf.get("key") or "gen-workplace").strip()
    base = (conf.get("base") or base_branch()).strip()
    clone_dir = conf.get("clone_dir") or repo_dir()

    lock_content = _get_base_file_content(clone_dir, base, "package-lock.json")
    if lock_content is None and worktree:
        lock_content = _get_base_file_content(worktree, base, "package-lock.json")

    if lock_content is None:
        return {"ok": True, "cached": False, "note": "không có package-lock.json ở base"}

    pkg_content = _get_base_file_content(clone_dir, base, "package.json")
    if pkg_content is None and worktree:
        pkg_content = _get_base_file_content(worktree, base, "package.json")
    if not pkg_content:
        pkg_content = "{}"

    sha = hashlib.sha256(lock_content.encode("utf-8") if isinstance(lock_content, str) else lock_content).hexdigest()
    cache_dir = os.path.join(str(db.DATA_DIR), "cache", "node_modules", key, sha)

    if not os.path.isdir(cache_dir):
        tmp_build = tempfile.mkdtemp(prefix=f"gw-npm-ci-{key}-")
        try:
            with open(os.path.join(tmp_build, "package.json"), "w", encoding="utf-8") as f:
                f.write(pkg_content)
            with open(os.path.join(tmp_build, "package-lock.json"), "w", encoding="utf-8") as f:
                f.write(lock_content)

            # Env sạch không chứa token
            clean_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.path.join(tmp_build, "home"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TMPDIR": tmp_build,
            }
            os.makedirs(clean_env["HOME"], exist_ok=True)
            cmd = ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
            to = timeout if timeout is not None else _env_int("GW_BUILD_NPM_TIMEOUT_SEC", 120)
            try:
                p = subprocess.run(cmd, cwd=tmp_build, capture_output=True, text=True, timeout=to, env=clean_env)
                if p.returncode != 0:
                    err = _redact(((p.stderr or "") + "\n" + (p.stdout or "")).strip())[-600:]
                    return {"ok": False, "error": f"npm ci thất bại (rc={p.returncode}): {err}"}
            except subprocess.TimeoutExpired:
                return {"ok": False, "error": f"npm ci quá {to}s"}
            except FileNotFoundError:
                return {"ok": False, "error": "không tìm thấy lệnh npm"}

            nm_built = os.path.join(tmp_build, "node_modules")
            if not os.path.isdir(nm_built):
                return {"ok": False, "error": "npm ci không tạo được node_modules"}

            os.makedirs(os.path.dirname(cache_dir), exist_ok=True)
            if not os.path.isdir(cache_dir):
                shutil.move(nm_built, cache_dir)
        finally:
            shutil.rmtree(tmp_build, ignore_errors=True)

    wt_nm = os.path.join(worktree, "node_modules")
    if not os.path.exists(wt_nm) and not os.path.islink(wt_nm):
        try:
            os.symlink(cache_dir, wt_nm)
        except OSError as e:
            return {"ok": False, "error": f"Không thể tạo symlink node_modules: {e}"}

    _add_git_exclude(worktree, "node_modules")
    return {"ok": True, "cached": True, "cache_dir": cache_dir, "symlink": wt_nm}


def check_deps_changed(worktree, base="main"):
    """Kiểm tra nhánh trong worktree có thay đổi package.json hoặc package-lock.json so với base."""
    b = (base or "main").strip() or "main"
    ref = f"{REMOTE}/{b}"
    if _git(["rev-parse", "--verify", "--quiet", ref], cwd=worktree).returncode != 0:
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{b}"], cwd=worktree).returncode == 0:
            ref = f"refs/heads/{b}"
        else:
            return False
    diff_range = f"{ref}...HEAD"
    r = _git(["diff", "--name-only", "--no-renames", "-z", diff_range], cwd=worktree)
    if r.returncode != 0:
        r = _git(["diff", "--name-only", "--no-renames", "-z", f"{ref}..HEAD"], cwd=worktree)
        if r.returncode != 0:
            return False
    for p in (r.stdout or "").split("\0"):
        norm = p.strip().replace("\\", "/")
        bname = os.path.basename(norm)
        if bname in ("package.json", "package-lock.json"):
            return True
    return False


# ---------------------------------------------------------------------------
# Kiểm của app sau khi agy xong: py_compile + test, push, compare / PR nháp
# ---------------------------------------------------------------------------
def run_tests(worktree, budget_sec=None, repo_conf=None):
    """
    Chạy test theo repo_conf.kind:
    - "python" (mặc định): py_compile mọi file .py đang theo dõi + chạy từng scripts/test_*.py (trừ GW_BUILD_TEST_EXCLUDE).
    - "node": chuẩn bị node_modules an toàn từ lockfile base, chạy lệnh cố định từ repo_conf.test_cmd nếu có
      (tách bằng shlex, KHÔNG shell=True), mặc định ["node", "--test"] + glob tests/*.test.mjs (sắp xếp).
      KHÔNG đọc scripts trong package.json của nhánh.
    Trả {ok, py_compile: {ok, files, output}, tests: [{name, ok, rc, sec, tail}], passed, total, skipped, note, sec}.
    """
    t0 = time.time()
    budget = budget_sec if budget_sec is not None else db.AGY_BUILD_POST_SEC - 120
    per_test = _env_int("GW_BUILD_TEST_TIMEOUT_SEC", 300)
    tmp = tempfile.mkdtemp(prefix="gw-build-test-")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.path.join(tmp, "home"), "LANG": "C.UTF-8",
           "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": os.path.join(tmp, "pycache"),
           "TMPDIR": tmp, "GW_AUTO_UPDATE": "0", "PYTHONIOENCODING": "utf-8"}
    os.makedirs(env["HOME"], exist_ok=True)
    res = {"ok": False, "py_compile": {"ok": False, "files": 0, "output": ""}, "tests": [], "passed": 0, "total": 0,
           "skipped": [], "note": "", "sec": 0.0}
    try:
        conf = repo_conf or {}
        kind = (conf.get("kind") or "python").strip().lower()
        if kind == "node":
            res["py_compile"] = {"ok": True, "files": 0, "output": "không áp dụng (node)"}
            left = budget - (time.time() - t0)
            if left < 5:
                res["note"] = f"hết ngân sách thời gian test ({int(budget)}s)"
                return res

            nm_res = ensure_node_modules(worktree, conf, timeout=min(120, max(10, int(left))))
            if not nm_res.get("ok"):
                res["note"] = nm_res.get("error") or "Không thể chuẩn bị node_modules"
                return res

            test_cmd = (conf.get("test_cmd") or "").strip()
            if test_cmd:
                cmd = shlex.split(test_cmd)
            else:
                tdir = os.path.join(worktree, "tests")
                files = []
                if os.path.isdir(tdir):
                    files = sorted([os.path.join("tests", f) for f in os.listdir(tdir)
                                    if fnmatch.fnmatch(f, "*.test.mjs") and os.path.isfile(os.path.join(tdir, f))])
                cmd = ["node", "--test"] + files

            left = budget - (time.time() - t0)
            if left < 5:
                res["note"] = f"hết ngân sách thời gian test ({int(budget)}s)"
                return res

            s = time.time()
            try:
                p = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True,
                                   timeout=min(per_test, left), env=env)
                rc, tail = p.returncode, ((p.stdout or "") + "\n" + (p.stderr or "")).strip()[-800:]
            except subprocess.TimeoutExpired:
                rc, tail = -1, f"quá {int(min(per_test, left))}s"
            except FileNotFoundError:
                rc, tail = 127, f"không tìm thấy lệnh: {cmd[0] if cmd else ''}"

            res["tests"].append({"name": " ".join(cmd), "ok": rc == 0, "rc": rc, "sec": round(time.time() - s, 1),
                                 "tail": tail if rc else tail[-200:]})
            res["total"] = 1
            res["passed"] = 1 if rc == 0 else 0
            res["ok"] = (rc == 0) and not res["note"]
        else:
            files = [f for f in _out(_git(["ls-files", "*.py"], cwd=worktree)).splitlines() if f.strip()]
            res["py_compile"]["files"] = len(files)
            if files:
                try:
                    p = subprocess.run([sys.executable, "-m", "py_compile"] + files, cwd=worktree, capture_output=True, text=True,
                                       timeout=120, env=env)
                    res["py_compile"].update(ok=p.returncode == 0, output=((p.stderr or "") + (p.stdout or "")).strip()[-800:])
                except subprocess.TimeoutExpired:
                    res["py_compile"]["output"] = "py_compile quá 120s"
            else:
                res["py_compile"].update(ok=True, output="không có file .py")
            sdir = os.path.join(worktree, "scripts")
            names = sorted(n for n in (os.listdir(sdir) if os.path.isdir(sdir) else [])
                           if re.match(r"^test_[A-Za-z0-9_]+\.py$", n))
            excl = test_exclude()
            res["skipped"] = [n for n in names if n in excl]
            todo = [n for n in names if n not in excl]
            res["total"] = len(todo)
            for n in todo:
                left = budget - (time.time() - t0)
                if left < 10:
                    res["note"] = f"hết ngân sách thời gian test ({int(budget)}s): chưa chạy {len(todo) - len(res['tests'])} file"
                    break
                s = time.time()
                try:
                    p = subprocess.run([sys.executable, os.path.join("scripts", n)], cwd=worktree, capture_output=True, text=True,
                                       timeout=min(per_test, left), env=env)
                    rc, tail = p.returncode, ((p.stdout or "") + "\n" + (p.stderr or "")).strip()[-800:]
                except subprocess.TimeoutExpired:
                    rc, tail = -1, f"quá {int(min(per_test, left))}s"
                res["tests"].append({"name": n, "ok": rc == 0, "rc": rc, "sec": round(time.time() - s, 1), "tail": tail if rc else tail[-200:]})
            res["passed"] = sum(1 for t in res["tests"] if t["ok"])
            res["ok"] = res["py_compile"]["ok"] and res["passed"] == res["total"] and not res["note"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        res["sec"] = round(time.time() - t0, 1)
    return res


def github_repo_slug(repo=None):
    """'owner/repo' của origin (GW_BUILD_GITHUB_REPO ghi đè khi remote không phải GitHub, vd test); không suy ra được → ''."""
    forced = (os.environ.get("GW_BUILD_GITHUB_REPO") or "").strip()
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", forced):
        return forced
    url = _out(_git(["remote", "get-url", REMOTE], cwd=repo or repo_dir()))
    m = _GITHUB_URL_RE.search(url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


def compare_url(slug, branch, base=None):
    b = (base or base_branch()).strip()
    return f"https://github.com/{slug}/compare/{b}...{branch}" if slug else ""


def push_branch(worktree, branch):
    """App push wt/TSK-n lên origin bằng credential git sẵn có của máy (không hỏi mật khẩu). Trả {ok, ref, sha, error, output}."""
    sha = _out(_git(["rev-parse", "HEAD"], cwd=worktree))
    r = _git(["push", "--porcelain", REMOTE, f"HEAD:refs/heads/{branch}"], cwd=worktree, timeout=_env_int("GW_BUILD_PUSH_TIMEOUT_SEC", 120))
    out = {"ok": r.returncode == 0, "ref": branch, "sha": sha, "output": _redact((r.stdout or "").strip())[-400:]}
    if r.returncode != 0:
        out["error"] = _err(r)[-400:] or f"git push thoát {r.returncode}"
    return out


def create_draft_pr(slug, branch, title, body, base=None):
    """Có GITHUB_TOKEN → tạo PR NHÁP head=wt/TSK-n base=<base>. Không bao giờ merge. Trả (url, lỗi)."""
    token = db.github_token()
    if not token or not slug:
        return "", ""
    b = (base or base_branch()).strip()
    api = (os.environ.get("GW_GITHUB_API_URL") or "https://api.github.com").rstrip("/")
    data = json.dumps({"title": title, "head": branch, "base": b, "body": body, "draft": True}).encode("utf-8")
    req = urllib.request.Request(f"{api}/repos/{slug}/pulls", data=data, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "Content-Type": "application/json", "User-Agent": "gen-workplace-build"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return (json.loads(resp.read().decode("utf-8") or "{}").get("html_url") or ""), ""
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8") or "{}").get("message") or ""
        except Exception:
            msg = ""
        return "", f"HTTP {e.code} {msg}".strip().replace(token, "***")
    except Exception as e:
        return "", f"{type(e).__name__}: {e}".replace(token, "***")


BLOCKED_DIRS = (".github", ".gitea", "deploy")
BLOCKED_FILES = ("dockerfile", "docker-compose.yml", "install.sh")


def _push_diff_reasons(path, prot):
    """Lý do chặn cho 1 đường dẫn trong diff (so khớp không phân biệt hoa thường cho đường cấm cố định)."""
    norm = path.replace("\\", "/").lstrip("/")
    parts = [x for x in norm.split("/") if x and x != "."]
    norm = "/".join(parts)
    low = norm.lower()
    lparts = low.split("/") if low else []
    bname = parts[-1] if parts else ""
    lb = bname.lower()
    if lparts and lparts[0] in BLOCKED_DIRS:
        return f"thư mục '{parts[0]}/' ({norm})"
    if lb in BLOCKED_FILES:
        return f"file '{bname}' ({norm})"
    if lb.startswith(".env"):
        return f"file nhạy cảm '{bname}' ({norm})"
    for pat in prot:
        pc = pat.replace("\\", "/").strip().lstrip("/")
        if pc.startswith("./"):
            pc = pc[2:]
        if not pc:
            continue
        pr = pc.rstrip("/")
        if norm == pr or norm.startswith(pr + "/") or fnmatch.fnmatchcase(norm, pc) or fnmatch.fnmatchcase(bname, pc):
            return f"protected_paths '{pat}' ({norm})"
    return ""


def check_push_diff(worktree, base="main", protected_paths=None, fetch=True):
    """
    Kiểm diff trước khi push (Issue #57 mục 4) — lớp chặn chính, áp cho mọi repo kể cả gen-workplace:
    so HEAD với merge-base của origin/<base> (git diff --no-renames -z origin/<base>...HEAD, cả --name-status và --raw).
    Chặn nếu có file (thêm / sửa / xoá, kể cả 2 đầu của rename):
    - dưới .github/, .gitea/, deploy/ ở gốc repo
    - tên Dockerfile, docker-compose.yml, install.sh, .env*
    - khớp protected_paths
    - mode mới 120000 (symlink) / 160000 (submodule)
    Không kiểm được (thiếu ref, git lỗi) → chặn (fail closed). Trả list lý do chặn; rỗng = cho push.
    """
    violations = []

    def add(msg):
        if msg and msg not in violations:
            violations.append(msg)

    b = (base or "main").strip() or "main"
    if not _valid_base(b):
        return [f"base không hợp lệ: '{b}'"]
    if fetch:
        _git(["fetch", "--quiet", REMOTE, f"+refs/heads/{b}:refs/remotes/{REMOTE}/{b}"], cwd=worktree,
             timeout=_env_int("GW_BUILD_FETCH_TIMEOUT_SEC", 60))
    ref = f"refs/remotes/{REMOTE}/{b}"
    if _git(["rev-parse", "--verify", "--quiet", ref], cwd=worktree).returncode != 0:
        if _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{b}"], cwd=worktree).returncode == 0:
            ref = f"refs/heads/{b}"
        else:
            return [f"không kiểm được diff: không có {REMOTE}/{b}"]
    diff_range = f"{ref}...HEAD"
    prot = [str(x) for x in (protected_paths or []) if isinstance(x, str)]

    # 1. Tên file (-z: không bị quote đường dẫn có ký tự lạ)
    r_name = _git(["diff", "--name-only", "--no-renames", "-z", diff_range], cwd=worktree)
    if r_name.returncode != 0:
        return [f"không kiểm được diff ({_err(r_name)[:200]})"]
    for p in (r_name.stdout or "").split("\0"):
        if p:
            add(_push_diff_reasons(p, prot))

    # 2. Mode (symlink / submodule)
    r_raw = _git(["diff", "--raw", "--no-renames", "-z", "--no-abbrev", diff_range], cwd=worktree)
    if r_raw.returncode != 0:
        return violations + [f"không kiểm được diff --raw ({_err(r_raw)[:200]})"]
    toks = (r_raw.stdout or "").split("\0")
    i = 0
    while i < len(toks):
        meta = toks[i]
        if not meta.startswith(":"):
            i += 1
            continue
        path = toks[i + 1] if i + 1 < len(toks) else ""
        i += 2
        parts = meta[1:].split()
        dst_mode = parts[1] if len(parts) >= 2 else ""
        if dst_mode == "120000":
            add(f"symlink mode 120000 ({path})")
        elif dst_mode == "160000":
            add(f"submodule mode 160000 ({path})")

    return violations


# ---------------------------------------------------------------------------
# Giao việc + chạy
# ---------------------------------------------------------------------------
def assign_build(todo_id, sid, role, task, body, claim, project_id="PRJ-GEN-WORKPLACE", author="Ryan (Owner)",
                 channel_id="war_room", wait=False, repo=""):
    """Phần chế độ Làm của db.assign_task_to_role (task đã claim cho sid): ghi tin giao việc vào war-room (không giao qua
    war-room), tạo dòng dispatch_log kind=build (running) rồi chạy run_build ở thread nền (wait=True: chạy đồng bộ)."""
    busy = running_build(todo_id)
    if busy:
        return {"error": f"{todo_id} đang có lần Làm dispatch:{busy} chạy — chờ xong (wait_worker_result) rồi giao lại",
                "code": "busy", "task_id": todo_id, "dispatch_id": busy}
    repo_key = (repo or task.get("repo") or "").strip()
    branch, wt = task_branch(todo_id), task_worktree_dir(todo_id, repo_key)
    msg = f"[Làm] {body}\n(chế độ Làm: agy sửa code trong worktree {wt}, nhánh {branch}; app tự chạy test + push nhánh)"
    msg_id = db.save_warroom_record(project_id, channel_id, author, msg, "Assignment")
    did = db.start_dispatch_log(sid, kind=KIND, channel_id=channel_id, task_id=todo_id,
                                viec_ref=task.get("viec_ref") or "", request_msg_id=msg_id)
    with db.get_connection() as conn:
        conn.execute("UPDATE dispatch_log SET worktree_dir = ?, build_branch = ? WHERE id = ?", (wt, branch, did))
        conn.commit()
    if wait:
        run_build(did, project_id)
    else:
        threading.Thread(target=run_build, args=(did, project_id), daemon=True, name=f"agy-build-{todo_id}").start()
    return {"status": "assigned", "mode": KIND, "task_id": todo_id, "session_id": sid, "role": role,
            "viec_ref": task.get("viec_ref") or "", "dispatch_id": did, "request_msg_id": msg_id, "channel_id": channel_id,
            "claim": claim, "message": msg, "branch": branch, "worktree_dir": wt}


def _session_profile(session_id):
    account, profile_dir = "owner_default", ""
    try:
        with db.get_connection() as conn:
            row = conn.execute("SELECT account_type, profile_dir FROM tmux_sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                account = row["account_type"] or "owner_default"
                profile_dir = row["profile_dir"] or ""
    except Exception:
        pass
    return account, (os.path.expanduser(profile_dir) if profile_dir else db._profile_dir(account))


def _set_row(did, **cols):
    if not cols:
        return
    keys = list(cols)
    with db.get_connection() as conn:
        conn.execute(f"UPDATE dispatch_log SET {', '.join(k + ' = ?' for k in keys)} WHERE id = ?", [cols[k] for k in keys] + [did])
        conn.commit()


def build_command(p_dir, prompt, stream=True):
    """Lệnh agy chế độ Làm: chế độ mặc định (không --mode plan), không --model, không --dangerously-skip-permissions."""
    return [db._agy_bin(), f"--gemini_dir={p_dir}", "-p", prompt] + (db.AGY_STREAM_ARGS if stream else [])


def run_build(dispatch_id, project_id="PRJ-GEN-WORKPLACE"):
    """Chạy 1 lần build (dòng dispatch_log kind=build đang running) tới khi chốt done/failed. Không ném lỗi."""
    try:
        return _run_build(dispatch_id, db.normalize_project_id(project_id))
    except Exception as e:
        print(f"[build] dispatch:{dispatch_id} lỗi: {type(e).__name__}: {e}")
        try:
            with db.get_connection() as conn:
                conn.execute("UPDATE dispatch_log SET status = 'failed', finished_at = ?, summary = ? WHERE id = ? AND status = 'running'",
                             (time.strftime("%Y-%m-%d %H:%M:%S"), f"Lỗi nội bộ chế độ Làm: {type(e).__name__}: {e}", dispatch_id))
                conn.commit()
            db.report_dispatch_to_task(dispatch_id, project_id)
        finally:
            db._notify_dispatch_change()
        return {"dispatch_id": dispatch_id, "status": "failed", "error": str(e)}


def _run_build(did, project_id):
    row = db._get_dispatch_row(did) or {}
    sid, task_id, channel_id = row.get("session_id") or "", row.get("task_id") or "", row.get("channel_id") or "war_room"
    t0 = time.time()
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")

    task = db._task_detail(task_id, project_id) or {}
    repo_key = (task.get("repo") or "").strip()
    repo_conf = get_repo(repo_key, auto_clone=True)
    if not repo_conf:
        repo_conf = get_repo("gen-workplace", auto_clone=False)
    repo = repo_conf["clone_dir"]
    repo_base = repo_conf.get("base") or base_branch()

    account, primary_dir = _session_profile(sid)
    wt_info = ensure_task_worktree(task_id, repo_key)
    wt, branch = wt_info.get("dir") or task_worktree_dir(task_id, repo_key), wt_info.get("branch") or task_branch(task_id)
    _set_row(did, worktree_dir=wt, build_branch=branch, profile_initial=account)

    ctx = {"exit_code": -1, "output": "", "sinfo": {}, "cmd": [], "fail": "", "blocked": "", "fb_note": "", "profile_used": "",
           "attempts": [], "perm_note": "", "commits": [], "head": "", "dirty": [], "violations": [], "tests": None,
           "push": None, "compare": "", "pr_url": ""}
    if not wt_info.get("ok"):
        ctx["fail"] = f"Không tạo được worktree: {wt_info.get('error')}"
        return _finish(did, row, ctx, wt, branch, wt_info, started_at, t0, project_id, account)

    before_repo = _repo_snapshot(repo, base=repo_base)
    # Repo khác: kiểm thêm HEAD/main của repo app gen-workplace (Issue #57 mục 3)
    app_repo = repo_dir()
    extra_snap = [] if os.path.realpath(app_repo) == os.path.realpath(repo) else [(app_repo, _repo_snapshot(app_repo), None)]
    before_head = _out(_git(["rev-parse", "HEAD"], cwd=wt))
    st0 = _git(["status", "--porcelain"], cwd=wt)
    pending = [ln[3:] for ln in (st0.stdout or "").splitlines() if ln.strip()] if st0.returncode == 0 else []
    prompt = build_prompt(task_id, sid, wt, branch, project_id, pending=pending, base=repo_base, repo_conf=repo_conf)
    if (repo_conf.get("kind") or "").strip().lower() == "node":
        ensure_node_modules(wt, repo_conf)
    hooks = write_guard_hooks(task_id, wt, branch)
    stream = {"ok": not db._env_on("GW_AGY_NO_STREAM")}
    timeout = db.AGY_BUILD_TIMEOUT_SEC
    tried, res = [], None
    for _ in range(12):
        sel = db.select_agy_profile_for_run(account, primary_dir, tried=tried)
        if not sel["profile_id"]:
            ctx["fb_note"] = sel["note"]
            if res is None:
                ctx["fail"] = f"Không gọi agy: {sel['note']}"
            break
        pid, pdir = sel["profile_id"], sel["p_dir"]
        if sel["note"]:
            ctx["fb_note"] = sel["note"]
        token = db.ensure_agy_build_permissions(pdir, wt, repo)
        if token is None:
            ctx["perm_note"] = (f"không ghi được allow-rule chế độ Làm vào hồ sơ {pid} (chưa có {pdir}/antigravity-cli hoặc "
                                "settings hỏng / GW_AGY_PLAN_ALLOW=0) — agy có thể bị từ chối quyền")
        env = {**db._agy_env(pdir), **agy_git_env(hooks, sid)}
        cmd = build_command(pdir, prompt, stream["ok"])
        ctx["cmd"], ctx["profile_used"] = cmd, pid
        _set_row(did, command=" ".join(cmd[:3] + ["<prompt>"] + cmd[4:]), profile_used=pid)
        st = "error"
        try:
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=wt, env=env)
                if stream["ok"] and db.agy_flag_unsupported(res):
                    stream["ok"] = False
                    cmd = build_command(pdir, prompt, False)
                    ctx["cmd"] = cmd
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=wt, env=env)
                res, ctx["sinfo"] = db.normalize_agy_stream_result(res)
                st, _reset = db.record_quota_probe_from_result(pid, "default", res)
            except subprocess.TimeoutExpired:
                res = None
                st = "timeout"
                ctx["fail"] = f"agy không xong sau {timeout // 60} phút, đã hủy"
                db.record_quota_probe(pid, "default", "timeout", "", ctx["fail"])
            except Exception as e:
                res = None
                ctx["fail"] = f"Lỗi khi chạy agy ({db._agy_bin()}): {e}"
        finally:
            db.release_agy_build_permissions(token)
        tried.append(pid)
        ctx["attempts"].append({"profile": pid, "status": st})
        if st != "rate_limited":
            break
    if res is not None:
        ctx["exit_code"] = res.returncode
        ctx["output"] = ((res.stdout or "") + ("\n" + res.stderr if res.stderr else "")).strip()
        denied = db.agy_output_denied(ctx["output"]) if res.returncode == 0 else ""
        if res.returncode == 0 and not denied and ctx["sinfo"].get("is_stream") and not (res.stdout or "").strip():
            m = db.AGY_DENIED_RE.search(ctx["output"])
            denied = m.group(0) if m else "no output produced"
        if denied or db.agy_blocked_commands(ctx["sinfo"]):
            ctx["blocked"] = db.describe_agy_blocked(ctx["sinfo"], ctx["output"])
        if ctx["attempts"] and ctx["attempts"][-1]["status"] == "rate_limited":
            ctx["fail"] = ctx["fail"] or f"hết quota: {ctx['fb_note'] or 'mọi hồ sơ'}"
        elif denied:
            ctx["fail"] = ctx["fail"] or f"agy bị từ chối quyền / không ra kết quả ({denied}). {ctx['blocked']}"
        elif res.returncode != 0:
            ctx["fail"] = ctx["fail"] or f"agy thoát lỗi (exit={res.returncode})"

    # --- App kiểm: commit mới, vi phạm, test, push ---
    if os.path.exists(os.path.join(wt, ".git")):
        log = _out(_git(["log", "--format=%H %s", f"{before_head}..HEAD"], cwd=wt)) if before_head else ""
        ctx["commits"] = [ln for ln in log.splitlines() if ln.strip()]
        ctx["head"] = _out(_git(["rev-parse", "HEAD"], cwd=wt))
        st = _git(["status", "--porcelain"], cwd=wt)   # không strip: dòng porcelain "XY path" bắt đầu bằng dấu cách (#47)
        ctx["dirty"] = [ln for ln in (st.stdout or "").splitlines() if ln.strip()][:30] if st.returncode == 0 else []
        ctx["violations"] = detect_violations(repo, before_repo, wt, branch, base=repo_base, extra=extra_snap)
    tried_push = [t["command"] for t in (ctx["sinfo"].get("tools") or []) if re.search(r"\bgit\s+(.*\s)?push\b", t.get("command") or "")]
    if tried_push:
        ctx["push_attempts"] = tried_push
    if ctx["violations"]:
        ctx["fail"] = "VI PHẠM phạm vi chế độ Làm: " + "; ".join(ctx["violations"]) + " — KHÔNG push"
    elif not ctx["commits"] and not ctx["fail"]:
        ctx["fail"] = f"agy không tạo commit mới trên {branch}" + (f" (còn {len(ctx['dirty'])} file sửa chưa commit)" if ctx["dirty"] else "")
    if ctx["commits"] and not ctx["violations"]:
        ctx["tests"] = run_tests(wt, repo_conf=repo_conf)
        ctx["deps_changed"] = check_deps_changed(wt, base=repo_base)
        diff_blocked = check_push_diff(wt, base=repo_base, protected_paths=repo_conf.get("protected_paths"))
        if diff_blocked:
            ctx["fail"] = "Chặn push: " + "; ".join(diff_blocked)
        else:
            ctx["push"] = push_branch(wt, branch)
            slug = repo_conf.get("slug") or github_repo_slug(repo)
            ctx["compare"] = compare_url(slug, branch, base=repo_base)
            if ctx["push"]["ok"]:
                task_detail = db._task_detail(task_id, project_id) or {}
                pr_body = (f"PR nháp do app gen-workplace tạo cho lần Làm dispatch:{did} ({sid}, agy). "
                           f"Claude điều phối review rồi mới merge.\n\nRefs {task_detail.get('viec_ref') or ''} {task_id}")
                if ctx.get("deps_changed"):
                    pr_body += "\n\n⚠️ **Lưu ý**: Nhánh có thay đổi `package.json` / `package-lock.json` (deps_changed=True). Cần review kỹ dependencies trước khi merge."
                url, err = create_draft_pr(slug, branch, f"[agy] {task_id}: {task_detail.get('title') or ''}".strip(),
                                           pr_body, base=repo_base)
                if url:
                    ctx["pr_url"] = url
                    ctx["push"]["pr_url"] = url
                elif err:
                    ctx["push"]["pr_error"] = err
    return _finish(did, row, ctx, wt, branch, wt_info, started_at, t0, project_id, account)


def _finish(did, row, ctx, wt, branch, wt_info, started_at, t0, project_id, account):
    sid, task_id, channel_id = row.get("session_id") or "", row.get("task_id") or "", row.get("channel_id") or "war_room"
    status = "done" if not ctx["fail"] and ctx["commits"] else "failed"
    finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
    tests, push = ctx["tests"], ctx["push"]
    bf = {"worktree_dir": wt, "build_branch": branch, "build_commit": ctx["head"] if ctx["commits"] else "",
          "build_tests": tests, "build_push": push, "compare_url": ctx["compare"]}
    head = [f"[Làm] {task_id} · {sid} · {'XONG' if status == 'done' else 'LỖI'}"
            + (f" — {ctx['fail']}" if ctx["fail"] else "")]
    head += db.format_build_lines(bf)
    if ctx["commits"]:
        head.append("Commit mới: " + " | ".join(c[:10] + c[40:] for c in ctx["commits"][:10]))
    if ctx["dirty"]:
        head.append(f"Còn {len(ctx['dirty'])} file sửa chưa commit: " + ", ".join(d[3:] for d in ctx["dirty"][:10]))
    if ctx.get("push_attempts"):
        head.append("agy đã thử push (bị chặn, app mới là bên push): " + " ; ".join(f"`{c[:120]}`" for c in ctx["push_attempts"][:3]))
    if ctx["blocked"]:
        head.append(ctx["blocked"])
    need = db.agy_need_permission_lines(ctx["output"])
    if need:
        head.append("[agy báo cần quyền] " + " | ".join(need))
    if ctx["fb_note"]:
        head.append(f"[Tài khoản] {ctx['fb_note']}")
    if ctx["perm_note"]:
        head.append(f"[Quyền] {ctx['perm_note']}")
    if ctx.get("deps_changed"):
        head.append("[Dependencies] Nhánh có thay đổi package.json / package-lock.json (deps_changed=True)")
    if wt_info.get("note"):
        head.append(f"[Worktree] {wt_info['note']}")
    header = "\n".join(head)
    summary = header + "\n\n" + db._shorten_output(ctx["output"] or "(agy không trả output)", db.DISPATCH_SUMMARY_MAX)

    report_path = ""
    try:
        rdir = os.path.join(db.HOME_DIR, "gw-reports")
        os.makedirs(rdir, exist_ok=True)
        report_path = os.path.join(rdir, f"build-{task_id}-{time.strftime('%Y%m%d-%H%M%S')}-{did}.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(f"# Làm {task_id} · {sid} · {started_at} → {finished_at}\n\n")
            cmd = ctx["cmd"]
            shown = " ".join(cmd[:3] + ["<prompt>"] + cmd[4:]) if len(cmd) > 3 else " ".join(cmd)
            f.write(f"- worktree: {wt}\n- nhánh: {branch}\n- Lệnh: {shown}\n- exit: {ctx['exit_code']}\n"
                    f"- Hồ sơ gán: {account} · hồ sơ chạy: {ctx['profile_used'] or '(không)'}\n\n## Kết quả\n\n{header}\n\n")
            shell = db.agy_shell_commands(ctx["sinfo"])
            if shell:
                f.write("## Lệnh shell agy đã gọi\n\n" + "\n".join(
                    f"- `{t['command'][:500]}`" + (" — BỊ CHẶN" if t.get("denied") else "")
                    + (f" — lỗi: {t['error'][:300]}" if t.get("error") else "") for t in shell) + "\n\n")
            if tests:
                f.write("## Kiểm của app\n\n" + f"- py_compile ({tests['py_compile']['files']} file): "
                        + ("ok" if tests["py_compile"]["ok"] else "LỖI\n```\n" + tests["py_compile"]["output"] + "\n```") + "\n")
                for t in tests["tests"]:
                    f.write(f"- {t['name']}: {'pass' if t['ok'] else 'FAIL rc=' + str(t['rc'])} ({t['sec']}s)\n")
                    if not t["ok"]:
                        f.write("```\n" + t["tail"] + "\n```\n")
                f.write("\n")
            f.write(f"## Output\n\n{ctx['output']}\n")
    except Exception as e:
        print(f"[build] Không ghi được báo cáo: {e}")
        report_path = ""

    reply_msg_id = None
    try:
        body = header + "\n\n" + (ctx["output"][:3000] if ctx["output"] else "(agy không trả output)") + f"\nexit={ctx['exit_code']}"
        with db.get_connection() as conn:
            cur = conn.execute("""
            INSERT INTO chat_messages (project_id, runtime_id, author, created_time, tag, body, react_json, reply_to, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (project_id, channel_id, sid, time.strftime("%H:%M:%S"), "Report", body,
                  json.dumps(["🛠 agy Làm"], ensure_ascii=False), row.get("request_msg_id"), db._now_iso()))
            reply_msg_id = cur.lastrowid
            conn.commit()
    except Exception as e:
        print(f"[build] Không ghi được tin war-room: {e}")

    viec_ref = db.get_task_viec_ref(task_id, project_id)
    webhook_sent = db.send_event_webhook("dispatch_finished", project_id=project_id, viec_ref=viec_ref, task_id=task_id,
                                         session_id=sid, exit_code=ctx["exit_code"], report_path=report_path, status=status)
    try:
        _set_row(did, status=status, exit_code=ctx["exit_code"], finished_at=finished_at, summary=summary, report_path=report_path,
                 reply_msg_id=reply_msg_id, viec_ref=viec_ref, webhook_sent=1 if webhook_sent else 0,
                 profile_initial=account, profile_used=ctx["profile_used"], fallback_reason=ctx["fb_note"],
                 profiles_tried=json.dumps(ctx["attempts"], ensure_ascii=False),
                 worktree_dir=wt, build_branch=branch, build_commit=bf["build_commit"],
                 build_tests=json.dumps(tests, ensure_ascii=False) if tests else "",
                 build_push=json.dumps(push, ensure_ascii=False) if push else "", compare_url=ctx["compare"],
                 pr_url=ctx["pr_url"])
        db.report_dispatch_to_task(did, project_id)
    finally:
        db._notify_dispatch_change()
    return {"dispatch_id": did, "status": status, "task_id": task_id, "session_id": sid, "branch": branch, "worktree_dir": wt,
            "commit": bf["build_commit"], "tests": tests, "push": push, "compare_url": ctx["compare"], "pr_url": ctx["pr_url"],
            "deps_changed": ctx.get("deps_changed", False),
            "error": ctx["fail"], "report_path": report_path, "elapsed_sec": round(time.time() - t0, 1)}
