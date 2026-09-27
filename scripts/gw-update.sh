#!/usr/bin/env bash
# gw-update: cập nhật gen-workplace trên máy chủ về đúng một commit đã có trên GitHub rồi khởi động lại app.
#
#   gw-update            -> origin/main
#   gw-update <nhánh>    -> origin/<nhánh> (vd nhánh PR đang xem thử)
#
# Thay đổi chưa commit trong thư mục (nếu có) được cất vào `git stash` với nhãn có ngày giờ,
# KHÔNG bị xóa. App được khởi động lại bằng systemd --user nếu có unit `gen-workplace.service`,
# nếu không thì tắt tiến trình `python3 backend/main.py` cũ và chạy lại bằng nohup.
set -euo pipefail

BRANCH="${1:-main}"
REPO_DIR="${GW_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_DIR="${GW_LOG_DIR:-$HOME/gw-reports}"
mkdir -p "$LOG_DIR"

cd "$REPO_DIR"
echo "[gw-update] thư mục: $REPO_DIR · nhánh: $BRANCH"

if [ -n "$(git status --porcelain)" ]; then
  STAMP="$(date +%Y%m%d-%H%M%S)"
  git stash push -u -m "gw-update $STAMP (trước khi chuyển sang $BRANCH)" >/dev/null
  echo "[gw-update] đã cất thay đổi chưa commit vào stash: gw-update $STAMP"
fi

git fetch origin "$BRANCH"
git checkout -q -B "$BRANCH" "origin/$BRANCH"
NEW_SHA="$(git rev-parse --short HEAD)"
echo "[gw-update] đang ở commit $NEW_SHA ($(git log -1 --pretty=%s))"

python3 -m py_compile backend/*.py
echo "[gw-update] py_compile OK"

if systemctl --user list-unit-files 2>/dev/null | grep -q '^gen-workplace.service'; then
  systemctl --user restart gen-workplace.service
  echo "[gw-update] đã restart gen-workplace.service (systemd --user)"
else
  OLD_PIDS="$(pgrep -f 'python3 backend/main.py' || true)"
  if [ -n "$OLD_PIDS" ]; then
    echo "[gw-update] tắt tiến trình cũ: $OLD_PIDS"
    kill $OLD_PIDS || true
    sleep 1
  fi
  nohup python3 backend/main.py >>"$LOG_DIR/gen-workplace.log" 2>&1 &
  echo "[gw-update] đã chạy lại python3 backend/main.py (log: $LOG_DIR/gen-workplace.log)"
fi

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS -m 2 "http://127.0.0.1:${PORT:-8888}/api/status" >/dev/null 2>&1; then
    echo "[gw-update] app trả lời OK tại cổng ${PORT:-8888} · commit $NEW_SHA"
    exit 0
  fi
  sleep 1
done
echo "[gw-update] CẢNH BÁO: app chưa trả lời sau 10 giây, xem log $LOG_DIR/gen-workplace.log" >&2
exit 1
