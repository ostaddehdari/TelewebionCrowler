#!/usr/bin/env bash
# Optional daily discovery of NEW program parents. Only after a successful manual pilot.
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
APP=/opt/telewebion-crawler
[[ -f "$APP/twpg/discover_all.py" && -f /etc/telewebion/twcrawler.env ]] || { echo 'Install Stage02 first' >&2; exit 1; }
cat >/etc/systemd/system/twcrawler-discovery.service <<'EOF'
[Unit]
Description=Telewebion public channel catalog discovery
After=network-online.target postgresql.service
Wants=network-online.target
[Service]
Type=oneshot
User=twcrawler
Group=twcrawler
WorkingDirectory=/opt/telewebion-crawler
EnvironmentFile=/etc/telewebion/twcrawler.env
ExecStart=/opt/telewebion-crawler/.venv/bin/python -m twpg.discover_all --all --scrolls 20 --min-age-hours 24 --gap-seconds 15
TimeoutStartSec=6h
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/telewebion/private /var/lib/twcrawler
UMask=0027
EOF
cat >/etc/systemd/system/twcrawler-discovery.timer <<'EOF'
[Unit]
Description=Once-daily Telewebion catalog discovery
[Timer]
OnCalendar=*-*-* 03:30:00 UTC
RandomizedDelaySec=1800
Persistent=true
Unit=twcrawler-discovery.service
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now twcrawler-discovery.timer
systemctl list-timers --all twcrawler-discovery.timer --no-pager
