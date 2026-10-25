#!/usr/bin/env bash
# Upgrade UI on an existing, healthy TWCrawler PostgreSQL install.
# Does not migrate or modify PostgreSQL, Redis, crawler worker or ACME configuration.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root' >&2; exit 1; }
ROOT=/opt/telewebion-crawler
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE=/etc/nginx/sites-available/twcrawler-ip.conf
PASS_ENV=/etc/telewebion/twcrawler-ui.env
BACKUP="/root/twcrawler-stage03-backup-$(date +%Y%m%d-%H%M%S)"
[[ -f "$ROOT/twpg/web.py" && -f "$ROOT/.venv/bin/uvicorn" && -f "$SITE" ]] || { echo 'Existing application / Nginx site missing. Stop.' >&2; exit 2; }
[[ "$(systemctl is-active twcrawler-web)" == active ]] || { echo 'Web service is not active' >&2; exit 2; }
if ! grep -Eq '^[[:space:]]*listen[[:space:]]+443[[:space:]]+ssl' "$SITE"; then
    echo 'Port 443 is not configured for this site yet. Configure HTTPS first. No changes made.' >&2
    exit 3
fi
mkdir -p "$BACKUP/twpg"
cp -a "$ROOT/twpg/web.py" "$BACKUP/twpg/web.py"
cp -a "$ROOT/twpg/templates" "$BACKUP/twpg/templates"
[[ ! -d "$ROOT/twpg/static" ]] || cp -a "$ROOT/twpg/static" "$BACKUP/twpg/static"
[[ ! -f "$ROOT/twpg/auth.py" ]] || cp -a "$ROOT/twpg/auth.py" "$BACKUP/twpg/auth.py"
cp -a "$SITE" "$BACKUP/nginx-site.conf"
printf '%s\n' "$BACKUP" > /root/twcrawler-stage03-last-backup

echo "[1/5] Backup: $BACKUP"
install -D -m 644 "$SRC/twpg/web.py" "$ROOT/twpg/web.py"
install -D -m 644 "$SRC/twpg/auth.py" "$ROOT/twpg/auth.py"
install -d -m 755 "$ROOT/twpg/static"
for p in "$SRC"/twpg/static/*; do install -m 644 "$p" "$ROOT/twpg/static/$(basename "$p")"; done
for p in "$SRC"/twpg/templates/*; do install -m 644 "$p" "$ROOT/twpg/templates/$(basename "$p")"; done
chown -R root:twcrawler "$ROOT/twpg/static" "$ROOT/twpg/templates"
chmod 755 "$ROOT/twpg/static" "$ROOT/twpg/templates"
# Do not change the DB, worker services, or existing passwords.

echo '[2/5] Configure proper form-based login'
if [[ ! -f "$PASS_ENV" ]]; then
    [[ -t 0 ]] || { echo 'Interactive terminal required to securely enter password' >&2; exit 4; }
    while :; do
        read -r -s -p 'Set manager password (min 14 chars): ' PASSWORD; echo
        if [[ ${#PASSWORD} -ge 14 ]]; then break; fi
        echo 'Password too short (14 chars required).'
    done
    export NEW_TWC_PASSWORD="$PASSWORD"
    umask 077
    ROOT_SECRET="$(openssl rand -hex 48)"
    HASH="$(PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" -c 'from twpg.auth import encode_password; import os; print(encode_password(os.environ["NEW_TWC_PASSWORD"]))')"
    unset NEW_TWC_PASSWORD PASSWORD
    printf 'TWC_UI_USER=manager\nTWC_UI_SECRET=%s\nTWC_UI_PASSWORD_HASH=%s\nTW_BASE_PATH=/twcrawler\n' "$ROOT_SECRET" "$HASH" > "$PASS_ENV"
    chown root:twcrawler "$PASS_ENV"
    chmod 640 "$PASS_ENV"
else
    echo 'Existing application login secret and password retained.'
fi
mkdir -p /etc/systemd/system/twcrawler-web.service.d
cat >/etc/systemd/system/twcrawler-web.service.d/10-login.conf <<'EOF'
[Service]
EnvironmentFile=/etc/telewebion/twcrawler-ui.env
EOF

# Fail-closed: authenticate at app layer before disabling popup Basic Auth.
echo '[3/5] Restart application and verify login gate'
"$ROOT/.venv/bin/python" -m py_compile "$ROOT/twpg/web.py" "$ROOT/twpg/auth.py"
systemctl daemon-reload
systemctl restart twcrawler-web
for i in $(seq 1 18); do
    if curl -fsS -o /dev/null http://127.0.0.1:18895/login; then break; fi
    sleep 1
done
LOGIN_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:18895/login)"
ADMIN_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:18895/dashboard)"
API_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:18895/api/live)"
[[ "$LOGIN_CODE" == 200 && "$ADMIN_CODE" == 303 && "$API_CODE" == 401 ]] || {
  echo "Failed local authentication checks (login=$LOGIN_CODE, page=$ADMIN_CODE, API=$API_CODE). Restore from: $BACKUP" >&2
  exit 5
}

echo '[4/5] Replace Nginx popup authentication with protected app login'
python3 - "$SITE" <<'PY'
from pathlib import Path
import sys
site=Path(sys.argv[1]);content=site.read_text()
needle='location ^~ /twcrawler/ {\n'
if 'auth_basic off; # app-login-stage03' not in content:
    if content.count(needle)!=1:
        raise SystemExit('Unexpected Nginx proxy location; refusing change')
    content=content.replace(needle, needle+'        auth_basic off; # app-login-stage03\n',1)
    site.write_text(content)
PY
if ! nginx -t || ! systemctl reload nginx; then
   cp -a "$BACKUP/nginx-site.conf" "$SITE"
   nginx -t && systemctl reload nginx || true
   echo 'Nginx change reverted; private app login remains enabled.' >&2
   exit 6
fi

# Verify after auth_basic override: HTTP 200 login; HTTP 303 dashboard.
echo '[5/5] End-to-end HTTPS checks'
LOGIN_HTTPS="$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 -H 'Host: 5.61.28.187' https://127.0.0.1/twcrawler/login)"
PRIVATE_HTTPS="$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 -H 'Host: 5.61.28.187' https://127.0.0.1/twcrawler/dashboard)"
echo "HTTPS login: $LOGIN_HTTPS | Protected page: $PRIVATE_HTTPS"
if [[ "$LOGIN_HTTPS" != 200 || "$PRIVATE_HTTPS" != 303 ]]; then
   cp -a "$BACKUP/nginx-site.conf" "$SITE"
   nginx -t && systemctl reload nginx || true
   echo 'HTTPS verification failed; Nginx Basic Auth reverted. Inspect application logs.' >&2
   exit 7
fi
printf '\nSUCCESS: https://5.61.28.187/twcrawler/login\nBackups: %s\n' "$BACKUP"
echo 'Note: temporary self-signed certificate is NOT a trusted public certificate. Keep SSH tunnel for sensitive administration until ACME succeeds.'
