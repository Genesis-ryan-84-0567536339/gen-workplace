#!/usr/bin/env python3
"""
Sandbox chạy test của chế độ "Làm" (Issue #54): lớp Python quanh scripts/gw-sandbox-run (bản của APP, không bao giờ bản
nằm trong worktree do agy sửa được).

- run(worktree, argv, timeout_sec): chạy 1 lệnh (py_compile / scripts/test_*.py) trong sandbox, trả rc, output, cơ chế
  (bwrap | podman | none-unsandboxed), trạng thái (ok | sandbox_unavailable | bad_args | timeout | error).
- probe(): cơ chế khả dụng + phiên bản (gw-sandbox-run --probe).
- self_test(): tự kiểm thật trong sandbox: GHI được worktree; KHÔNG đọc được $HOME/.ssh, hồ sơ agy, DB app; KHÔNG có mạng
  (kể cả API của chính app trên 127.0.0.1); KHÔNG ghi được ngoài worktree (kiểm lại từ bên ngoài sau khi chạy).
- status(refresh): probe + self_test, cache 60s — cho GET /api/sandbox/status.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

try:
    from backend import db
except ImportError:
    import db

EXIT_UNAVAILABLE = 97
EXIT_BADARG = 98
EXIT_DRYRUN = 99
INSTALL_HINT = "sudo dnf install -y bubblewrap"
_HEADER_RE = re.compile(r"^\[gw-sandbox\] mechanism=(\S+)(?: version=(\S*))?.*$", re.M)
_TAG_LINE_RE = re.compile(r"^\[gw-sandbox\] mechanism=.*\n?", re.M)
# Biến môi trường app chuyển cho wrapper (wrapper tự --clearenv trong sandbox; HOME / XDG_RUNTIME_DIR chỉ để podman rootless
# tìm kho image của user, KHÔNG vào sandbox)
_PASS_ENV = ("HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "GW_DISPATCH_REPO", "GW_SANDBOX_MECHANISMS", "GW_SANDBOX_IMAGE",
             "GW_SANDBOX_NPROC", "GW_SANDBOX_MEM_MB", "GW_ALLOW_UNSANDBOXED_TESTS")
# Không bao giờ chuyển cho tiến trình agy: agy chỉ được chạy test TRONG sandbox (không mở đường chạy ngoài / chạy giả)
AGY_STRIP_ENV = ("GW_ALLOW_UNSANDBOXED_TESTS", "GW_SANDBOX_DRY_RUN")


def wrapper_path():
    """Đường dẫn thật của gw-sandbox-run trong repo APP (BASE_DIR), không phải bản trong worktree của task."""
    return os.path.realpath(os.path.join(str(db.BASE_DIR), "scripts", "gw-sandbox-run"))


def wrapper_env(timeout_sec=None):
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
           "GW_WORKTREE_ROOT": db._worktree_root()}
    for k in _PASS_ENV:
        v = os.environ.get(k)
        if v:
            env[k] = v
    if timeout_sec is not None:
        env["GW_SANDBOX_TIMEOUT_SEC"] = str(max(1, min(3600, int(timeout_sec))))
    return env


def agy_env(env):
    """Bỏ biến cho phép chạy ngoài sandbox / chạy giả khỏi môi trường của tiến trình agy."""
    return {k: v for k, v in env.items() if k not in AGY_STRIP_ENV}


def parse_header(text):
    m = _HEADER_RE.search(text or "")
    return {"mechanism": m.group(1), "version": m.group(2) or ""} if m else {"mechanism": "", "version": ""}


def describe(sb):
    """Chuỗi ngắn cho dispatch_log / tin: 'bwrap 0.9.0' | 'sandbox_unavailable' | 'none-unsandboxed (…)'."""
    if not sb:
        return ""
    if sb.get("status") == "sandbox_unavailable":
        return "sandbox_unavailable"
    mech = sb.get("mechanism") or "?"
    if mech == "none-unsandboxed":
        return "none-unsandboxed (GW_ALLOW_UNSANDBOXED_TESTS=1, chạy NGOÀI sandbox)"
    return f"{mech} {sb.get('version') or ''}".strip()


def run(worktree, argv, timeout_sec=600):
    """
    Chạy argv trong sandbox với cwd = worktree (đường dẫn thật). Không ném lỗi. Trả {rc, output (đã bỏ dòng tiêu đề của
    wrapper), mechanism, version, status, sec, hint?}.
    """
    t0 = time.time()
    wt = os.path.realpath(str(worktree))
    out = {"rc": -1, "output": "", "mechanism": "", "version": "", "status": "error", "sec": 0.0}
    w = wrapper_path()
    if not os.path.isfile(w) or not os.access(w, os.X_OK):
        out["output"] = f"thiếu wrapper sandbox {w} (hoặc chưa có quyền chạy)"
        return out
    try:
        p = subprocess.run([w, wt, "--"] + list(argv), cwd=wt, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           errors="replace", env=wrapper_env(timeout_sec), timeout=int(timeout_sec) + 30)
        rc, text = p.returncode, (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
    except subprocess.TimeoutExpired as e:
        rc, text = 124, f"quá {int(timeout_sec) + 30}s (wrapper không tự dừng) {e}"
    except OSError as e:
        out["output"] = f"không chạy được wrapper sandbox: {e}"
        out["sec"] = round(time.time() - t0, 1)
        return out
    out.update(parse_header(text))
    out["rc"] = rc
    out["output"] = _TAG_LINE_RE.sub("", text).strip()
    if rc == EXIT_UNAVAILABLE and "sandbox_unavailable" in text:
        out.update(status="sandbox_unavailable", mechanism="none",
                   hint=f"Cài bubblewrap: {INSTALL_HINT} (hoặc podman rootless + image). Test KHÔNG chạy ngoài sandbox.")
    elif rc == EXIT_BADARG:
        out["status"] = "bad_args"
    elif rc in (124, 137) and "[gw-sandbox] quá thời gian" in text:
        out["status"] = "timeout"
    elif out["mechanism"]:
        out["status"] = "ok"
    out["sec"] = round(time.time() - t0, 1)
    return out


def probe():
    w = wrapper_path()
    try:
        p = subprocess.run([w, "--probe"], capture_output=True, text=True, timeout=90, env=wrapper_env(),
                           stdin=subprocess.DEVNULL)
        data = json.loads(p.stdout or "{}")
        if isinstance(data, dict) and "mechanism" in data:
            return data
        return {"available": False, "mechanism": "none", "status": "error", "wrapper": w,
                "hint": ((p.stderr or "") + (p.stdout or "")).strip()[-300:]}
    except Exception as e:
        return {"available": False, "mechanism": "none", "status": "error", "wrapper": w, "hint": f"{type(e).__name__}: {e}"}


# Chương trình chạy TRONG sandbox khi tự kiểm (cũng là mẫu cho test tự tấn công scripts/test_gw_sandbox.py).
_PROBE_PY = r'''
import json, os, socket, sys
home, data_dir, port, token = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
outs = json.loads(sys.argv[5])
res = {}
try:
    with open("gw-sandbox-selftest-ok.txt", "w") as f:
        f.write(token)
    res["write_worktree"] = True
except Exception as e:
    res["write_worktree"] = False
    res["write_worktree_err"] = repr(e)
seen = []
for d in (os.path.join(home, ".ssh"), os.path.join(home, ".agy-profiles"), os.path.join(home, ".gemini")):
    try:
        names = os.listdir(d)
        seen.append(d)
        for n in names:
            try:
                open(os.path.join(d, n), "rb").read(16)
                seen.append(os.path.join(d, n))
            except Exception:
                pass
    except Exception:
        pass
for n in ("id_rsa", "id_ed25519", "id_ecdsa"):
    try:
        open(os.path.join(home, ".ssh", n), "rb").read(16)
        seen.append(n)
    except Exception:
        pass
res["read_ssh"] = [s for s in seen if "/.ssh" in s or s.startswith("id_")]
res["read_agy_profiles"] = [s for s in seen if ".agy-profiles" in s or ".gemini" in s]
try:
    open(os.path.join(data_dir, "gen-workplace.db"), "rb").read(16)
    res["read_db"] = True
except Exception:
    res["read_db"] = False
net = []
for host, p in (("1.1.1.1", 53), ("8.8.8.8", 443), ("172.17.0.1", 8888), ("127.0.0.1", port)):
    try:
        s = socket.create_connection((host, p), timeout=2)
        s.close()
        net.append(f"{host}:{p}")
    except Exception:
        pass
try:
    socket.getaddrinfo("github.com", 443)
    net.append("dns:github.com")
except Exception:
    pass
res["net_ok"] = net
try:
    res["ifaces"] = [n for _, n in socket.if_nameindex()]
except Exception:
    res["ifaces"] = []
wrote = []
for p in outs:
    try:
        with open(p, "w") as f:
            f.write(token)
        wrote.append(p)
    except Exception:
        pass
res["wrote_inside"] = wrote
print("GWSBX-RESULT " + json.dumps(res))
'''


def self_test():
    """Tự kiểm thật (xem docstring của module). Trả {ok, mechanism, version, checks: [{name, ok, detail}], sec, error?}."""
    t0 = time.time()
    root = db._worktree_root()
    out = {"ok": False, "mechanism": "", "version": "", "checks": [], "sec": 0.0}
    d = ""
    try:
        os.makedirs(root, exist_ok=True)
        d = os.path.realpath(tempfile.mkdtemp(prefix="gw-sandbox-selftest-", dir=root))
        token = os.urandom(8).hex()
        home = os.path.realpath(db.HOME_DIR)
        data_dir = os.path.realpath(str(db.DATA_DIR))
        outside = [os.path.join(os.path.dirname(d), f"gw-sandbox-outside-{token}"),
                   os.path.join(home, f"gw-sandbox-outside-{token}"),
                   os.path.join(data_dir, f"gw-sandbox-outside-{token}"),
                   os.path.join(tempfile.gettempdir(), f"gw-sandbox-outside-{token}"),
                   os.path.join(str(db.BASE_DIR), f"gw-sandbox-outside-{token}")]
        with open(os.path.join(d, "probe.py"), "w", encoding="utf-8") as f:
            f.write(_PROBE_PY)
        port = int(os.environ.get("PORT", "8888") or 8888)
        r = run(d, ["python3", "probe.py", home, data_dir, str(port), token, json.dumps(outside)], timeout_sec=60)
        out["mechanism"], out["version"] = r.get("mechanism") or "", r.get("version") or ""
        if r["status"] == "sandbox_unavailable":
            out.update(error="sandbox_unavailable", hint=r.get("hint") or "")
            return out
        m = re.search(r"^GWSBX-RESULT (.*)$", r.get("output") or "", re.M)
        if not m:
            out["error"] = f"tự kiểm không ra kết quả (rc={r['rc']}): {(r.get('output') or '')[-300:]}"
            return out
        res = json.loads(m.group(1))
        try:
            wt_ok = open(os.path.join(d, "gw-sandbox-selftest-ok.txt"), encoding="utf-8").read() == token
        except OSError:
            wt_ok = False
        leaked = [p for p in outside if os.path.exists(p)]
        for p in leaked:
            try:
                os.remove(p)
            except OSError:
                pass
        ifaces = [x for x in res.get("ifaces") or [] if x != "lo"]
        checks = [
            ("ghi được worktree", bool(res.get("write_worktree")) and wt_ok, res.get("write_worktree_err") or ""),
            ("không đọc được $HOME/.ssh", not res.get("read_ssh"), ", ".join(res.get("read_ssh") or [])),
            ("không đọc được hồ sơ agy (~/.agy-profiles, ~/.gemini)", not res.get("read_agy_profiles"),
             ", ".join(res.get("read_agy_profiles") or [])),
            ("không đọc được DB app", not res.get("read_db"), ""),
            ("không có mạng (kể cả API app ở 127.0.0.1)", not res.get("net_ok") and not ifaces,
             ", ".join((res.get("net_ok") or []) + ifaces)),
            ("không ghi được ngoài worktree", not leaked, ", ".join(leaked)),
        ]
        out["checks"] = [{"name": n, "ok": bool(ok), "detail": det} for n, ok, det in checks]
        out["ok"] = r["status"] == "ok" and all(c["ok"] for c in out["checks"])
        if out["mechanism"] == "none-unsandboxed":
            out["ok"] = False
            out["error"] = "đang chạy NGOÀI sandbox (GW_ALLOW_UNSANDBOXED_TESTS=1)"
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    finally:
        if d:
            shutil.rmtree(d, ignore_errors=True)
        out["sec"] = round(time.time() - t0, 1)


_CACHE = {"at": 0.0, "data": None}
_LOCK = threading.Lock()
CACHE_SEC = 60


def status(refresh=False):
    """GET /api/sandbox/status: probe + tự kiểm (cache CACHE_SEC giây, refresh=True để kiểm lại)."""
    with _LOCK:
        if not refresh and _CACHE["data"] and time.time() - _CACHE["at"] < CACHE_SEC:
            return {**_CACHE["data"], "cached": True}
        pr = probe()
        st = self_test() if pr.get("available") or os.environ.get("GW_ALLOW_UNSANDBOXED_TESTS") == "1" else None
        data = {"available": bool(pr.get("available")), "mechanism": pr.get("mechanism") or "none",
                "version": pr.get("version") or "", "status": pr.get("status") or "", "hint": pr.get("hint") or "",
                "allow_unsandboxed": bool(pr.get("allow_unsandboxed")), "install_hint": INSTALL_HINT,
                "wrapper": pr.get("wrapper") or wrapper_path(), "worktree_root": db._worktree_root(),
                "bwrap": pr.get("bwrap") or {}, "podman": pr.get("podman") or {}, "limits": pr.get("limits") or {},
                "self_test": st, "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        data["ok"] = bool(data["available"] and st and st.get("ok"))
        _CACHE.update(at=time.time(), data=data)
        return {**data, "cached": False}
