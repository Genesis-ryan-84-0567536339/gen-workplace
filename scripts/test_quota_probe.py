#!/usr/bin/env python3
"""
Test #6: quota hiển thị từ kết quả gọi agy thật. Chạy không cần server/agy/tmux:
GW_AGY_BIN trỏ tới script giả (lần 1 trả OK, lần 2 trả 429 RESOURCE_EXHAUSTED),
kiểm get_quota_telemetry ưu tiên dòng quota_probe mới nhất.
"""
import os
import sys
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-quota-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

AGY_OK = os.path.join(FAKEBIN, "agy-ok")
with open(AGY_OK, "w") as f:
    f.write("#!/bin/sh\necho \"OK từ agy giả: $*\"\nexit 0\n")
os.chmod(AGY_OK, 0o755)
AGY_429 = os.path.join(FAKEBIN, "agy-429")
with open(AGY_429, "w") as f:
    f.write("#!/bin/sh\necho 'Error: RESOURCE_EXHAUSTED: Individual quota reached. Resets in 2h13m' >&2\nexit 1\n")
os.chmod(AGY_429, 0o755)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db  # noqa: E402

# Không gọi mạng tới Cloud Code API trong test
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


REQUIRED_KEYS = ("status", "status_label", "percent", "reset_time", "detail")

print("[1] chưa có dòng probe → unknown, không có %")
g, c = db.get_quota_telemetry("profile1")
check("status unknown", g["status"] == "unknown" and c["status"] == "unknown", f"{g['status']}/{c['status']}")
check("percent None", g["percent"] is None and c["percent"] is None, f"{g['percent']}/{c['percent']}")
check("đủ key JSON cũ", all(k in g for k in REQUIRED_KEYS) and all(k in c for k in REQUIRED_KEYS))

print("[2] probe_quota với agy giả OK")
os.environ["GW_AGY_BIN"] = AGY_OK
res = db.probe_quota("profile1")
check("ok=True, exit 0", res["ok"] and res["exit_code"] == 0, str(res)[:200])
check("lệnh đúng dạng --gemini_dir/--mode plan/-p ping", "--gemini_dir=" in res["command"] and "--mode plan -p ping" in res["command"], res["command"])
check("output thật từ script giả", "OK từ agy giả" in res["output"], res["output"])
g, c = db.get_quota_telemetry("profile1")
check("gemini status ready (từ probe ok)", g["status"] == "ready" and g.get("source") == "agy_probe", f"{g['status']}/{g.get('source')}")
check("claude vẫn unknown (chưa gọi model claude)", c["status"] == "unknown")
with db.get_connection() as conn:
    n = conn.execute("SELECT count(*) FROM quota_probe WHERE profile_id='profile1' AND status='ok'").fetchone()[0]
check("quota_probe có 1 dòng ok", n == 1, str(n))

print("[3] probe_quota với agy giả 429")
os.environ["GW_AGY_BIN"] = AGY_429
res = db.probe_quota("profile1")
check("status rate_limited", res["status"] == "rate_limited", str(res)[:200])
check("reset_at trích từ output", res["reset_at"] == "2h13m", res["reset_at"])
g, c = db.get_quota_telemetry("profile1")
check("gemini status rate_limited", g["status"] == "rate_limited", g["status"])
check("status_label '429 · hồi 2h13m'", g["status_label"] == "429 · hồi 2h13m", g["status_label"])
check("reset_time = 2h13m", g["reset_time"] == "2h13m", g["reset_time"])
check("percent 0", g["percent"] == 0)

print("[4] dòng probe cũ > 6h bị bỏ qua")
with db.get_connection() as conn:
    conn.execute("UPDATE quota_probe SET checked_at = datetime('now', '-7 hours') WHERE profile_id='profile1'")
g, c = db.get_quota_telemetry("profile1")
check("về unknown", g["status"] == "unknown", g["status"])

print("[5] runner chat (call_agy_cli_turn) cũng ghi quota_probe")
os.environ["GW_AGY_BIN"] = AGY_429
with db.get_connection() as conn:
    conn.execute("INSERT OR IGNORE INTO gen_conversations (id, project_id, title) VALUES ('conv-test-quota', 'PRJ-GEN-WORKPLACE', 'test')")
    conn.commit()
reply, _, usage = db.call_agy_cli_turn("conv-test-quota", "xin chào", model="Claude Sonnet 4.6 (Thinking)", account="profile1")
check("runner trả lỗi quota", isinstance(usage, dict) and usage.get("error") == "RESOURCE_EXHAUSTED", str(usage))
with db.get_connection() as conn:
    r = conn.execute("SELECT model, status FROM quota_probe WHERE profile_id='profile1' ORDER BY id DESC LIMIT 1").fetchone()
check("quota_probe ghi model claude + rate_limited", r and "claude" in r["model"] and r["status"] == "rate_limited", dict(r) if r else "none")
g, c = db.get_quota_telemetry("profile1")
check("claude quota rate_limited, gemini unknown", c["status"] == "rate_limited" and g["status"] == "unknown", f"{c['status']}/{g['status']}")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)
