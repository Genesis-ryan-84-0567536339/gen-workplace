#!/usr/bin/env python3
"""
Test #22: tự chuyển tài khoản agy khi hồ sơ của vai hết quota. Chạy không cần server/agy thật:
HOME tạm có 5 hồ sơ ~/.agy-profiles/profile1..5 đã "đăng nhập" (token giả có email), GW_AGY_BIN là agy giả:
hồ sơ có tên trong file EXHAUSTED → in 429 RESOURCE_EXHAUSTED "Resets in <giờ riêng của hồ sơ>" và thoát 1, hồ sơ khác → OK.
Kiểm:
- đọc "Resets in XhYm" → reset_at tuyệt đối; exit 0 mà chỉ in lỗi quota vẫn là hết quota.
- war-room @devops (profile3 hết quota) → tự chạy lại bằng hồ sơ khác, done; dispatch_log ghi hồ sơ ban đầu / đã dùng / lý do;
  wait_worker_result + tin war-room nói "đã chuyển từ profile3 (hết quota, có lại lúc …) sang profileX"; allow-rule có ở hồ sơ mới;
  gán tài khoản của vai không đổi.
- lần sau: profile3 đang exhausted → bỏ qua, không gọi agy với profile3.
- mọi hồ sơ hết quota → failed "tất cả tài khoản hết quota, sớm nhất có lại lúc … (profileN)", số lần gọi ≤ số hồ sơ.
- chat Gen (call_agy_cli_turn / send_gen_chat) cũng tự chuyển, không mang --conversation của tài khoản cũ.
- API worker / hồ sơ / quota trả exhausted, reset_at, hồ sơ đang dùng thực tế.
- giao task qua tmux (swarm dispatch): bỏ qua hồ sơ exhausted trước khi gõ lệnh; hết quota giữa chừng → gõ lại với hồ sơ khác → done.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

for k in list(os.environ):
    if k.startswith("GIT_CONFIG_") or k.startswith("GIT_AUTHOR_") or k.startswith("GIT_COMMITTER_"):
        os.environ.pop(k, None)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-quota-fb-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
HOME = os.path.join(TMP, "home")
EXHAUSTED_FILE = os.path.join(TMP, "exhausted.txt")
CALLS_FILE = os.path.join(TMP, "calls.log")
RESETS = {"profile1": "5h", "profile2": "2h30m", "profile3": "76h11m", "profile4": "1h5m", "profile5": "3h"}

# agy giả: tên hồ sơ lấy từ --gemini_dir=<dir> hoặc ANTIGRAVITY_APP_DATA_DIR=<dir>/antigravity-cli
AGY = os.path.join(FAKEBIN, "agy")
with open(AGY, "w") as f:
    f.write(f"""#!/bin/bash
prof=""
json=0
prev=""
for a in "$@"; do
  case "$a" in
    --gemini_dir=*) prof="$(basename "${{a#--gemini_dir=}}")" ;;
  esac
  if [ "$prev" = "--output-format" ]; then [ "$a" = "stream-json" ] && json=2 || json=1; fi
  prev="$a"
done
if [ -z "$prof" ] && [ -n "$ANTIGRAVITY_APP_DATA_DIR" ]; then prof="$(basename "$(dirname "$ANTIGRAVITY_APP_DATA_DIR")")"; fi
[ -z "$prof" ] && prof="owner_default"
printf '%s\n' "$prof|$(printf '%s ' "$@" | tr '\n' ' ')" >> '{CALLS_FILE}'
if grep -qx "$prof" '{EXHAUSTED_FILE}' 2>/dev/null; then
  case "$prof" in
    profile1) r=5h ;; profile2) r=2h30m ;; profile3) r=76h11m ;; profile4) r=1h5m ;; profile5) r=3h ;; *) r=1h ;;
  esac
  echo "Error: 429 RESOURCE_EXHAUSTED \\"Individual quota reached. Resets in $r\\"" >&2
  exit 1
fi
if [ "$json" = 2 ]; then
  echo "{{\"event\":\"init\",\"init\":{{\"cwd\":\"$(pwd)\"}}}}"
  echo "{{\"event\":\"result\",\"result\":{{\"status\":\"SUCCESS\",\"response\":\"OK từ $prof\\ncwd=$(pwd)\"}}}}"
elif [ "$json" = 1 ]; then
  echo "{{\\"response\\": \\"OK từ $prof\\", \\"conversation_id\\": \\"agy-conv-$prof\\", \\"usage\\": {{\\"total_tokens\\": 7}}}}"
else
  echo "OK từ $prof"; echo "cwd=$(pwd)"
fi
exit 0
""")
os.chmod(AGY, 0o755)

DISPATCH_REPO = os.path.join(TMP, "repo")
os.makedirs(DISPATCH_REPO)
GIT = ["git", "-C", DISPATCH_REPO, "-c", "user.name=test", "-c", "user.email=test@example.com"]
subprocess.run(GIT + ["init", "-q"], check=True)
with open(os.path.join(DISPATCH_REPO, "README.md"), "w") as f:
    f.write("repo tạm\n")
subprocess.run(GIT + ["add", "."], check=True)
subprocess.run(GIT + ["commit", "-q", "-m", "init"], check=True)


def fake_id_token(email):
    payload = base64.urlsafe_b64encode(json.dumps({"email": email, "name": email.split("@")[0], "exp": 0}).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{payload}.sig"


for i in range(1, 6):
    cli = os.path.join(HOME, ".agy-profiles", f"profile{i}", "antigravity-cli")
    os.makedirs(cli)
    with open(os.path.join(cli, "antigravity-oauth-token"), "w") as f:
        json.dump({"token": {"access_token": "x", "refresh_token": "y"}, "id_token": fake_id_token(f"acc{i}@example.com")}, f)
    with open(os.path.join(cli, "settings.json"), "w") as f:
        json.dump({"permissions": {"allow": ["command(make test)"] if i == 3 else []}}, f)
os.makedirs(os.path.join(HOME, "gw-reports"))

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = HOME
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.environ["GW_AGY_BIN"] = AGY
os.environ["GW_DISPATCH_REPO"] = DISPATCH_REPO
os.environ["GW_WORKTREE_ROOT"] = os.path.join(TMP, "gw-worktrees")
os.environ["TMUX_TMPDIR"] = os.path.join(TMP, "tmux")   # tmux thật nhưng socket riêng, không đụng phiên thật
os.makedirs(os.environ["TMUX_TMPDIR"])
os.environ.pop("TMUX", None)
os.environ.pop("GW_DIRECTIVE_ALLOW_ALL", None)
os.environ.pop("GW_EVENT_WEBHOOK_URL", None)
os.makedirs(os.environ["DATA_DIR"])
sys.path.insert(0, ROOT)

from backend import db  # noqa: E402

db.fetch_live_google_quota = lambda profile_id="owner_default", force=False: None
db.init_db()

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


def set_exhausted(*profiles):
    with open(EXHAUSTED_FILE, "w") as f:
        f.write("".join(p + "\n" for p in profiles))


def calls():
    try:
        with open(CALLS_FILE) as f:
            return [ln.rstrip("\n").split("|", 1) for ln in f if ln.strip()]
    except FileNotFoundError:
        return []


def reset_calls():
    open(CALLS_FILE, "w").close()


def clear_states():
    with db.get_connection() as conn:
        conn.execute("DELETE FROM profile_quota_state")
        conn.commit()


def near(iso, delta, tol=120):
    dt = datetime.fromisoformat(iso)
    return abs((dt - (datetime.now().astimezone() + delta)).total_seconds()) < tol


def account_of(sid):
    with db.get_connection() as conn:
        return conn.execute("SELECT account_type FROM tmux_sessions WHERE id = ?", (sid,)).fetchone()["account_type"]


print("[1] đọc giờ hồi quota + nhận diện hết quota")
now = datetime.now().astimezone()
check("'76h11m' → +76h11m", db.parse_quota_reset("76h11m", now) == now + timedelta(hours=76, minutes=11))
check("'1h5m' / '45m' / '2d3h' / '30s'", db.parse_quota_reset("1h5m", now) == now + timedelta(hours=1, minutes=5)
      and db.parse_quota_reset("45m", now) == now + timedelta(minutes=45)
      and db.parse_quota_reset("2d3h", now) == now + timedelta(days=2, hours=3)
      and db.parse_quota_reset("30s", now) == now + timedelta(seconds=30))
check("chuỗi không đọc được → None", db.parse_quota_reset("sớm thôi", now) is None and db.parse_quota_reset("", now) is None)
check("exit 1 + RESOURCE_EXHAUSTED → rate_limited + 76h11m",
      db.classify_agy_result(1, 'Error: 429 RESOURCE_EXHAUSTED "Individual quota reached. Resets in 76h11m"') == ("rate_limited", "76h11m"))
check("exit 0 nhưng chỉ in lỗi quota → rate_limited",
      db.classify_agy_result(0, "Individual quota reached. Resets in 2h")[0] == "rate_limited")
check("exit 0 với trả lời dài có nhắc RESOURCE_EXHAUSTED → ok", db.classify_agy_result(0, "Giải thích: " + "x" * 800 + " RESOURCE_EXHAUSTED")[0] == "ok")

print("[2] war-room @devops: profile3 hết quota → tự chuyển hồ sơ khác, done")
set_exhausted("profile3")
reset_calls()
check("vai devops gán profile3", account_of("gw-devops-agy") == "profile3", account_of("gw-devops-agy"))
res = db.post_warroom_message(message="@devops [đọc] kiểm tra Dockerfile", author="Ryan (Owner)", wait=True)
did = res["dispatches"][0]["dispatch_id"]
w = db.wait_worker_result(dispatch_id=did, timeout_sec=5)
check("wait_worker_result done exit 0", w["status"] == "done" and w["exit_code"] == 0, str(w)[:300])
check("profile_initial = profile3, profile_used khác profile3", w["profile_initial"] == "profile3" and w["profile_used"].startswith("profile")
      and w["profile_used"] != "profile3" and w["fallback"] is True, str(w)[:300])
used = w["profile_used"]
check("fallback_reason: đã chuyển từ profile3 (hết quota, có lại lúc …) sang profileX",
      w["fallback_reason"].startswith("đã chuyển từ profile3 (hết quota, có lại lúc ") and w["fallback_reason"].endswith(f"sang {used}"), w["fallback_reason"])
check("summary của wait_worker_result nêu việc chuyển tài khoản", "[Tài khoản] đã chuyển từ profile3" in w["summary"] and f"OK từ {used}" in w["summary"], w["summary"][:300])
c = calls()
check("gọi agy 2 lần: profile3 rồi hồ sơ mới", [x[0] for x in c] == ["profile3", used], str([x[0] for x in c]))
wt = os.path.realpath(os.path.join(os.environ["GW_WORKTREE_ROOT"], "gw-devops-agy"))
check("lần chạy lại: đúng lệnh (--mode plan -p, cùng prompt), --gemini_dir của hồ sơ mới",
      c[1][1].startswith(f"--gemini_dir={os.path.join(HOME, '.agy-profiles', used)} --mode plan -p @devops kiểm tra Dockerfile")
      and c[0][1].split("--mode", 1)[1] == c[1][1].split("--mode", 1)[1], str(c)[:400])
with db.get_connection() as conn:
    reply = conn.execute("SELECT body FROM chat_messages WHERE id = ?", (w["reply_msg_id"],)).fetchone()["body"]
    dl = dict(conn.execute("SELECT * FROM dispatch_log WHERE id = ?", (did,)).fetchone())
check("tin war-room có dòng [Tài khoản] đã chuyển … + output thật + exit=0",
      "[Tài khoản] đã chuyển từ profile3 (hết quota, có lại lúc " in reply and f"OK từ {used}" in reply and "exit=0" in reply, reply[:300])
check("chạy trong worktree của vai", f"cwd={wt}" in reply, reply[:300])
check("dispatch_log: profile_initial/profile_used/fallback_reason/profiles_tried",
      dl["profile_initial"] == "profile3" and dl["profile_used"] == used and dl["fallback_reason"] == w["fallback_reason"]
      and [a["profile"] for a in json.loads(dl["profiles_tried"])] == ["profile3", used], str(dl)[:400])
st3 = db.get_profile_quota_state("profile3")
check("profile3 exhausted, reset_at ≈ bây giờ + 76h11m", st3["exhausted"] and near(st3["reset_at"], timedelta(hours=76, minutes=11)), str(st3))
with open(os.path.join(HOME, ".agy-profiles", used, "antigravity-cli", "settings.json")) as f:
    allow = json.load(f)["permissions"]["allow"]
check("hồ sơ mới có allow-rule read_file(worktree) + lệnh chỉ đọc + rule riêng của profile3",
      f"read_file({wt})" in allow and "command(git log)" in allow and "command(make test)" in allow, str(allow)[:300])
check("gán tài khoản cố định của vai không đổi", account_of("gw-devops-agy") == "profile3")

print("[3] lần sau: profile3 đang exhausted → bỏ qua, không gọi agy với profile3")
reset_calls()
res = db.post_warroom_message(message="@devops đọc docker-compose.yml", author="Ryan (Owner)", wait=True)
w2 = db.wait_worker_result(dispatch_id=res["dispatches"][0]["dispatch_id"], timeout_sec=5)
c = calls()
check("không có lần gọi nào với profile3, chỉ 1 lần gọi", len(c) == 1 and c[0][0] != "profile3", str([x[0] for x in c]))
check("done + lý do ghi 'bỏ qua, không gọi'", w2["status"] == "done" and "bỏ qua, không gọi" in w2["fallback_reason"]
      and w2["profile_used"] == c[0][0], str(w2)[:300])
check("chọn hồ sơ dùng ít gần nhất (khác hồ sơ vừa dùng ở lần trước)", w2["profile_used"] != used, f"{w2['profile_used']} vs {used}")

print("[4] mọi hồ sơ hết quota → failed, nêu thời điểm có lại sớm nhất")
set_exhausted("profile1", "profile2", "profile3", "profile4", "profile5")
reset_calls()
res = db.post_warroom_message(message="@backend kiểm tra API", author="Ryan (Owner)", wait=True)
w3 = db.wait_worker_result(dispatch_id=res["dispatches"][0]["dispatch_id"], timeout_sec=5)
c = calls()
check("failed", w3["status"] == "failed", str(w3)[:300])
check("gọi tối đa số hồ sơ khả dụng (4, profile3 bỏ qua từ trước), không gọi lại hồ sơ nào", len(c) == 4 and "profile3" not in [x[0] for x in c]
      and len({x[0] for x in c}) == 4, str([x[0] for x in c]))
st4 = db.get_profile_quota_state("profile4")
check("lý do: tất cả tài khoản hết quota, sớm nhất có lại lúc … (profile4, 1h5m)",
      w3["fallback_reason"].startswith(f"tất cả tài khoản hết quota, sớm nhất có lại lúc {db.format_reset_time(st4['reset_at'])} (profile4)")
      and near(st4["reset_at"], timedelta(hours=1, minutes=5)), w3["fallback_reason"])
check("summary failed chứa lý do", "tất cả tài khoản hết quota, sớm nhất có lại lúc" in w3["summary"], w3["summary"][:300])
with db.get_connection() as conn:
    reply = conn.execute("SELECT body FROM chat_messages WHERE id = ?", (w3["reply_msg_id"],)).fetchone()["body"]
check("tin war-room báo 429 + tất cả hết quota", "429" in reply and "tất cả tài khoản hết quota" in reply, reply[:300])
reset_calls()
res = db.post_warroom_message(message="@qa chạy test", author="Ryan (Owner)", wait=True)
w4 = db.wait_worker_result(dispatch_id=res["dispatches"][0]["dispatch_id"], timeout_sec=5)
check("tất cả đang exhausted → failed ngay, không gọi agy lần nào", w4["status"] == "failed" and calls() == []
      and "tất cả tài khoản hết quota" in w4["summary"], str(w4)[:300])

print("[5] chat Gen cũng tự chuyển hồ sơ")
clear_states()
set_exhausted("profile2")
reset_calls()
with db.get_connection() as conn:
    conn.execute("INSERT OR IGNORE INTO gen_conversations (id, project_id, title, agy_conv_id) VALUES ('conv-fb', 'PRJ-GEN-WORKPLACE', 'fb', 'agy-conv-cu')")
    conn.commit()
reply, ret_id, usage = db.call_agy_cli_turn("conv-fb", "xin chào", model="Gemini 3.8 Flash (High)", account="profile2")
c = calls()
check("trả lời thật từ hồ sơ khác", reply.startswith("OK từ profile") and "profile2" not in reply and usage.get("profile_used") != "profile2"
      and usage.get("fallback") is True, f"{reply} {usage}")
check("usage.fallback_note: đã chuyển từ profile2 (hết quota, có lại lúc …)", usage.get("fallback_note", "").startswith("đã chuyển từ profile2 (hết quota, có lại lúc "), str(usage))
check("lượt đầu dùng --conversation của phiên, lượt chạy lại không mang --conversation của tài khoản cũ",
      len(c) == 2 and c[0][0] == "profile2" and "--conversation agy-conv-cu" in c[0][1] and "--conversation" not in c[1][1], str(c)[:400])
with db.get_connection() as conn:
    acid = conn.execute("SELECT agy_conv_id FROM gen_conversations WHERE id = 'conv-fb'").fetchone()["agy_conv_id"]
check("agy_conv_id của phiên giữ nguyên (không ghi phiên của tài khoản khác)", acid == "agy-conv-cu", acid)
check("hết quota nhóm Gemini không chặn nhóm Claude", db.get_profile_quota_state("profile2", "gemini")["exhausted"]
      and not db.get_profile_quota_state("profile2", "claude")["exhausted"])
out = db.send_gen_chat("conv-fb", "Ryan (Owner)", "tiếp", "Gemini 3.8 Flash (High)", "profile2")
check("send_gen_chat: không lỗi, tin trả lời có [Tài khoản] …, account_used khác profile2",
      out["error"] is False and "[Tài khoản] đã chuyển từ profile2" in out["reply"] and out["account_used"] != "profile2", str(out)[:300])
set_exhausted("profile1", "profile2", "profile3", "profile4", "profile5")
out = db.send_gen_chat("conv-fb", "Ryan (Owner)", "nữa", "Gemini 3.8 Flash (High)", "profile2")
check("chat: mọi hồ sơ hết quota → lỗi thật nêu thời điểm có lại sớm nhất", out["error"] is True and out["error_code"] == "RESOURCE_EXHAUSTED"
      and "tất cả tài khoản hết quota, sớm nhất có lại lúc" in out["reply"], out["reply"][:300])

print("[6] API trạng thái: worker / hồ sơ / quota")
clear_states()
set_exhausted("profile3")
res = db.post_warroom_message(message="@devops liệt kê file", author="Ryan (Owner)", wait=True)
w5 = db.wait_worker_result(dispatch_id=res["dispatches"][0]["dispatch_id"], timeout_sec=5)
dev = [s for s in db.get_tmux_sessions() if s["id"] == "gw-devops-agy"][0]
check("worker devops: exhausted, reset_at, reset_at_label", dev["exhausted"] is True and dev["reset_at"] and dev["reset_at_label"], str({k: dev.get(k) for k in ("exhausted", "reset_at", "reset_at_label")}))
check("worker devops: profile_in_use = hồ sơ chạy thật, account_type vẫn profile3",
      dev["profile_in_use"] == w5["profile_used"] != "profile3" and dev["account_type"] == "profile3" and dev["fallback_reason"].startswith("đã chuyển từ profile3"), str(dev)[:300])
p3 = [p for p in db.get_oauth_profiles() if p["id"] == "profile3"][0]
check("hồ sơ profile3: exhausted + reset_at", p3["exhausted"] is True and p3["reset_at"], str(p3)[:300])
g, a = db.get_quota_telemetry("profile3")
check("quota profile3: gemini exhausted, claude không", g["exhausted"] is True and g["reset_at"] and a["exhausted"] is False, f"{g.get('exhausted')}/{a.get('exhausted')}")

print("[7] giao task qua tmux (swarm dispatch)")
if not shutil.which("tmux"):
    print("  (bỏ qua: máy không có tmux)")
else:
    subprocess.run(["tmux", "kill-session", "-t", "gw-devops-agy"], capture_output=True)
    subprocess.run(["tmux", "new-session", "-d", "-s", "gw-devops-agy", "-x", "220", "-y", "50", "-c", TMP, "bash --norc --noprofile"], check=True)
    time.sleep(0.3)
    with db.get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO roadmaps (id, project_id, title, description, todos_count, status, order_idx) VALUES ('RM-Q', 'PRJ-GEN-WORKPLACE', 'RM', '', 1, 'queued', 1)")
        conn.execute("INSERT OR IGNORE INTO todos (id, roadmap_id, project_id, title, assigned_role, status, viec_ref) VALUES ('TODO-Q1', 'RM-Q', 'PRJ-GEN-WORKPLACE', 'Đọc Dockerfile', 'DevOps', 'in_progress', 'VIEC-10')")
        conn.execute("UPDATE tmux_sessions SET current_task_id = 'TODO-Q1' WHERE id = 'gw-devops-agy'")
        conn.commit()
    # (a) profile3 đang exhausted → chọn hồ sơ khác ngay khi gõ lệnh
    reset_calls()
    out = db.dispatch_swarm_workflow(session_id="gw-devops-agy")["gw-devops-agy"]
    check("dispatched, profile_used khác profile3, lệnh mang --gemini_dir hồ sơ mới", out["status"] == "dispatched" and out["profile_used"] != "profile3"
          and f"/.agy-profiles/{out['profile_used']}'" in out["command"] and "bỏ qua, không gọi" in out["fallback_reason"], str(out)[:400])
    wt = db.wait_worker_result(dispatch_id=out["dispatch_id"], timeout_sec=20)
    check("tmux: done, không gọi profile3", wt["status"] == "done" and "profile3" not in [x[0] for x in calls()], str(wt)[:300] + str(calls()))
    # (b) profile3 chưa bị đánh dấu nhưng hết quota khi chạy → watcher gõ lại với hồ sơ khác
    clear_states()
    reset_calls()
    out = db.dispatch_swarm_workflow(session_id="gw-devops-agy")["gw-devops-agy"]
    check("dispatched bằng profile3 (chưa biết hết quota)", out["status"] == "dispatched" and out["profile_used"] == "profile3", str(out)[:300])
    wt = db.wait_worker_result(dispatch_id=out["dispatch_id"], timeout_sec=30)
    c = calls()
    check("tmux: hết quota giữa chừng → gõ lại với hồ sơ khác → done", wt["status"] == "done" and wt["exit_code"] == 0
          and wt["profile_initial"] == "profile3" and wt["profile_used"] != "profile3"
          and [x[0] for x in c][:1] == ["profile3"] and c[-1][0] == wt["profile_used"], str(wt)[:400] + str([x[0] for x in c]))
    check("tmux: summary + fallback_reason nêu việc chuyển", wt["fallback_reason"].startswith("đã chuyển từ profile3 (hết quota, có lại lúc ")
          and "[Tài khoản] đã chuyển từ profile3" in wt["summary"], wt["summary"][:300])
    # (c) mọi hồ sơ exhausted → lỗi rõ, không gõ lệnh
    set_exhausted("profile1", "profile2", "profile3", "profile4", "profile5")
    for pid in ("profile1", "profile2", "profile4", "profile5"):
        db.mark_profile_exhausted(pid, RESETS[pid])
    out = db.dispatch_swarm_workflow(session_id="gw-devops-agy")["gw-devops-agy"]
    check("tmux: tất cả exhausted → error nêu thời điểm sớm nhất", out["status"] == "error"
          and out["reason"].startswith("tất cả tài khoản hết quota, sớm nhất có lại lúc") and "(profile4)" in out["reason"], str(out)[:300])
    subprocess.run(["tmux", "kill-server"], capture_output=True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)
