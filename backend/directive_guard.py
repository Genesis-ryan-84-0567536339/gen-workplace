#!/usr/bin/env python3
"""
Allowlist cho lệnh gửi vào tmux của worker (send_worker_directive / POST /api/tmux/send).

Lý do: các phiên tmux là shell bash thật chạy dưới user chủ máy. Không có allowlist thì
bất kỳ ai gọi được MCP/API đều có shell từ xa toàn quyền. Module này là NƠI DUY NHẤT
quyết định một chuỗi có được gõ vào tmux hay không; cả MCP tool lẫn API HTTP đều gọi
check_directive() trước khi chạy `tmux send-keys`.

Cho phép:
  - Phím điều khiển: C-c, Enter, q, Escape.
  - Lệnh bắt đầu bằng `agy ...` hoặc `agy-run ...` (tuỳ chọn `clear;` / `clear &&` đứng trước),
    có thể nối thêm đúng mẫu giao việc chuẩn:
        ... 2>&1 | tee ~/gw-reports/<file>.md; echo "=== XONG exit=${PIPESTATUS[0]} ==="
  - Alias tra cứu: gw-role, gw-status; gw-update [nhánh] (cập nhật app từ GitHub, scripts/gw-update.sh).
  - Không nhận cờ --dangerously-skip-permissions trong lệnh agy (Issue #7): quyền ghi không hỏi chỉ có ở alias
    agy-run của vai trong GW_AGY_WRITE_ROLES, và chỉ khi phiên tmux mở trong worktree riêng của vai.
  - Riêng phiên gw-oauth-login: chuỗi văn bản thuần (không ký tự điều khiển shell) để
    trả lời màn hình đăng nhập của agy.

Biến môi trường GW_DIRECTIVE_ALLOW_ALL=1 tắt allowlist (mặc định tắt, chỉ dùng khi debug).
"""

import os
import re
import shlex

ALLOWED_KEYS = {"C-c", "Enter", "q", "Escape"}
ALLOWED_ALIASES = {"gw-role", "gw-status"}
_GW_UPDATE_RE = re.compile(r"^gw-update(?: [A-Za-z0-9._/\-]{1,80})?$")
SEPARATORS = {";", "|", "&&", "||", "&"}
OAUTH_LOGIN_SESSION = "gw-oauth-login"
_PLAIN_TEXT_RE = re.compile(r"^[A-Za-z0-9 _./:?=&%@~+\-]+$")
_REPORT_DIR_RE = re.compile(r"^(?:~|\$HOME|/home/[A-Za-z0-9_\-]+)/gw-reports/[A-Za-z0-9_\-.]+$")
_ALLOWED_EXPANSIONS = {"$?", "${PIPESTATUS[0]}"}
# Cờ bỏ qua hỏi quyền của agy: không bao giờ nhận qua directive (Issue #7)
FORBIDDEN_AGY_FLAGS = {"--dangerously-skip-permissions"}
_EXPANSION_RE = re.compile(r"\$\{[^}]*\}|\$[A-Za-z_?][A-Za-z0-9_]*")


def allow_all_enabled():
    return os.environ.get("GW_DIRECTIVE_ALLOW_ALL", "").strip().lower() in ("1", "true", "yes", "on")


def _tokenize(command):
    lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _split_segments(tokens):
    """Tách danh sách token thành các đoạn theo ; | && || &  (giữ lại dấu tách)."""
    segments, current = [], []
    for tok in tokens:
        if tok in SEPARATORS:
            segments.append((current, tok))
            current = []
        else:
            current.append(tok)
    segments.append((current, None))
    return segments


def _token_is_dangerous(tok):
    """Token có mở rộng/shell-substitution nguy hiểm không (ngoài phần nằm trong nháy đơn)."""
    if tok.startswith("'") and tok.endswith("'") and len(tok) >= 2:
        return False  # nháy đơn: bash không mở rộng gì bên trong
    if "`" in tok or "$(" in tok or "<(" in tok or ">(" in tok:
        return True
    for exp in _EXPANSION_RE.findall(tok):
        if exp not in _ALLOWED_EXPANSIONS:
            return True
    return False


def check_directive(session_id, command="", key=""):
    """
    Trả về (allowed: bool, reason: str). reason rỗng khi cho phép.
    """
    session_id = (session_id or "").strip()
    command = (command or "").strip()
    key = (key or "").strip()

    if allow_all_enabled():
        return True, ""

    if key:
        if key in ALLOWED_KEYS:
            return True, ""
        return False, f"Phím '{key}' không nằm trong allowlist {sorted(ALLOWED_KEYS)}"

    if not command:
        return False, "Lệnh rỗng"
    if "\n" in command or "\r" in command:
        return False, "Lệnh nhiều dòng bị từ chối"

    if command in ALLOWED_ALIASES or _GW_UPDATE_RE.match(command):
        return True, ""

    try:
        tokens = _tokenize(command)
    except ValueError as e:
        return False, f"Không phân tích được lệnh (nháy chưa đóng?): {e}"
    if not tokens:
        return False, "Lệnh rỗng"

    for tok in tokens:
        if _token_is_dangerous(tok):
            return False, f"Token '{tok}' chứa mở rộng shell không được phép"

    segments = _split_segments(tokens)

    # Tuỳ chọn: clear; hoặc clear &&
    if segments and segments[0][0] == ["clear"] and segments[0][1] in (";", "&&"):
        segments = segments[1:]

    if not segments or not segments[0][0]:
        return False, "Thiếu lệnh chính"
    main_seg, main_sep = segments[0]
    head = main_seg[0]

    if head in ALLOWED_ALIASES and len(main_seg) == 1 and main_sep is None:
        return True, ""

    if head not in ("agy", "agy-run"):
        return False, f"Chỉ cho phép lệnh bắt đầu bằng agy/agy-run hoặc {sorted(ALLOWED_ALIASES)}; nhận được '{head}'"

    # Cho phép '2>&1' ở cuối đoạn agy (shlex tách thành '2', '>&', '1')
    body = list(main_seg[1:])
    if len(body) >= 3 and body[-3:] == ["2", ">&", "1"]:
        body = body[:-3]
    for tok in body:
        if tok in ("<", ">", ">>", "<<", ">&", "<&", "(", ")"):
            return False, f"Chuyển hướng/nhóm lệnh '{tok}' không được phép trong đoạn agy"
        if tok.strip("'\"").split("=", 1)[0] in FORBIDDEN_AGY_FLAGS:
            return False, (f"Cờ '{tok}' không được gửi qua directive; quyền ghi không hỏi chỉ bật qua GW_AGY_WRITE_ROLES "
                           "(alias agy-run của vai đó, trong worktree riêng)")

    # Các đoạn nối tiếp: chỉ chấp nhận '| tee <~/gw-reports/...>' rồi '; echo "..."'
    rest = segments[1:]
    prev_sep = main_sep
    for seg, sep in rest:
        if prev_sep == "|":
            if len(seg) != 2 or seg[0] != "tee":
                return False, "Sau '|' chỉ cho phép 'tee <đường dẫn trong ~/gw-reports/>'"
            path = seg[1].strip("'\"")
            if ".." in path or not _REPORT_DIR_RE.match(path):
                return False, f"Đường dẫn tee '{seg[1]}' phải nằm trong ~/gw-reports/"
        elif prev_sep == ";":
            if not seg or seg[0] != "echo":
                return False, "Sau ';' chỉ cho phép 'echo ...'"
            if sep is not None:
                return False, "Không được nối thêm lệnh sau echo"
        else:
            return False, f"Toán tử '{prev_sep}' không được phép"
        prev_sep = sep
    if prev_sep is not None:
        return False, f"Lệnh kết thúc bằng toán tử '{prev_sep}' không hợp lệ"
    return True, ""


def check_oauth_login_text(command):
    """Văn bản gõ vào phiên gw-oauth-login (trả lời màn hình agy), không phải lệnh shell."""
    command = (command or "").strip()
    if not command or "\n" in command:
        return False, "Văn bản rỗng hoặc nhiều dòng"
    if _PLAIN_TEXT_RE.match(command):
        return True, ""
    return False, "Phiên gw-oauth-login chỉ nhận văn bản thuần (không ký tự điều khiển shell)"


def guard(session_id, command="", key=""):
    """Điểm gọi chung cho MCP và HTTP: chọn luật theo phiên rồi trả (allowed, reason)."""
    if allow_all_enabled():
        return True, ""
    if (session_id or "").strip() == OAUTH_LOGIN_SESSION and not key:
        return check_oauth_login_text(command)
    return check_directive(session_id, command, key)
