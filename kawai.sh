#!/usr/bin/env bash
# KawaiiGPT launcher - one entry point for the whole stack.
#
#   ./kawai.sh start     start the UI (systemd user service, auto-restart)
#   ./kawai.sh tunnel    print the ssh command to reach it from your machine
#   ./kawai.sh cli       run the CLI in this terminal
#   ./kawai.sh status    is it up? which model, keyed or free tier
#   ./kawai.sh logs      tail the service journal
#   ./kawai.sh stop      stop the UI
set -u
cd "$(dirname "$0")" || exit 1
PORT="${KAWAII_PORT:-8772}"

case "${1:-start}" in
  start)
    systemctl --user start kawaii-ui 2>/dev/null
    sleep 1
    if curl -sf -m 3 "http://127.0.0.1:$PORT/api/status" >/dev/null; then
      echo "up on http://127.0.0.1:$PORT"
      if [ -n "${SSH_CONNECTION:-}" ]; then
        echo "from your machine:  ssh -L $PORT:127.0.0.1:$PORT $(whoami)@$(hostname -I | awk '{print $1}')"
      fi
    else
      echo "did not come up - check: systemctl --user status kawaii-ui" >&2
      exit 1
    fi
    ;;
  tunnel)
    echo "ssh -L $PORT:127.0.0.1:$PORT $(whoami)@$(hostname -I | awk '{print $1}')"
    echo "then open http://127.0.0.1:$PORT"
    ;;
  cli)
    exec ./venv/bin/python kawai.py
    ;;
  status)
    curl -s -m 3 "http://127.0.0.1:$PORT/api/status" || echo "down"
    echo
    systemctl --user is-active kawaii-ui
    ;;
  logs)
    exec journalctl --user -u kawaii-ui -f --no-pager
    ;;
  stop)
    systemctl --user stop kawaii-ui
    echo "stopped"
    ;;
  *)
    sed -n '2,10p' "$0"
    ;;
esac
