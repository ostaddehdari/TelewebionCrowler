#!/usr/bin/env bash
# NewCrawler v1.5.3 safe one-command hotfix: HTTPS .net -> .ir gateway redirects.
set -Eeuo pipefail
(( EUID==0 )) || { echo '[ERROR] Run as root.' >&2; exit 1; }
ROOT=/opt/newtwcrawler
CRAWLER="$ROOT/newtwcrawler/crawler.py"
TIMER=newtwcrawler-fetch.timer
SERVICE=newtwcrawler-fetch.service
PATCH=/root/patch-newtwcrawler-v153.py
[[ -f "$CRAWLER" && -x "$ROOT/.venv/bin/python" ]] || { echo '[ERROR] NewCrawler installation not found.' >&2; exit 2; }
URL='https://raw.githubusercontent.com/ostaddehdari/TelewebionCrowler/601bc4c4d31181d80bf8174a6721756d85e25c20/newstack/deploy/patch_v153_gateway_redirect.py'
curl -fsSL --retry 3 --max-time 30 "$URL" -o "$PATCH"
python3 -m py_compile "$PATCH"
BACKUP="/root/newtwcrawler-v153-backup-$(date -u +%Y%m%dT%H%M%SZ).py"
cp -p "$CRAWLER" "$BACKUP"
recover(){
  echo '[ROLLBACK] Restoring original crawler.py.' >&2
  cp -p "$BACKUP" "$CRAWLER" || true
  systemctl start "$TIMER" || true
}
trap recover ERR
systemctl stop "$TIMER"
systemctl stop "$SERVICE" || true
python3 "$PATCH" "$CRAWLER"
"$ROOT/.venv/bin/python" -m py_compile "$CRAWLER"
(cd "$ROOT" && "$ROOT/.venv/bin/python" -c 'from newtwcrawler.crawler import run_once; assert "original_request_url" in run_once.__code__.co_varnames')
runuser -u postgres -- psql -d newtwcrawler -v ON_ERROR_STOP=1 -P pager=off <<'SQL'
BEGIN;
WITH retry AS (
  UPDATE crawl_tasks
    SET state='queued',next_run=now(),attempts=0,
        last_error=NULL,updated_at=now()
    WHERE last_http=301 AND state IN ('review','error','waiting')
      AND (last_error LIKE '%Redirect outside approved API origin%'
           OR last_error LIKE '%Redirect response%301%')
    RETURNING 1
)
SELECT COUNT(*) AS redirected_tasks_requeued FROM retry;
COMMIT;
SQL
systemctl start "$TIMER"
systemctl start --no-block "$SERVICE"
trap - ERR
printf '[SUCCESS] NewCrawler v1.5.3 gateway redirect hotfix installed.\nBackup: %s\n' "$BACKUP"
echo '[NOTE] Only gateway.telewebion.net and gateway.telewebion.ir are approved; HTTP 403/429 protections remain.'
echo '[CHECK] journalctl -u newtwcrawler-fetch.service --since "3 minutes ago" -n 35 --no-pager'
