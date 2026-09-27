#!/usr/bin/env python3
"""
GENESIS Multi-Agent Swarm Workplace - MCP Stdio Server Bridge
Chạy độc lập làm MCP Stdio Process cho các LLM Client / CLI như Antigravity,
Claude Desktop, Cursor, Codex, OpenCode, hoặc các Subagent.
Giao thức: Đọc JSON-RPC từng dòng từ stdin, phản hồi qua stdout.
"""

import os
import sys
import json
import logging
from pathlib import Path

# Cấu hình log ra stderr để không làm nhiễu stdout (stdout dành riêng cho JSON-RPC)
logging.basicConfig(level=logging.INFO, format="[MCP %(levelname)s] %(message)s", stream=sys.stderr)

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    from backend import mcp_core
except ImportError:
    import mcp_core

def run_stdio():
    logging.info(f"Khởi động gen-workplace MCP Server (v{mcp_core.MCP_SERVER_INFO['version']}) trên kênh Stdio...")
    logging.info(f"Đã nạp {len(mcp_core.TOOLS)} MCP Tools và {len(mcp_core.RESOURCES)} Resources sẵn sàng.")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            logging.error(f"Lỗi cú pháp JSON-RPC từ stdin: {e}")
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
            }
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue

        try:
            resp = mcp_core.handle_jsonrpc(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
        except Exception as e:
            logging.error(f"Lỗi khi điều phối JSON-RPC: {e}")
            req_id = req.get("id") if isinstance(req, dict) else None
            err_resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": f"Internal error: {str(e)}"}
            }
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()

def main():
    if "--tools" in sys.argv:
        print(f"gen-workplace MCP Server Tools ({len(mcp_core.TOOLS)} tools):")
        for t in mcp_core.TOOLS:
            print(f" - {t['name']}: {t['description'][:80]}...")
        return

    if "--test" in sys.argv:
        print("Testing MCP initialize...")
        init_res = mcp_core.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        print(json.dumps(init_res, indent=2, ensure_ascii=False))
        return

    run_stdio()

if __name__ == "__main__":
    main()
