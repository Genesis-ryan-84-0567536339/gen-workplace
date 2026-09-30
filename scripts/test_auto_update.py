#!/usr/bin/env python3
"""Kiểm thử tự cập nhật (backend/auto_update.py, #13). Chạy: python3 scripts/test_auto_update.py

Dựng repo "origin" (bare) + bản clone giả lập máy chủ trong thư mục tạm; backend/main.py giả
là 1 HTTP server nhỏ trả /api/status, để chạy thử (smoke_test) thật mà không đụng app thật.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_"):
        os.environ.pop(k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import auto_update as au

GOOD_MAIN = '''import os, json
from http.server import HTTPServer, BaseHTTPRequestHandler
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps({"status": "online", "v": VERSION}).encode())
    def log_message(self, *a): pass
VERSION = "%s"
HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''
CRASH_MAIN = 'raise SystemExit("khởi động hỏng")\n'
SYNTAX_MAIN = 'def (:\n'

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t"}
failed = 0
total = 0


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        raise RuntimeError(f"git {args}: {r.stderr}")
    return r.stdout.strip()


def check(name, cond, extra=""):
    global failed, total
    total += 1
    if not cond:
        failed += 1
        print(f"FAIL: {name} {extra}")


def push_commit(dev, content, msg):
    (dev / "backend" / "main.py").write_text(content)
    git(dev, "add", "-A")
    git(dev, "commit", "-qm", msg)
    git(dev, "push", "-q", "origin", "main")
    return git(dev, "rev-parse", "HEAD")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="gw-au-test-"))
    origin, dev, host, data = tmp / "origin.git", tmp / "dev", tmp / "host", tmp / "data"
    data.mkdir()
    git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp, "clone", "-q", str(origin), str(dev))
    git(dev, "checkout", "-q", "-B", "main")
    (dev / "backend").mkdir()
    (dev / ".gitignore").write_text("data/\n__pycache__/\n")
    v1 = push_commit(dev, GOOD_MAIN % "v1", "v1")
    git(tmp, "clone", "-q", "-b", "main", str(origin), str(host))

    restarts = []
    run = lambda **kw: au.run_cycle(host, data, restart=lambda: restarts.append(1), branch="main", **kw)

    # 1. Đã mới nhất
    r = run()
    check("up_to_date", r["action"] == "up_to_date", r)

    # 2. Có commit mới tốt -> cập nhật + restart + ghi log
    v2 = push_commit(dev, GOOD_MAIN % "v2", "v2 tốt")
    r = run()
    check("updated", r["action"] == "updated", r)
    check("HEAD lên v2", git(host, "rev-parse", "HEAD") == v2)
    check("đã gọi restart", len(restarts) == 1)
    check("log có updated", any(e["action"] == "updated" for e in au.read_log(data)))

    # 3. Commit mới khởi động hỏng -> quay về v2, không restart, lần sau bỏ qua
    v3 = push_commit(dev, CRASH_MAIN, "v3 hỏng khi chạy")
    r = run()
    check("rolled_back (crash)", r["action"] == "rolled_back", r)
    check("HEAD vẫn v2", git(host, "rev-parse", "HEAD") == v2)
    check("không restart khi hỏng", len(restarts) == 1)
    r = run()
    check("skip_bad lần sau", r["action"] == "skip_bad", r)

    # 4. Commit lỗi cú pháp -> py_compile bắt, quay về
    push_commit(dev, SYNTAX_MAIN, "v4 lỗi cú pháp")
    r = run()
    check("rolled_back (syntax)", r["action"] == "rolled_back" and "py_compile" in r["detail"], r)
    check("HEAD vẫn v2 (syntax)", git(host, "rev-parse", "HEAD") == v2)

    # 5. Thay đổi chưa commit trên máy chủ -> cất vào stash rồi cập nhật
    v5 = push_commit(dev, GOOD_MAIN % "v5", "v5 tốt")
    (host / "ghi-chu-local.txt").write_text("đừng xóa")
    r = run()
    check("updated khi bẩn", r["action"] == "updated", r)
    check("HEAD lên v5", git(host, "rev-parse", "HEAD") == v5)
    check("thay đổi local nằm trong stash", "auto-update" in git(host, "stash", "list"))

    # 6. Đang xem thử nhánh khác -> không kéo về main
    git(host, "checkout", "-q", "-b", "xem-thu-pr")
    push_commit(dev, GOOD_MAIN % "v6", "v6")
    r = run()
    check("other_branch", r["action"] == "other_branch", r)
    check("vẫn ở nhánh xem thử", git(host, "rev-parse", "--abbrev-ref", "HEAD") == "xem-thu-pr")
    git(host, "checkout", "-q", "main")

    # 7. Máy chủ có commit local mới hơn origin -> không đè
    git(host, "fetch", "-q", "origin", "main")
    git(host, "reset", "-q", "--hard", "origin/main")
    (host / "local.txt").write_text("x")
    git(host, "add", "-A")
    git(host, "commit", "-qm", "local")
    r = run()
    check("local_ahead", r["action"] == "local_ahead", r)

    # 8. Không phải repo git
    r = au.run_cycle(tmp / "data", data, branch="main")
    check("not_git", r["action"] == "not_git", r)

    # 9. Bật/tắt bằng biến môi trường
    os.environ["GW_AUTO_UPDATE"] = "0"
    check("GW_AUTO_UPDATE=0 tắt", not au.enabled() and au.start_worker(host, data) is None)
    os.environ.pop("GW_AUTO_UPDATE")
    check("mặc định bật", au.enabled())
    st = au.status(host, data)
    check("status có commit + history", st["commit"] and isinstance(st["history"], list), st)

    # 10. Còn việc đang chạy (agy build...) -> hoãn, không checkout / restart; hết việc -> cập nhật
    git(host, "checkout", "-q", "main")
    git(host, "reset", "-q", "--hard", "origin/main")
    before = git(host, "rev-parse", "HEAD")
    v10 = push_commit(dev, GOOD_MAIN % "v10", "v10 tốt")
    busy_items = [{"id": 7, "kind": "build", "session_id": "gw-backend-agy", "task_id": "TSK-9", "started_at": "2026-09-29 10:00:00"}]
    n_restart = len(restarts)
    n_log = len(au.read_log(data, 1000))
    r = run(busy=lambda: busy_items)
    check("deferred khi còn việc chạy", r["action"] == "deferred" and "dispatch:7 build gw-backend-agy TSK-9" in r["detail"], r)
    check("deferred: HEAD giữ nguyên, không restart", git(host, "rev-parse", "HEAD") == before and len(restarts) == n_restart)
    r2 = run(busy=lambda: busy_items)
    check("deferred lần 2 (thử lại chu kỳ sau)", r2["action"] == "deferred" and r2["deferred_since"] == r["deferred_since"], r2)
    log_new = au.read_log(data, 1000)[n_log:]
    check("log chỉ ghi 1 dòng deferred (không lặp mỗi chu kỳ)", [e["action"] for e in log_new] == ["deferred"], log_new)
    au.set_busy_checker(lambda: busy_items)
    st = au.status(host, data)
    check("status: max_defer_min + deferred + busy_now", st["max_defer_min"] == 60 and st["deferred"]
          and st["deferred"]["deferrals"] == 2 and st["deferred"]["busy"][0]["id"] == 7 and st["busy_now"] == busy_items, st)
    au.set_busy_checker(None)
    r = run(busy=lambda: [])
    check("hết việc chạy -> updated", r["action"] == "updated" and git(host, "rev-parse", "HEAD") == v10
          and len(restarts) == n_restart + 1 and not r.get("forced"), r)
    check("status: hết hoãn", au.status(host, data)["deferred"] is None)

    # 11. Dispatch treo quá GW_AUTO_UPDATE_MAX_DEFER_MIN -> vẫn cập nhật + log cảnh báo
    v11 = push_commit(dev, GOOD_MAIN % "v11", "v11 tốt")
    os.environ["GW_AUTO_UPDATE_MAX_DEFER_MIN"] = "30"
    r = run(busy=lambda: busy_items)
    check("max 30 phút: lần đầu vẫn hoãn", r["action"] == "deferred" and r["max_defer_min"] == 30, r)
    au._state["defer"]["since"] -= 31 * 60     # giả lập đã chờ 31 phút
    n_log = len(au.read_log(data, 1000))
    r = run(busy=lambda: busy_items)
    check("quá hạn hoãn -> updated (forced) dù còn việc chạy", r["action"] == "updated" and r.get("forced")
          and git(host, "rev-parse", "HEAD") == v11 and len(restarts) == n_restart + 2, r)
    check("chi tiết cập nhật có CẢNH BÁO + việc treo", "CẢNH BÁO" in r["detail"] and "dispatch:7" in r["detail"], r)
    log_new = [e["action"] for e in au.read_log(data, 1000)[n_log:]]
    check("log có defer_expired rồi updated", log_new == ["defer_expired", "updated"], log_new)
    check("hết hoãn sau khi cập nhật", au._state["defer"] is None)
    os.environ["GW_AUTO_UPDATE_MAX_DEFER_MIN"] = "abc"
    check("GW_AUTO_UPDATE_MAX_DEFER_MIN sai -> 60", au.max_defer_min() == 60)
    os.environ.pop("GW_AUTO_UPDATE_MAX_DEFER_MIN")

    # 12. Đã mới nhất thì không hoãn gì (không có bản mới để lên)
    r = run(busy=lambda: busy_items)
    check("up_to_date dù còn việc chạy", r["action"] == "up_to_date", r)

    # 13. db.running_dispatches_for_update: build / warroom / tmux tính, việc đã xong không tính
    os.environ["DATA_DIR"] = str(tmp / "dbdata")
    from backend import db
    ids = {k: db.start_dispatch_log("gw-backend-agy", kind=k, task_id="TSK-1") for k in ("build", "warroom", "tmux")}
    done = db.start_dispatch_log("gw-qa-agy", kind="build")
    with db.get_connection() as conn:
        conn.execute("UPDATE dispatch_log SET status = 'done' WHERE id = ?", (done,))
        conn.commit()
    got = db.running_dispatches_for_update()
    check("running_dispatches_for_update: build + warroom + tmux, bỏ done",
          sorted(x["kind"] for x in got) == ["build", "tmux", "warroom"] and all(x["id"] != done for x in got), got)

    print(f"{total - failed}/{total} test pass")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
