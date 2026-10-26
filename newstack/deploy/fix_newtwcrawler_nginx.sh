#!/usr/bin/env bash
# Repair only the NEW independent Nginx route. Never change PostgreSQL or old service.
set -Eeuo pipefail
if (( EUID != 0 )); then echo '[ERROR] Run this script as root.' >&2; exit 1; fi
IP='5.61.28.187'
PORT='18896'
SITE_LINK='/etc/nginx/sites-enabled/twcrawler-ip.conf'

printf '\n[1/5] Verify new application and original website\n'
if [[ ! -e "$SITE_LINK" ]]; then
  echo "[ERROR] Active site link not found: $SITE_LINK" >&2
  echo 'Enabled Nginx site files:'
  ls -l /etc/nginx/sites-enabled/ || true
  exit 2
fi
SITE="$(readlink -f "$SITE_LINK")"
if [[ ! -f "$SITE" ]]; then echo "[ERROR] Broken symlink: $SITE_LINK" >&2; exit 2; fi
printf 'Active site: %s -> %s\n' "$SITE_LINK" "$SITE"
if [[ "$(systemctl is-active newtwcrawler-web)" != active ]]; then
  echo '[ERROR] newtwcrawler-web is not active; repair the app first.' >&2; exit 3
fi
for path in /health / /dashboard /api/stats; do
  code="$(curl --noproxy '*' -sS -o /dev/null --max-time 15 -w '%{http_code}' "http://127.0.0.1:${PORT}${path}" || true)"
  printf '  new app localhost %-16s HTTP %s\n' "$path" "$code"
  if [[ "$code" != 200 ]]; then echo "[ERROR] Upstream path $path returned $code. No Nginx changes made." >&2; exit 4; fi
done
OLD_BEFORE="$(curl --noproxy '*' -ksS -o /dev/null -w '%{http_code}' --max-time 10 --resolve "${IP}:443:127.0.0.1" "https://${IP}/twcrawler/login" || true)"
printf '  old site login status (before): %s\n' "$OLD_BEFORE"

printf '\n[2/5] Check whether the new public route is already present\n'
check_public() {
  curl --noproxy '*' -ksS -o /dev/null -w '%{http_code}' --max-time 15 \
    --resolve "${IP}:443:127.0.0.1" "https://${IP}/newtwcrawler/$1" || true
}
ALREADY="$(check_public '')"
if [[ "$ALREADY" == 200 ]]; then
  echo 'Already fixed: new public homepage HTTP 200 (no changes needed).'
  exit 0
fi
printf '  before: https://%s/newtwcrawler/ -> HTTP %s\n' "$IP" "$ALREADY"

printf '\n[3/5] Save active config, patch only the IP HTTPS server\n'
BACKUP="/root/newtwcrawler-nginx-route-before-$(date -u +%Y%m%dT%H%M%SZ).conf"
cp -p "$SITE" "$BACKUP"
export NEWTWC_SITE="$SITE"
python3 - <<'PY'
from pathlib import Path
import os, re
site=Path(os.environ['NEWTWC_SITE'])
s=site.read_text()
server_blocks=[]
for m in re.finditer(r'(?m)^\s*server\s*\{',s):
    depth=1; end=None
    for j in range(m.end(),len(s)):
        ch=s[j]
        if ch=='{':depth+=1
        elif ch=='}':
            depth-=1
            if depth==0:
                end=j; break
    if end is None:continue
    body=s[m.end():end]
    if re.search(r'(?m)^\s*listen\s+(?:[0-9.]+:)?443\s+[^;]*\bssl\b',body) and re.search(r'(?m)^\s*server_name\s+[^;]*\b5\.61\.28\.187\b',body):
        server_blocks.append((m.end(),end,body))
if len(server_blocks)!=1:
    raise SystemExit(f'[ERROR] Expected exactly one IP SSL virtual host in {site}, found {len(server_blocks)}. No changes made.')
start,end,body=server_blocks[0]
if re.search(r'(?m)^\s*location\s+(?:\^~\s+)?/newtwcrawler/',body):
    raise SystemExit('[ERROR] A /newtwcrawler/ route already exists in the SSL server but did not return 200. No overwrite; inspect route and selected virtual host.')
if 'location ^~ /twcrawler/' not in body:
    raise SystemExit('[ERROR] Existing /twcrawler/ route not found in matching SSL server. Aborting to protect old site.')
idx=body.find('location ^~ /twcrawler/')
insert='''location = /newtwcrawler { return 301 /newtwcrawler/; }
    location ^~ /newtwcrawler/ {
        auth_basic off;
        proxy_pass http://127.0.0.1:18896/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Prefix /newtwcrawler;
        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
    }
    '''
site.write_text(s[:start]+body[:idx]+insert+body[idx:]+s[end:])
print(f'[OK] Inserted /newtwcrawler/ inside active HTTPS server in {site}')
PY

rollback() {
  echo '[ROLLBACK] Restoring original active Nginx site file.' >&2
  cp -p "$BACKUP" "$SITE"
  nginx -t && systemctl reload nginx || true
}
if ! nginx -t; then rollback; exit 6; fi
if ! systemctl reload nginx; then rollback; exit 6; fi
sleep 1

printf '\n[4/5] Test correct IP name/SNI over loopback (GET, not HEAD)\n'
ERROR=0
for path in '' dashboard api/stats; do
  code="$(check_public "$path")"
  printf '  /newtwcrawler/%-12s -> HTTP %s\n' "$path" "$code"
  if [[ "$code" != 200 ]]; then ERROR=1; fi
done
OLD_AFTER="$(curl --noproxy '*' -ksS -o /dev/null -w '%{http_code}' --max-time 10 --resolve "${IP}:443:127.0.0.1" "https://${IP}/twcrawler/login" || true)"
printf '  old site login status (after): %s\n' "$OLD_AFTER"
if [[ "$OLD_BEFORE" != "$OLD_AFTER" ]] || ((ERROR)); then
  echo '[ERROR] Verification failed. The site is restored; collect diagnostics below.' >&2
  rollback
  echo '-- enabled sites --'
  ls -l /etc/nginx/sites-enabled/ || true
  echo '-- active site relevant routes --'
  grep -nE 'server_name|listen [^;]*443|location .*twcrawler|proxy_pass' "$SITE" || true
  echo '-- active nginx configuration listing --'
  nginx -T 2>&1 | grep -E '^# configuration file|server_name |listen .*443|location .*twcrawler' | head -90 || true
  exit 7
fi

printf '\n[5/5] SUCCESS\n'
printf 'URL: https://%s/newtwcrawler/\n' "$IP"
printf 'Config backup: %s\n' "$BACKUP"
echo 'No database, old project files, or crawler services were changed.'
