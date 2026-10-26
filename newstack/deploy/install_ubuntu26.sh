#!/usr/bin/env bash
# Creates a NEW PostgreSQL DB/user + independent Python app + Nginx path.
# Existing twcrawler app/service/data are untouched. Only append a new Nginx location.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Must run as root'; exit 1; }
ROOT=/opt/newtwcrawler
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG_DIR=/etc/newtwcrawler
ENV_FILE=$CFG_DIR/app.env
ROLE=newtwcrawler
DB=newtwcrawler
NGINX=/etc/nginx/sites-available/twcrawler-ip.conf

[[ -f "$SRC/newtwcrawler/schema.sql" ]] || { echo 'Package missing schema'; exit 1; }
[[ -f "$NGINX" ]] || { echo 'Existing SSL Nginx virtual host missing; no changes made'; exit 1; }
if ! grep -Eq 'listen[[:space:]]+443[[:space:]]+ssl' "$NGINX"; then
 echo 'Existing HTTPS virtual host is not configured; refusing to publish new path' >&2; exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip postgresql-client postgresql curl openssl
id "$ROLE" &>/dev/null || useradd --system --create-home --home-dir /var/lib/newtwcrawler --shell /usr/sbin/nologin "$ROLE"
install -d -m 750 -o root -g "$ROLE" "$CFG_DIR"
install -d -m 750 -o "$ROLE" -g "$ROLE" /var/lib/newtwcrawler

if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_roles WHERE rolname='$ROLE'" | grep -qx 1; then
  runuser -u postgres -- createuser --no-superuser --no-createdb --no-createrole "$ROLE"
fi
if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -qx 1; then
  runuser -u postgres -- createdb --owner="$ROLE" "$DB"
fi
if [[ ! -f "$ENV_FILE" ]]; then
  # Do not rotate an existing role's password without an existing config.
  if runuser -u postgres -- psql -d "$DB" -Atqc "SELECT EXISTS(SELECT 1 FROM public.entities)" >/dev/null 2>&1; then
    echo 'Existing DB tables and missing config: refusing to reset credentials' >&2; exit 1
  fi
  PASS="$(openssl rand -hex 36)"
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -v dbpass="$PASS" <<'SQL'
ALTER ROLE newtwcrawler WITH LOGIN PASSWORD :'dbpass';
SQL
  while :; do
    read -r -s -p 'New site manager password (14+ chars): ' WEBPASS; echo
    [[ ${#WEBPASS} -ge 14 ]] && break
    echo 'Password too short.'
  done
  HASH="$(printf '%s' "$WEBPASS" | PYTHONPATH="$SRC" python3 -c 'from newtwcrawler.auth import hash_password; import sys; print(hash_password(sys.stdin.read()))')"
  unset WEBPASS
  SECRET="$(openssl rand -hex 48)"
  umask 077
  cat > "$ENV_FILE" <<EOF
NEWTW_DATABASE_URL=postgresql://newtwcrawler:$PASS@127.0.0.1:5432/newtwcrawler
NEWTW_SESSION_SECRET=$SECRET
NEWTW_PASSWORD_HASH=$HASH
EOF
  chown root:"$ROLE" "$ENV_FILE" && chmod 640 "$ENV_FILE"
  unset PASS SECRET HASH
else
  echo '[KEEP] Existing newtwcrawler credentials preserved'
fi

install -d -m 750 -o root -g "$ROLE" "$ROOT"
cp -a "$SRC/newtwcrawler" "$ROOT/"
cp -a "$SRC/requirements.txt" "$ROOT/requirements.txt"
find "$ROOT/newtwcrawler" -type f -exec chmod 644 {} +
find "$ROOT/newtwcrawler" -type d -exec chmod 755 {} +
chown -R root:"$ROLE" "$ROOT"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -r "$ROOT/requirements.txt"
chown -R root:"$ROLE" "$ROOT/.venv"
chmod 750 "$ROOT"

NEWTW_DATABASE_URL="$(sed -n 's/^NEWTW_DATABASE_URL=//p' "$ENV_FILE")"
runuser -u "$ROLE" -- env NEWTW_DATABASE_URL="$NEWTW_DATABASE_URL" \
  "$ROOT/.venv/bin/python" -m newtwcrawler.crawler bootstrap
unset NEWTW_DATABASE_URL

cat >/etc/systemd/system/newtwcrawler-web.service <<EOF
[Unit]
Description=Independent Telewebion HTTP data catalog
After=postgresql.service network-online.target
Wants=network-online.target
[Service]
Type=simple
User=$ROLE
Group=$ROLE
WorkingDirectory=$ROOT
EnvironmentFile=$ENV_FILE
ExecStart=$ROOT/.venv/bin/uvicorn newtwcrawler.web:app --host 127.0.0.1 --port 18896 --workers 2
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/newtwcrawler
UMask=0027
[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/newtwcrawler-fetch.service <<EOF
[Unit]
Description=Browser-free Telewebion API crawler
After=postgresql.service network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=$ROLE
Group=$ROLE
WorkingDirectory=$ROOT
EnvironmentFile=$ENV_FILE
ExecStart=$ROOT/.venv/bin/python -u -m newtwcrawler.crawler run --max-requests 40 --delay 1.5
TimeoutStartSec=900
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/newtwcrawler
UMask=0027
EOF
cat >/etc/systemd/system/newtwcrawler-fetch.timer <<'EOF'
[Unit]
Description=Periodically collect public Telewebion API metadata
[Timer]
OnBootSec=3min
OnUnitInactiveSec=5min
Persistent=true
Unit=newtwcrawler-fetch.service
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now newtwcrawler-web
for i in {1..20}; do
  if curl -fsS -o /dev/null http://127.0.0.1:18896/health; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:18896/health

# Insert a sibling route *within the existing TLS server*, preserving old contents.
BACKUP="/root/newtwcrawler-nginx-$(date -u +%Y%m%dT%H%M%SZ).conf"
cp -a "$NGINX" "$BACKUP"
python3 - "$NGINX" <<'PY'
from pathlib import Path
import sys
file=Path(sys.argv[1]); s=file.read_text()
if 'location ^~ /newtwcrawler/' in s:
    print('Nginx route already configured; leaving existing site intact')
else:
    marker='location ^~ /twcrawler/ {'
    at=s.find(marker)
    if at==-1 or s.rfind('listen 443 ssl',0,at)==-1:
        raise SystemExit('Expected TLS twcrawler location not found; no Nginx changes')
    insert='''location = /newtwcrawler { return 301 /newtwcrawler/; }
    location ^~ /newtwcrawler/ {
        auth_basic off;
        proxy_pass http://127.0.0.1:18896/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 15s;
        proxy_read_timeout 60s;
    }
    '''
    file.write_text(s[:at]+insert+s[at:])
    print('Added new /newtwcrawler/ location. Existing /twcrawler/ unchanged.')
PY
if ! nginx -t; then
  cp -a "$BACKUP" "$NGINX"
  nginx -t || true
  echo "Nginx invalid; restored backup $BACKUP" >&2
  exit 7
fi
systemctl reload nginx
LOGIN_CODE="$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 -H 'Host: 5.61.28.187' https://127.0.0.1/newtwcrawler/login)"
PROTECTED_CODE="$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 -H 'Host: 5.61.28.187' https://127.0.0.1/newtwcrawler/dashboard)"
if [[ "$LOGIN_CODE" != 200 || "$PROTECTED_CODE" != 303 ]]; then
 cp -a "$BACKUP" "$NGINX"; nginx -t && systemctl reload nginx || true
 echo "Nginx checks failed: login=$LOGIN_CODE protected=$PROTECTED_CODE. Restored config." >&2; exit 8
fi

# First pilot extraction is a small bounded batch; timer stays disabled until review.
echo '[PILOT] Running up to 40 limited direct HTTP requests'
systemctl start newtwcrawler-fetch.service || true
journalctl -u newtwcrawler-fetch.service -n 25 --no-pager || true
cat <<INFO
SUCCESS - independent app initialized.
LOGIN: https://5.61.28.187/newtwcrawler/login
STATUS: https://5.61.28.187/newtwcrawler/dashboard
DATABASE: newtwcrawler (separate from twcrawler)
Nginx backup: $BACKUP
The timer is NOT yet enabled. Review pilot logs before enabling:
  systemctl enable --now newtwcrawler-fetch.timer
HTTPS uses the existing certificate; if it is self-signed, browser will warn.
INFO
