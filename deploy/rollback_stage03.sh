#!/usr/bin/env bash
# Restore application/UI and Nginx configuration backed up by install_stage03.sh.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || exit 1
BACKUP="$(cat /root/twcrawler-stage03-last-backup)"
[[ "$BACKUP" == /root/twcrawler-stage03-backup-* && -f "$BACKUP/twpg/web.py" ]] || exit 2
ROOT=/opt/telewebion-crawler
cp -a "$BACKUP/twpg/web.py" "$ROOT/twpg/web.py"
rm -rf "$ROOT/twpg/templates"; cp -a "$BACKUP/twpg/templates" "$ROOT/twpg/templates"
if [[ -d "$BACKUP/twpg/static" ]]; then
  rm -rf "$ROOT/twpg/static"; cp -a "$BACKUP/twpg/static" "$ROOT/twpg/static"
fi
if [[ -f "$BACKUP/twpg/auth.py" ]]; then cp -a "$BACKUP/twpg/auth.py" "$ROOT/twpg/auth.py"; fi
cp -a "$BACKUP/nginx-site.conf" /etc/nginx/sites-available/twcrawler-ip.conf
rm -f /etc/systemd/system/twcrawler-web.service.d/10-login.conf
systemctl daemon-reload
systemctl restart twcrawler-web
nginx -t && systemctl reload nginx
echo 'Previous app and Nginx configuration restored; database untouched.'
