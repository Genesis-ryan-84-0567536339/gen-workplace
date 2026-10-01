#!/usr/bin/env python3
"""
Test bảo vệ scripts/test_mcp_suite.py (Issue #68 mục 4, 10).
- Chạy test_mcp_suite.py không env → exit 0 và in "bỏ qua" (không gọi mạng vào app thật).
- Chạy test_mcp_suite.py với GW_MCP_SUITE_URL=http://localhost:8888 không ALLOW_LIVE → exit 0 và in "bỏ qua".
- Chạy test_mcp_suite.py với GW_MCP_SUITE_URL=http://127.0.0.1:8888 không ALLOW_LIVE → exit 0 và in "bỏ qua".
"""

import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "test_mcp_suite.py")

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


def run_suite(env_overrides):
    env = os.environ.copy()
    env.pop("GW_MCP_SUITE_URL", None)
    env.pop("GW_MCP_SUITE_ALLOW_LIVE", None)
    env.update(env_overrides)
    res = subprocess.run(
        [sys.executable, SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        timeout=10,
    )
    return res.returncode, res.stdout, res.stderr


print("[1] Chạy test_mcp_suite.py không có env")
rc, out, err = run_suite({})
check("exit code == 0 khi không có env", rc == 0, f"rc={rc}")
check("in 'bỏ qua' khi không có env", "bỏ qua" in out.lower(), f"out={out!r}")

print("[2] Chạy test_mcp_suite.py với GW_MCP_SUITE_URL=http://localhost:8888 (không ALLOW_LIVE)")
rc, out, err = run_suite({"GW_MCP_SUITE_URL": "http://localhost:8888"})
check("exit code == 0 với localhost:8888", rc == 0, f"rc={rc}")
check("in 'bỏ qua' với localhost:8888", "bỏ qua" in out.lower(), f"out={out!r}")

print("[3] Chạy test_mcp_suite.py với GW_MCP_SUITE_URL=http://localhost:8888 và GW_MCP_SUITE_ALLOW_LIVE=0")
rc, out, err = run_suite({"GW_MCP_SUITE_URL": "http://localhost:8888", "GW_MCP_SUITE_ALLOW_LIVE": "0"})
check("exit code == 0 với ALLOW_LIVE=0", rc == 0, f"rc={rc}")
check("in 'bỏ qua' với ALLOW_LIVE=0", "bỏ qua" in out.lower(), f"out={out!r}")

print("[4] Chạy test_mcp_suite.py với GW_MCP_SUITE_URL=http://127.0.0.1:8888 (không ALLOW_LIVE)")
rc, out, err = run_suite({"GW_MCP_SUITE_URL": "http://127.0.0.1:8888"})
check("exit code == 0 với 127.0.0.1:8888", rc == 0, f"rc={rc}")
check("in 'bỏ qua' với 127.0.0.1:8888", "bỏ qua" in out.lower(), f"out={out!r}")

print(f"\nKết quả: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
