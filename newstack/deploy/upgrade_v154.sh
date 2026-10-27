#!/usr/bin/env bash
# V1.5.4: Stop queued program_episodes / program_clips from starving.
# Only patches Python scheduling; does not change database or user accounts.
set -Eeuo pipefail
(( EUID==0 )) || { echo '[ERROR] Run as root' >&2; exit 1; }

APP=/opt/newtwcrawler
PY="$APP/.venv/bin/python"
CRAWLER="$APP/newtwcrawler/crawler.py"
TIMER=newtwcrawler-fetch.timer
SERVICE=newtwcrawler-fetch.service
PATCH=/root/patch-newtwcrawler-v154.py
URL='https://raw.githubusercontent.com/ostaddehdari/TelewebionCrowler/1c266b10eea59e1da41e91d574c70e9fd1051d81/newstack/deploy/patch_v154_queue_fairness.py'
[[ -f "$CRAWLER" && -x "$PY" ]] || { echo '[ERROR] NewCrawler not found' >&2; exit 2; }

# Download and compile before touching services.
curl -fLsS --retry 3 --max-time 30 -o "$PATCH" "$URL"
python3 -m py_compile "$PATCH"

TIMER_WAS_RUNNING=0
if systemctl is-active --quiet "$TIMER"; then TIMER_WAS_RUNNING=1; fi
BACKUP="/root/newtwcrawler-v154-backup-$(date -u +%Y%m%dT%H%M%SZ).py"
cp -p "$CRAWLER" "$BACKUP"

restore(){
 echo '[ROLLBACK] Restoring original crawler.py' >&2
 cp -p "$BACKUP" "$CRAWLER" || true
 if ((TIMER_WAS_RUNNING)); then systemctl start "$TIMER" || true; fi
}
trap restore ERR
systemctl stop "$TIMER"
systemctl stop "$SERVICE"
python3 "$PATCH" "$CRAWLER"
"$PY" -m py_compile "$CRAWLER"
(cd "$APP" && "$PY" -c "import inspect; from newtwcrawler.crawler import claim; assert 'AND task_type=%s' in inspect.getsource(claim)")
if ((TIMER_WAS_RUNNING)); then
 systemctl start "$TIMER"
 systemctl start --no-block "$SERVICE"
 echo '[RUNNING] Timer was active before upgrade; it has been restarted.'
else
 echo '[STOPPED] Timer was stopped before upgrade and remains stopped.'
fi
trap - ERR
echo "[SUCCESS] V1.5.4 fair queue scheduler installed. Backup: $BACKUP"
echo '[INFO] No SQL data, tasks, offsets, media or user tokens changed.'
echo '[INFO] To resume: systemctl start newtwcrawler-fetch.timer && systemctl start --no-block newtwcrawler-fetch.service'
