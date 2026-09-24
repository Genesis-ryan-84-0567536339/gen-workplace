#!/usr/bin/env bash
# ==============================================================================
# GEN-WORKPLACE · ONE-COMMAND ALL-IN-ONE INSTALLER
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Kiểm tra Python3
if ! command -v python3 &>/dev/null; then
    echo "Python3 chưa được cài đặt. Đang tự động bổ sung..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y python3
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y python3
    elif command -v brew &>/dev/null; then
        brew install python3
    else
        echo "Lỗi: Vui lòng cài đặt Python 3 để chạy bộ cài đặt TUI."
        exit 1
    fi
fi

# Chạy TUI Installer với giao diện trực quan
python3 "${SCRIPT_DIR}/installer_tui.py"
