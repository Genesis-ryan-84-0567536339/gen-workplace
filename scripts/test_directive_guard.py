#!/usr/bin/env python3
"""Kiểm thử allowlist lệnh gửi vào tmux (backend/directive_guard.py). Chạy: python3 scripts/test_directive_guard.py"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("GW_DIRECTIVE_ALLOW_ALL", None)

from backend import directive_guard as g

TEMPLATE = ("clear; agy --gemini_dir='/home/ryan/.agy-profiles/profile3' --model claude-sonnet-4-6 "
            "--mode plan --sandbox --print-timeout 15m -p 'đề bài; có $(x) `y` && rm -rf /' 2>&1 "
            "| tee ~/gw-reports/bao-cao.md; echo \"=== XONG exit=${PIPESTATUS[0]} ===\"")

ALLOWED = [
    ("gw-qa-agy", TEMPLATE, ""),
    ("gw-qa-agy", "agy -p 'xin chào'", ""),
    ("gw-qa-agy", "agy-run", ""),
    ("gw-qa-agy", "clear && agy --mode plan -p \"liệt kê file\"", ""),
    ("gw-qa-agy", "gw-role", ""),
    ("gw-qa-agy", "gw-status", ""),
    ("gw-qa-agy", "gw-update", ""),
    ("gw-qa-agy", "gw-update claude/new-session-aiif5x", ""),
    ("gw-qa-agy", "", "C-c"),
    ("gw-qa-agy", "", "Enter"),
    ("gw-qa-agy", "", "q"),
    ("gw-qa-agy", "agy -p 'x' 2>&1 | tee /home/ryan/gw-reports/x.md", ""),
    ("gw-oauth-login", "4/0AbC-dEf_ghi123", ""),
    ("gw-oauth-login", "https://accounts.google.com/o/oauth2/auth?code=abc&state=profile1", ""),
]

REJECTED = [
    ("gw-qa-agy", "rm -rf /tmp/x", ""),
    ("gw-qa-agy", "ls", ""),
    ("gw-qa-agy", "agy -p 'hi' && rm -rf /", ""),
    ("gw-qa-agy", "agy -p 'hi'; rm -rf /", ""),
    ("gw-qa-agy", "agy -p 'hi' | bash", ""),
    ("gw-qa-agy", "agy -p 'hi' | tee /etc/passwd", ""),
    ("gw-qa-agy", "agy -p 'hi' | tee ~/gw-reports/../.bashrc", ""),
    ("gw-qa-agy", "agy -p \"$(cat /etc/passwd)\"", ""),
    ("gw-qa-agy", "agy -p `id`", ""),
    ("gw-qa-agy", "agy -p 'hi' > /tmp/out", ""),
    ("gw-qa-agy", "agy -p 'hi'; echo ok; rm -rf /", ""),
    ("gw-qa-agy", "agy -p 'hi' 2>&1 | tee ~/gw-reports/a.md | bash", ""),
    ("gw-qa-agy", "agy -p 'hi' &", ""),
    ("gw-qa-agy", "agyx -p 'hi'", ""),
    ("gw-qa-agy", "agy -p 'hi\nrm -rf /'", ""),
    ("gw-qa-agy", "agy -p 'chưa đóng nháy", ""),
    ("gw-qa-agy", "gw-status; ls", ""),
    ("gw-qa-agy", "gw-update main; ls", ""),
    ("gw-qa-agy", "gw-update $(id)", ""),
    ("gw-qa-agy", "export GEMINI_DIR=/tmp", ""),
    ("gw-qa-agy", "", "C-d"),
    ("gw-qa-agy", "", "C-z"),
    ("gw-oauth-login", "ls; rm -rf /", ""),
    ("gw-oauth-login", "$(id)", ""),
]


def main():
    failed = 0
    for sid, cmd, key in ALLOWED:
        ok, reason = g.guard(sid, cmd, key)
        if not ok:
            failed += 1
            print(f"FAIL (phải cho phép) [{sid}] {cmd or key!r}: {reason}")
    for sid, cmd, key in REJECTED:
        ok, reason = g.guard(sid, cmd, key)
        if ok:
            failed += 1
            print(f"FAIL (phải từ chối) [{sid}] {cmd or key!r}")

    os.environ["GW_DIRECTIVE_ALLOW_ALL"] = "1"
    ok, _ = g.guard("gw-qa-agy", "rm -rf /tmp/x")
    os.environ.pop("GW_DIRECTIVE_ALLOW_ALL", None)
    if not ok:
        failed += 1
        print("FAIL: GW_DIRECTIVE_ALLOW_ALL=1 phải tắt allowlist")

    total = len(ALLOWED) + len(REJECTED) + 1
    print(f"{total - failed}/{total} test pass")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
