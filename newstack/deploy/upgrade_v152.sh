#!/usr/bin/env bash
# Safe, one-command repair of NewCrawler V1.5.1 false provider throttling.
set -Eeuo pipefail
(( EUID==0 )) || { echo '[ERROR] Run as root.' >&2; exit 1; }
APP=/opt/newtwcrawler
CRAWLER="$APP/newtwcrawler/crawler.py"
SERVICE=newtwcrawler-fetch.service
TIMER=newtwcrawler-fetch.timer
PATCH=/root/patch-newtwcrawler-v152.py
URL='https://raw.githubusercontent.com/ostaddehdari/TelewebionCrowler/dcf3f8e11078e44f7b9457200db3cde95411fbb9/newstack/deploy/patch_v152_http_status.py'
[[ -f "$CRAWLER" && -x "$APP/.venv/bin/python" ]] || { echo '[ERROR] NewCrawler installation missing' >&2; exit 2; }
curl -fsSL --retry 3 --max-time 30 "$URL" -o "$PATCH"
python3 -m py_compile "$PATCH"
BACKUP="/root/newtwcrawler-v152-backup-$(date -u +%Y%m%dT%H%M%SZ).py"
cp -p "$CRAWLER" "$BACKUP"
restore(){
  echo '[ROLLBACK] Restoring old crawler.py' >&2
  cp -p "$BACKUP" "$CRAWLER" || true
  systemctl start "$TIMER" || true
}
trap restore ERR
systemctl stop "$TIMER"
systemctl stop "$SERVICE"
python3 "$PATCH" "$CRAWLER"
"$APP/.venv/bin/python" -m py_compile "$CRAWLER"
(cd "$APP" && "$APP/.venv/bin/python" -c 'from newtwcrawler import crawler; assert "urljoin" in crawler.run_once.__code__.co_names')
# Reclassify only proven false HTTP-301 throttle events; no data deletion.
runuser -u postgres -- psql -d newtwcrawler -v ON_ERROR_STOP=1 -P pager=off <<'SQL'
BEGIN;
UPDATE crawl_events
SET level='error', message='[Corrected: HTTP 301, not throttled] ' || message
WHERE level='throttled'
  AND message LIKE 'HTTPStatusError: Redirect response ''301 Moved Permanently''%';
UPDATE crawl_tasks
SET state='queued',attempts=0,next_run=now(),updated_at=now()
WHERE task_type='content' AND last_http=301
  AND params->>'content_id'='0xc20d403'
  AND state IN ('waiting','error','review');
COMMIT;
SQL
systemctl start "$TIMER"
systemctl start --no-block "$SERVICE"
trap - ERR
printf '\n[SUCCESS] V1.5.2 HTTP classifier installed. Backup: %s\n' "$BACKUP"
printf '[INFO] Real HTTP 429 still triggers a global cooldown; 403 is per-task.\n'
printf '[INFO] Monitor: journalctl -u newtwcrawler-fetch.service -n 45 --no-pager\n'
