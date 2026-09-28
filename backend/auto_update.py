#!/usr/bin/env python3
"""
Tự đồng bộ app với GitHub (#13): Boss không phải chạy gw-update sau mỗi lần merge.

Mỗi GW_AUTO_UPDATE_SEC giây (mặc định 120) thread nền:
  1. git fetch origin <nhánh> (GW_AUTO_UPDATE_BRANCH, mặc định main).
  2. HEAD == origin/<nhánh> -> không làm gì.
     Đang đứng ở nhánh khác (Boss chạy `gw-update <nhánh>` để xem thử PR) -> bỏ qua.
     HEAD đã chứa origin/<nhánh> (có commit local mới hơn) -> bỏ qua.
  3. Cất thay đổi chưa commit vào git stash (không xóa), checkout origin/<nhánh>.
  4. Chạy thử bản mới trên cổng phụ với BẢN SAO DB; /api/status trả lời OK mới restart
     tiến trình bằng os.execv (giữ nguyên PID -> systemd/nohup đều ổn).
  5. Chạy thử hỏng -> quay về commit cũ, nhớ commit hỏng để không thử lại, ghi log.

Nhật ký: <DATA_DIR>/auto_update.log (JSON mỗi dòng). Tắt hẳn: GW_AUTO_UPDATE=0.
"""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

LOG_NAME = "auto_update.log"
_bad_shas = set()
_state = {"last_check": None, "last_result": None}


def enabled():
    return os.environ.get("GW_AUTO_UPDATE", "1").strip().lower() not in ("0", "false", "no", "off")


def interval_sec():
    try:
        return max(30, int(os.environ.get("GW_AUTO_UPDATE_SEC", "120")))
    except ValueError:
        return 120


def branch_name():
    return (os.environ.get("GW_AUTO_UPDATE_BRANCH") or "main").strip()


def _git(repo_dir, *args, timeout=60):
    cmd = ["git", "-c", "safe.directory=*", *args]
    try:
        return subprocess.run(cmd, cwd=str(repo_dir), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:  # không có git (Docker) hoặc treo mạng
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def current_commit(repo_dir):
    r = _git(repo_dir, "rev-parse", "--short", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def _log(data_dir, entry):
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    _state["last_result"] = entry
    print(f"[auto-update] {entry.get('action')}: {entry.get('detail', '')}", flush=True)
    try:
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        with open(Path(data_dir) / LOG_NAME, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def read_log(data_dir, limit=20):
    p = Path(data_dir) / LOG_NAME
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def status(repo_dir, data_dir):
    return {
        "enabled": enabled(),
        "branch": branch_name(),
        "interval_sec": interval_sec(),
        "commit": current_commit(repo_dir),
        "last_check": _state["last_check"],
        "last_result": _state["last_result"],
        "history": read_log(data_dir, 10),
    }


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def smoke_test(repo_dir, data_dir, timeout=30):
    """Chạy bản code hiện có trong repo_dir trên cổng phụ, DB là bản sao. Trả (ok, chi tiết)."""
    comp = subprocess.run([sys.executable, "-m", "py_compile", *[str(p) for p in Path(repo_dir, "backend").glob("*.py")]],
                          capture_output=True, text=True, timeout=120)
    if comp.returncode != 0:
        return False, "py_compile lỗi: " + (comp.stderr or comp.stdout).strip()[-400:]

    tmp = tempfile.mkdtemp(prefix="gw-smoke-")
    proc = None
    try:
        src_db = Path(data_dir) / "gen-workplace.db"
        if src_db.exists():
            with sqlite3.connect(str(src_db), timeout=15) as src, sqlite3.connect(str(Path(tmp) / "gen-workplace.db")) as dst:
                src.backup(dst)
        port = _free_port()
        env = {**os.environ, "PORT": str(port), "DATA_DIR": tmp, "GW_AUTO_UPDATE": "0",
               "GW_RECLAIM_INTERVAL_SEC": "86400", "GW_EVENT_WEBHOOK_URL": ""}
        log_path = Path(tmp) / "smoke.log"
        with open(log_path, "w") as logf:
            proc = subprocess.Popen([sys.executable, str(Path(repo_dir) / "backend" / "main.py")], cwd=str(repo_dir),
                                    env=env, stdout=logf, stderr=subprocess.STDOUT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
                    if r.status == 200 and json.loads(r.read().decode()).get("status") == "online":
                        return True, f"bản mới chạy thử OK trên cổng {port}"
            except Exception:
                pass
            time.sleep(0.5)
        tail = log_path.read_text(errors="replace")[-600:] if log_path.exists() else ""
        if proc.poll() is not None:
            return False, f"bản mới thoát ngay khi khởi động (exit={proc.returncode}): {tail.strip()}"
        return False, f"bản mới không trả lời /api/status sau {timeout}s: {tail.strip()}"
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


def run_cycle(repo_dir, data_dir, restart=None, branch=None, smoke=smoke_test):
    """Một lượt kiểm tra. Trả dict {action, detail, ...}; action=updated thì đã gọi restart()."""
    branch = branch or branch_name()
    _state["last_check"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    if _git(repo_dir, "rev-parse", "--git-dir").returncode != 0:
        return {"action": "not_git", "detail": f"{repo_dir} không phải repo git"}
    cur_branch = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if cur_branch != branch:
        return {"action": "other_branch", "detail": f"đang ở nhánh '{cur_branch}', chỉ tự cập nhật khi ở '{branch}'"}

    f = _git(repo_dir, "fetch", "-q", "origin", branch, timeout=120)
    if f.returncode != 0:
        return {"action": "fetch_failed", "detail": f.stderr.strip()[-300:]}
    head = _git(repo_dir, "rev-parse", "HEAD").stdout.strip()
    target = _git(repo_dir, "rev-parse", f"origin/{branch}").stdout.strip()
    if head == target:
        return {"action": "up_to_date", "detail": head[:7]}
    if target in _bad_shas:
        return {"action": "skip_bad", "detail": f"{target[:7]} đã chạy thử hỏng, chờ commit mới"}
    if _git(repo_dir, "merge-base", "--is-ancestor", target, head).returncode == 0:
        return {"action": "local_ahead", "detail": f"HEAD {head[:7]} đã chứa origin/{branch} {target[:7]}"}

    stash_note = ""
    if _git(repo_dir, "status", "--porcelain").stdout.strip():
        label = f"auto-update {time.strftime('%Y%m%d-%H%M%S')} (trước khi lên {target[:7]})"
        s = _git(repo_dir, "stash", "push", "-u", "-m", label)
        if s.returncode != 0:
            return _log(data_dir, {"action": "stash_failed", "detail": s.stderr.strip()[-300:]})
        stash_note = f"; đã cất thay đổi chưa commit vào stash '{label}'"

    c = _git(repo_dir, "checkout", "-q", "-B", branch, f"origin/{branch}")
    if c.returncode != 0:
        return _log(data_dir, {"action": "checkout_failed", "detail": c.stderr.strip()[-300:] + stash_note})

    ok, detail = smoke(repo_dir, data_dir)
    if not ok:
        _git(repo_dir, "checkout", "-q", "-B", branch, head)
        _bad_shas.add(target)
        return _log(data_dir, {"action": "rolled_back", "from": head[:7], "to": target[:7],
                               "detail": f"giữ {head[:7]}; {detail}{stash_note}"})

    subject = _git(repo_dir, "log", "-1", "--pretty=%s").stdout.strip()
    entry = _log(data_dir, {"action": "updated", "from": head[:7], "to": target[:7],
                            "detail": f"{subject} · {detail}{stash_note}"})
    if restart:
        restart()
    return entry


def restart_process():
    """Thay tiến trình hiện tại bằng bản mới (giữ PID, cwd, argv -> gw-update/pgrep vẫn nhận ra)."""
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable] + sys.argv)


def start_worker(repo_dir, data_dir):
    if not enabled():
        print("  Tự cập nhật: TẮT (GW_AUTO_UPDATE=0)")
        return None
    interval = interval_sec()

    def _loop():
        time.sleep(min(30, interval))
        while True:
            try:
                res = run_cycle(repo_dir, data_dir, restart=restart_process)
                _state["last_result"] = res if "ts" in res else {**res, "ts": _state["last_check"]}
            except Exception as e:
                _log(data_dir, {"action": "error", "detail": str(e)[-300:]})
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="AutoUpdateWorker")
    t.start()
    print(f"  Tự cập nhật: mỗi {interval}s từ origin/{branch_name()} (tắt: GW_AUTO_UPDATE=0)")
    return t
