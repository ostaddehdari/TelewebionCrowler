#!/usr/bin/env bash
set -euo pipefail
printf 'NEW APP:\n'
systemctl is-active newtwcrawler-web
printf '\nOLD APP (unchanged):\n'
systemctl is-active twcrawler-web
printf '\nNEW DATABASE:\n'
runuser -u postgres -- psql -d newtwcrawler -c "SELECT kind,count(*) FROM entities GROUP BY kind ORDER BY kind;"
runuser -u postgres -- psql -d newtwcrawler -c "SELECT state,count(*) FROM crawl_tasks GROUP BY state ORDER BY state;"
printf '\nNEW HTTP:\n'
curl -sS -w '\nhttp=%{http_code} total=%{time_total}s\n' http://127.0.0.1:18896/health
printf '\nPUBLIC HTTPS LOGIN (with local trust override for temporary certificate):\n'
curl -k -sS -o /dev/null -w 'login http=%{http_code} total=%{time_total}s\n' https://5.61.28.187/newtwcrawler/login
printf '\nOLD SITE HTTP PROBE:\n'
curl -k -sS -o /dev/null -w 'old status=%{http_code}\n' https://5.61.28.187/twcrawler/login
