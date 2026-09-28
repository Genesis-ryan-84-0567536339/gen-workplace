#!/usr/bin/env python3
"""
Test Issue #12: db.compact_gen_conversation không bịa tóm tắt. Chạy không cần server/agy/tmux (DB tạm qua DATA_DIR):
- 0 tin hoặc 1 tin, kể cả manual=True → {"status": "skipped", "reason": "Chưa đủ tin để nén"}, không ghi snapshot/tin compact.
- Có tin thật → tóm tắt chỉ gồm số tin, trích nguyên văn (rút gọn) 3 câu hỏi / 3 trả lời gần nhất, #NOTE thật;
  không còn câu mẫu ("Đã thống nhất cơ chế bảo toàn SSOT…", "Thảo luận điều phối…", "(chưa có ghi chú)").
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="gw-test-compact-")
FAKEBIN = os.path.join(TMP, "bin")
os.makedirs(FAKEBIN)
with open(os.path.join(FAKEBIN, "tmux"), "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(os.path.join(FAKEBIN, "tmux"), 0o755)

os.environ["DATA_DIR"] = os.path.join(TMP, "data")
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["PATH"] = FAKEBIN + os.pathsep + os.environ.get("PATH", "")
os.makedirs(os.environ["DATA_DIR"])
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)

from backend import db  # noqa: E402

PASSED = 0
FAILED = 0
FAKE_PHRASES = [
    "Đã thống nhất cơ chế bảo toàn SSOT",
    "Thảo luận điều phối và kiến trúc hệ thống",
    "(chưa có ghi chú)",
    "Progressive Context Compact",
    "Quyết định chốt",
]


def check(label, cond, extra=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ok   {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label} {extra}")


def counts(conv_id):
    with db.get_connection() as conn:
        snaps = conn.execute("SELECT count(*) FROM gen_compact_snapshots WHERE conversation_id = ?", (conv_id,)).fetchone()[0]
        cmp_msgs = conn.execute("SELECT count(*) FROM gen_messages WHERE conversation_id = ? AND role = 'compact'", (conv_id,)).fetchone()[0]
        total = conn.execute("SELECT count(*) FROM gen_messages WHERE conversation_id = ?", (conv_id,)).fetchone()[0]
    return snaps, cmp_msgs, total


def add_msg(conv_id, role, content, author=None):
    with db.get_connection() as conn:
        conn.execute("""
        INSERT INTO gen_messages (conversation_id, author, role, content, model, note_ids_json, owner_id)
        VALUES (?, ?, ?, ?, 'test-model', '[]', 'owner-ryan')
        """, (conv_id, author or ("Ryan (Owner)" if role == "user" else "Gen Core (agy CLI)"), role, content))
        conn.commit()


conv = db.create_gen_conversation(title="Phiên test nén")
CID = conv["id"]

print("[1] 0 tin, manual=True → skipped, không ghi gì")
res = db.compact_gen_conversation(CID, manual=True)
check("status=skipped", res.get("status") == "skipped", str(res))
check("reason tiếng Việt", res.get("reason") == "Chưa đủ tin để nén", str(res))
check("không có snapshot / tin compact", counts(CID) == (0, 0, 0), str(counts(CID)))

print("[2] 1 tin, manual=True → vẫn skipped")
add_msg(CID, "user", "Câu hỏi đầu tiên về kiến trúc")
res = db.compact_gen_conversation(CID, manual=True)
check("status=skipped", res.get("status") == "skipped", str(res))
check("không có snapshot / tin compact, 1 tin gốc", counts(CID) == (0, 0, 1), str(counts(CID)))

print("[3] nhiều tin thật → tóm tắt chỉ từ dữ liệu thật")
add_msg(CID, "assistant", "Trả lời 1: dùng SQLite WAL. Xem #NOTE-07 và #EVT-03.")
add_msg(CID, "user", "Câu hỏi 2: tại sao không dùng Postgres?")
add_msg(CID, "assistant", "Trả lời 2: nhẹ, không cần dịch vụ ngoài.")
add_msg(CID, "user", "Câu hỏi 3: nén ngữ cảnh hoạt động ra sao?")
add_msg(CID, "assistant", "Trả lời 3: " + ("x" * 400))
add_msg(CID, "user", "Câu hỏi 4 (mới nhất): còn #NOTE-12 thì sao?")
res = db.compact_gen_conversation(CID, model_from="A", model_to="B", manual=True)
summ = res.get("summary", "")
check("status=compacted", res.get("status") == "compacted", str(res))
check("message_count = 7 tin thật", res.get("message_count") == 7, str(res.get("message_count")))
check("note_ids là danh sách thật", res.get("note_ids") == ["#EVT-03", "#NOTE-07", "#NOTE-12"], str(res.get("note_ids")))
check("summary có số tin thật", "đã nén 7 tin nhắn" in summ, summ)
check("3 câu hỏi gần nhất, nguyên văn (không có câu 1)",
      "Câu hỏi 2: tại sao không dùng Postgres?" in summ and "Câu hỏi 4 (mới nhất)" in summ and "Câu hỏi đầu tiên" not in summ, summ)
check("3 trả lời agy gần nhất, nguyên văn", "Trả lời 1: dùng SQLite WAL" in summ and "Trả lời 2: nhẹ" in summ and "Trả lời 3: xxxx" in summ, summ)
check("trả lời dài bị rút gọn kèm '…'", "…" in summ and ("x" * 200) not in summ)
check("liệt kê #NOTE thật", "#EVT-03, #NOTE-07, #NOTE-12" in summ, summ)
check("không còn câu mẫu bịa", not any(p in summ for p in FAKE_PHRASES), summ)
snaps, cmp_msgs, total = counts(CID)
check("1 snapshot + 1 tin compact (7 gốc + 1)", (snaps, cmp_msgs, total) == (1, 1, 8), str((snaps, cmp_msgs, total)))
with db.get_connection() as conn:
    n_un = conn.execute("SELECT count(*) FROM gen_messages WHERE conversation_id = ? AND is_compacted = 0 AND role != 'compact'", (CID,)).fetchone()[0]
    model_now = conn.execute("SELECT model FROM gen_conversations WHERE id = ?", (CID,)).fetchone()["model"]
check("7 tin gốc đã đánh dấu is_compacted", n_un == 0, str(n_un))
check("model phiên đổi sang B", model_now == "B", model_now)

print("[4] nén lại ngay → skipped, không ghi thêm")
res = db.compact_gen_conversation(CID, manual=True)
check("status=skipped", res.get("status") == "skipped", str(res))
check("vẫn 1 snapshot / 1 tin compact", counts(CID) == (1, 1, 8), str(counts(CID)))

print("[5] tin không có #NOTE → ghi 'không có', không bịa")
conv2 = db.create_gen_conversation(title="Phiên test nén 2")
add_msg(conv2["id"], "user", "hỏi")
add_msg(conv2["id"], "assistant", "đáp")
res = db.compact_gen_conversation(conv2["id"])
check("status=compacted (không manual, đủ 2 tin)", res.get("status") == "compacted", str(res))
check("Note: không có", "Note/Event được nhắc tới:** không có" in res.get("summary", ""), res.get("summary"))
check("không còn câu mẫu bịa", not any(p in res.get("summary", "") for p in FAKE_PHRASES), res.get("summary"))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{PASSED}/{PASSED + FAILED} test pass")
sys.exit(0 if FAILED == 0 else 1)
