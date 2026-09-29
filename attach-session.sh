#!/usr/bin/env bash
# GENESIS Swarm Workplace - Attach to Agent Tmux Session
# Usage: ./attach-session.sh [session_id]
# Example: ./attach-session.sh gw-qa-agy
#
# Phiên tmux chỉ mở khi cần và tự ngủ đông sau GW_TMUX_IDLE_MIN phút rảnh (#32):
# script gọi API "open" để mở phiên nếu đang ngủ (không khởi động lại phiên đang chạy), rồi attach.
# GW_URL: địa chỉ app (mặc định http://127.0.0.1:8888).

SESSION="${1:-gw-qa-agy}"
GW_URL="${GW_URL:-http://127.0.0.1:8888}"

echo "================================================================================"
echo "Connecting to Swarm Runtime: $SESSION"
echo "To detach safely from tmux without killing the agent, press: Ctrl+b then d"
echo "================================================================================"

if command -v curl >/dev/null 2>&1; then
  curl -s -m 15 -X POST -H 'Content-Type: application/json' \
    -d "{\"session_id\": \"$SESSION\", \"action\": \"open\"}" "$GW_URL/api/tmux/action" >/dev/null \
    || echo "(Không gọi được $GW_URL để mở phiên — thử attach trực tiếp)"
fi

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx gen-workplace-app; then
  docker exec -it gen-workplace-app tmux attach-session -t "$SESSION"
else
  tmux attach-session -t "$SESSION"
fi
