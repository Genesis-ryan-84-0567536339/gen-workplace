#!/usr/bin/env bash
# GENESIS Swarm Workplace - Attach to Agent Tmux Session
# Usage: ./attach-session.sh [session_id]
# Example: ./attach-session.sh gw-lead-agy

SESSION="${1:-gw-lead-agy}"

echo "================================================================================"
echo "Connecting to Swarm Runtime: $SESSION"
echo "To detach safely from tmux without killing the agent, press: Ctrl+b then d"
echo "================================================================================"

docker exec -it gen-workplace-app tmux attach-session -t "$SESSION"
