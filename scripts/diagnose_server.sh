#!/usr/bin/env bash
# TelewebionCrawler diagnostics: read-only (except a private report file).
# Tested target: Ubuntu 26.04, PostgreSQL 18, twcrawler-web/worker.
set -uo pipefail
umask 077
if (( EUID != 0 )); then echo "Run as root: sudo bash scripts/diagnose_server.sh"; exit 1; fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="/root/twcrawler-diagnostics-$STAMP.log"
touch "$OUT" || exit 1
exec > >(tee -a "$OUT") 2>&1
DEEP=0
if [[ "$#" -gt 0 && "$1" == "--deep" ]]; then DEEP=1; fi
DB="twcrawler"
CFG="/etc/telewebion/twcrawler.env"
ROOT="/opt/telewebion-crawler"
MAX_ATTEMPTS=3
RETRY_PARTIAL=false
if [[ -r "$CFG" ]]; then
  setting="$(awk -F= '$1=="TW_MAX_ATTEMPTS"{print $2}' "$CFG" | tail -1)"
  if [[ "$setting" =~ ^[1-9][0-9]?$ ]]; then MAX_ATTEMPTS="$setting"; fi
  setting="$(awk -F= '$1=="TW_RETRY_PARTIAL"{print $2}' "$CFG" | tail -1)"
  if [[ "$setting" == true ]]; then RETRY_PARTIAL=true; fi
fi

heading() { printf '\n========== %s ==========\n' "$1"; }
sql() {
  if ! command -v psql >/dev/null; then echo "psql missing"; return 0; fi
  runuser -u postgres -- psql -X -q -P pager=off -P footer=off \
    -v ON_ERROR_STOP=1 -d "$DB" -c \
    "SET default_transaction_read_only=on; SET statement_timeout='12000ms'; $1" \
    2>&1 || echo "[WARN] Query failed or reached 12-second timeout"
}
service() {
  local name="$1"
  echo "--- $name ---"
  systemctl show "$name" \
    -p LoadState -p ActiveState -p SubState -p UnitFileState \
    -p MainPID -p NRestarts -p ExecMainStatus -p ActiveEnterTimestamp \
    --no-pager 2>&1 || true
}

heading "IDENTITY / NO CREDENTIALS"
date -u '+UTC: %Y-%m-%d %H:%M:%S'
hostname
uname -r
printf 'Report saved in %s (root-only permissions)\n' "$OUT"
echo 'DO NOT post your .env, DATABASE_URL, password, or session cookie.'
echo "Configured retry_partial=$RETRY_PARTIAL max_attempts=$MAX_ATTEMPTS"
if [[ -r "$CFG" ]]; then
  awk -F= '$1 ~ /^(TW_BATCH_LIMIT|TW_RESCAN_SECONDS|TW_DELAY_MS|TW_MAX_SCROLLS|TW_MAX_API_PAGES|TW_RETRY_PARTIAL|TW_MAX_ATTEMPTS|PLAYWRIGHT_BROWSERS_PATH)$/ {print $1 "=" $2}' "$CFG"
else
  echo "[WARN] Configuration file not readable: $CFG"
fi

heading "SERVICES / PROCESS STATE"
for unit in twcrawler-web twcrawler-worker nginx postgresql; do service "$unit"; done
echo "--- other crawler services / timers ---"
systemctl list-units --all --type=service --no-legend 2>/dev/null | grep -i twcrawl || true
systemctl list-timers --all --no-pager 2>/dev/null | grep -i twcrawl || true
echo "--- worker and browser processes, names only ---"
ps -eo pid,ppid,user,etime,stat,%cpu,%mem,comm | grep -E '(python|uvicorn|chrom|firefox|webkit|PID)' | head -35 || true
echo "--- listeners ---"
ss -lntp 2>/dev/null | grep -E ':(80|443|18895|5432|6379)\b' || true

heading "CRAWLER SWITCH AND ELIGIBLE QUEUE"
sql "SELECT key,value FROM settings WHERE key='enabled';"
sql "SELECT status,COUNT(*) AS total,
 COUNT(*) FILTER (WHERE attempts < $MAX_ATTEMPTS) AS under_attempt_limit,
 COUNT(*) FILTER (WHERE attempts >= $MAX_ATTEMPTS) AS exhausted,
 MAX(updated_at) AS newest_job_change
 FROM jobs GROUP BY status ORDER BY status;"
sql "SELECT
 COUNT(*) FILTER (WHERE status IN ('pending','failed') AND attempts < $MAX_ATTEMPTS) AS eligible_normal,
 COUNT(*) FILTER (WHERE status IN ('partial','empty_review') AND attempts < $MAX_ATTEMPTS) AS eligible_with_retry_partial,
 COUNT(*) FILTER (WHERE status='running') AS marked_running,
 COUNT(*) FILTER (WHERE status='running' AND
      (updated_at IS NULL OR LEFT(updated_at,19)::timestamp < NOW()-INTERVAL '30 minutes')) AS stale_running
 FROM jobs;"
sql "SELECT program_key,status,attempts,worker,started_at,updated_at,
 episodes_found,clips_found
 FROM jobs WHERE status='running' ORDER BY updated_at DESC NULLS LAST LIMIT 8;"

heading "LATEST PERSISTED CONTENT / ACTIVITY"
sql "SELECT kind,COUNT(*) AS rows,MAX(last_seen) AS last_saved
 FROM media_items GROUP BY kind ORDER BY kind;"
sql "SELECT level,COUNT(*) AS events,MAX(at) AS newest
 FROM events GROUP BY level ORDER BY level;"
sql "SELECT at,level,LEFT(program_key,40) AS program
 FROM events ORDER BY id DESC LIMIT 18;"
sql "SELECT split_part(last_error,':',1) AS error_type,COUNT(*) AS jobs
 FROM jobs WHERE last_error IS NOT NULL AND last_error<>'' 
 GROUP BY 1 ORDER BY jobs DESC LIMIT 12;"
sql "SELECT COUNT(*) AS channels,COUNT(source_url) AS channels_with_source_url FROM channels;"
sql "SELECT key,value FROM settings WHERE key LIKE 'discovery.last.%'
 ORDER BY value DESC LIMIT 8;"

heading "WEB LATENCY: UNAUTHENTICATED ENDPOINTS"
for endpoint in /health /login /dashboard; do
  curl --noproxy '*' --max-time 15 -sS -o /dev/null \
    -w "endpoint=$endpoint status=%{http_code} connect=%{time_connect}s TTFB=%{time_starttransfer}s total=%{time_total}s\n" \
    "http://127.0.0.1:18895$endpoint" || true
done
echo 'Note: /dashboard is normally a 303 login redirect without a session.'
echo 'Heavy /api/live is authenticated; database EXPLAIN below checks its queries.'

heading "DATABASE PRESSURE"
sql "SELECT state,wait_event_type,wait_event,COUNT(*) AS connections
 FROM pg_stat_activity WHERE datname=current_database()
 GROUP BY state,wait_event_type,wait_event ORDER BY connections DESC;"
sql "SELECT pid,ROUND(EXTRACT(EPOCH FROM clock_timestamp()-query_start))::int AS age_seconds,
 state,wait_event_type,wait_event,LEFT(query,130) AS query_prefix
 FROM pg_stat_activity WHERE datname=current_database()
 AND state='active' AND pid<>pg_backend_pid()
 ORDER BY query_start LIMIT 8;"
sql "SELECT relname,n_live_tup,n_dead_tup,seq_scan,idx_scan,
 last_analyze,last_autoanalyze FROM pg_stat_user_tables
 WHERE schemaname='public' AND relname IN
 ('channels','programs','program_channels','media_items','jobs','events')
 ORDER BY n_live_tup DESC;"
sql "SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;"
echo "--- disk and memory ---"
df -h / /var/lib/postgresql /opt 2>/dev/null
free -h
uptime

heading "PLAYWRIGHT / BROWSER INSTALLATION"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  "$ROOT/.venv/bin/python" -m playwright --version 2>&1 || true
fi
if [[ -d /var/lib/twcrawler/pw-browsers ]]; then
  find /var/lib/twcrawler/pw-browsers -maxdepth 2 -mindepth 1 -type d \
    -printf '%P\n' 2>/dev/null | head -20
else
  echo "[WARN] /var/lib/twcrawler/pw-browsers not found"
fi
if [[ -x /usr/bin/chromium ]]; then /usr/bin/chromium --version 2>&1; fi

heading "TELEWEBION CONNECTIVITY (HEAD ONLY, NO CRAWL)"
curl -4 -I -L -sS --connect-timeout 5 --max-time 15 -o /dev/null \
  -w 'telewebion HTTP=%{http_code} DNS=%{time_namelookup}s connect=%{time_connect}s total=%{time_total}s\n' \
  https://telewebion.net/ 2>&1 || true

heading "WORKER LOG SIGNALS (LAST 100 LINES; SANITIZED)"
journalctl -u twcrawler-worker --no-pager -o cat -n 100 2>/dev/null |
  grep -E '(\[LOOP\]|\[PAUSED\]|\[FAIL\]|\[SAVE\]|\[BROWSER\]|Traceback|Error|ERROR|Exception|batch return|Could not start)' |
  sed -E 's/((password|passwd|secret|token|api[_-]?key|DATABASE_URL)[=: ]+)[^[:space:]]+/\1[REDACTED]/Ig' |
  tail -55 || true

heading "WEB ERROR SIGNALS (LAST 80 LINES; SANITIZED)"
journalctl -u twcrawler-web --no-pager -o cat -n 80 2>/dev/null |
  grep -E '(Traceback|ERROR|Error|Exception|slow|timeout|Timeout|500 )' |
  sed -E 's/((password|passwd|secret|token|api[_-]?key|DATABASE_URL)[=: ]+)[^[:space:]]+/\1[REDACTED]/Ig' |
  tail -35 || true

if [[ "$DEEP" == 1 ]]; then
  heading "DEEP: EXPLAIN ANALYZE OF LIVE CHANNEL AGGREGATION (READ ONLY)"
  sql "EXPLAIN (ANALYZE,BUFFERS,TIMING OFF)
  WITH jobs_by_channel AS (
    SELECT pc.slug,COUNT(*) AS programs,
      COUNT(*) FILTER (WHERE j.status='pending') AS pending
    FROM program_channels pc LEFT JOIN jobs j ON j.program_key=pc.program_key GROUP BY pc.slug
  ), media_by_channel AS (
    SELECT pc.slug,COUNT(*) FILTER (WHERE mi.kind='episode') AS episodes,
      COUNT(*) FILTER (WHERE mi.kind='clip') AS clips
    FROM program_channels pc JOIN media_items mi ON mi.program_key=pc.program_key GROUP BY pc.slug
  )
  SELECT c.slug,j.programs,j.pending,m.episodes,m.clips FROM channels c
  LEFT JOIN jobs_by_channel j USING(slug)
  LEFT JOIN media_by_channel m USING(slug);"
  heading "DEEP: EXPLAIN ANALYZE OF RECENT JOBS (READ ONLY)"
  sql "EXPLAIN (ANALYZE,BUFFERS,TIMING OFF)
  SELECT j.program_key,p.name,COUNT(pc.slug) AS networks
  FROM jobs j JOIN programs p USING(program_key)
  LEFT JOIN program_channels pc ON pc.program_key=j.program_key
  WHERE j.status IN ('running','partial','failed','pending','empty_review')
  GROUP BY j.program_key,p.name,j.status,j.updated_at
  ORDER BY CASE WHEN j.status='running' THEN 0 WHEN j.status='partial' THEN 1
    WHEN j.status='failed' THEN 2 WHEN j.status='pending' THEN 3 ELSE 4 END,
    j.updated_at DESC NULLS LAST LIMIT 80;"
fi

heading "INTERPRETATION"
echo "- enabled=0: Dashboard toggle has not authorized database crawling."
echo "- inactive twcrawler-worker: A running JOB in PostgreSQL does NOT start systemd."
echo "- eligible_normal=0: Worker cannot claim a pending/failed job, even if enabled=1."
echo "- high partial count: TW_RETRY_PARTIAL=false excludes these jobs."
echo "- browser missing / launch errors: Playwright cannot crawl pages."
echo "- HTTP 403/429 or network timeout: source may be blocking/throttling requests."
echo "- Deep query >2s plus 12-second UI polling can create dashboard slowness."
echo "- TW_RESCAN_SECONDS=300 means a running loop can appear idle for ~5 minutes."
echo 'No database rows, enable switch, systemd unit or Nginx configuration were changed.'
printf 'PRIVATE REPORT: %s\n' "$OUT"
