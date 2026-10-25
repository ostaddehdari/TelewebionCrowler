#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
set -a; source /etc/telewebion/twcrawler.env; set +a
printf '===== SERVICES =====\n'
systemctl is-active postgresql nginx twcrawler-web
printf '===== LOCAL HEALTH =====\n'
curl -fsS http://127.0.0.1:18895/health
printf '\n===== COUNTS =====\n'
cd "$ROOT"
"$ROOT/.venv/bin/python" - <<'PY'
from twpg.db import connect, stats
with connect() as c:
 print(stats(c))
 print('Recent imports:',c.execute('select count(*) AS n from import_history').fetchone()['n'])
PY
printf '===== FIREWALL =====\n'
ufw status
printf '===== DATABASE LISTENERS =====\n'
ss -lntp | grep -E ':(5432|6379|18895)\b' || true
