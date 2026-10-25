#!/usr/bin/env bash
# Existing PostgreSQL 18 / Redis / Nginx are preserved; no database is destroyed.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root'; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$ROOT/twpg/schema.sql" ]] || { echo 'Missing source files'; exit 1; }
SVC=twcrawler
CFG=/etc/telewebion/twcrawler.env
PRIVATE=/srv/telewebion/private

echo '[1] Packages & services'
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip python3-dev \
  postgresql-client postgresql libpq-dev build-essential ca-certificates curl openssl
systemctl enable --now postgresql
systemctl enable --now nginx

id "$SVC" >/dev/null 2>&1 || useradd --system --home-dir /var/lib/twcrawler --create-home --shell /usr/sbin/nologin "$SVC"
install -d -o "$SVC" -g "$SVC" -m 750 "$PRIVATE" "$PRIVATE/imports" /var/lib/twcrawler
install -d -m 750 /etc/telewebion
chown root:"$SVC" /etc/telewebion

# No predictable database password, no password printed, no global PostgreSQL listen changes.
echo '[2] PostgreSQL role and database'
ROLE_EXISTS=0
if runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_roles WHERE rolname='twcrawler'" | grep -qx 1; then ROLE_EXISTS=1; fi
if [[ "$ROLE_EXISTS" == 1 && ! -f "$CFG" ]]; then
  echo '[SAFETY] Database role twcrawler already exists but config file is missing.' >&2
  echo 'Refusing to rotate an unknown existing password. Resolve manually.' >&2
  exit 5
fi
if [[ "$ROLE_EXISTS" == 0 ]]; then
  runuser -u postgres -- createuser --no-superuser --no-createdb --no-createrole twcrawler
fi
if ! runuser -u postgres -- psql -Atqc "SELECT 1 FROM pg_database WHERE datname='twcrawler'" | grep -qx 1; then
  runuser -u postgres -- createdb --owner=twcrawler twcrawler
fi
if [[ ! -f "$CFG" ]]; then
  DBPASS="$(openssl rand -hex 36)"
  runuser -u postgres -- psql -v ON_ERROR_STOP=1 -v dbpass="$DBPASS" <<'SQL'
ALTER ROLE twcrawler WITH LOGIN PASSWORD :'dbpass';
SQL
  cat > "$CFG" <<ENV
DATABASE_URL=postgresql://twcrawler:${DBPASS}@127.0.0.1:5432/twcrawler
TW_DELAY_MS=1500
TW_RESCAN_SECONDS=300
TW_MAX_ATTEMPTS=3
TW_BATCH_LIMIT=5
TW_RETRY_PARTIAL=false
TW_IMPORT_DIR=$PRIVATE/imports
PLAYWRIGHT_BROWSERS_PATH=/var/lib/twcrawler/pw-browsers
HOME=/var/lib/twcrawler
ENV
  chown root:"$SVC" "$CFG"
  chmod 640 "$CFG"
else
  echo '[KEEP] Existing database secrets preserved'
fi

# Virtualenv packages managed per project, no system-wide pip installations.
echo '[3] Python environment'
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"
chmod -R a+rX "$ROOT/twpg" "$ROOT/.venv"  # project scripts are non-secret
chmod 750 "$ROOT"
chown root:"$SVC" "$ROOT"

echo '[4] Create schema (no imported rows modified)'
set -a; source "$CFG"; set +a
runuser -u "$SVC" -- env DATABASE_URL="$DATABASE_URL" "$ROOT/.venv/bin/python" -c 'from twpg.db import initialize; initialize(); print("PG SCHEMA OK")'

echo '[5] systemd services'
cat >/etc/systemd/system/twcrawler-web.service <<EOF2
[Unit]
Description=Telewebion PostgreSQL private dashboard
After=network-online.target postgresql.service
Wants=network-online.target
[Service]
User=$SVC
Group=$SVC
WorkingDirectory=$ROOT
EnvironmentFile=$CFG
ExecStart=$ROOT/.venv/bin/uvicorn twpg.web:app --host 127.0.0.1 --port 18895 --workers 2
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$PRIVATE /var/lib/twcrawler
UMask=0027
[Install]
WantedBy=multi-user.target
EOF2
cat >/etc/systemd/system/twcrawler-worker.service <<EOF2
[Unit]
Description=Telewebion PostgreSQL metadata crawler
After=network-online.target postgresql.service
Wants=network-online.target
[Service]
User=$SVC
Group=$SVC
WorkingDirectory=$ROOT
EnvironmentFile=$CFG
ExecStart=$ROOT/.venv/bin/python -u -m twpg.service_loop
Restart=on-failure
RestartSec=30
KillMode=control-group
TimeoutStopSec=120
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$PRIVATE /var/lib/twcrawler
UMask=0027
[Install]
WantedBy=multi-user.target
EOF2
systemctl daemon-reload
systemctl enable --now twcrawler-web
# Deliberately don't activate crawler until transfer & browser pilot succeed.

echo '[6] Browser installation (best-effort; site works even if browser CDN is blocked)'
"$ROOT/.venv/bin/python" -m playwright install-deps chromium || echo '[WARN] playwright system dependencies need attention'
if ! runuser -u "$SVC" -- env HOME=/var/lib/twcrawler PLAYWRIGHT_BROWSERS_PATH=/var/lib/twcrawler/pw-browsers \
    "$ROOT/.venv/bin/python" -m playwright install chromium; then
  echo '[WARN] Chromium download failed. Do not activate worker until browser is installed.'
fi

echo '========================================='
echo 'SITE : http://127.0.0.1:18895 (localhost only)'
echo 'DATA : /srv/telewebion/private/imports/'
echo 'DB   : twcrawler (localhost PostgreSQL)'
echo 'WORKER remains OFF pending pilot test.'
echo '========================================='
