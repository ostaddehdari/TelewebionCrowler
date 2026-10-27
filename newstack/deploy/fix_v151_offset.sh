#!/usr/bin/env bash
# Safe V1.5.1 PostgreSQL reserved-column repair for already-transferred V1.5.
set -Eeuo pipefail
[[ "$EUID" -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
cd /root/newtwcrawler-v15
[[ -f deploy/upgrade_v15.sh && -f newtwcrawler/schema_v16.sql ]] || {
 echo "Unpacked V1.5 installer missing" >&2; exit 1;
}
python3 - <<'PY'
from pathlib import Path
fixes = {
 "newtwcrawler/schema.sql":{
    "    offset integer NOT NULL,":"    page_offset integer NOT NULL,"},
 "newtwcrawler/schema_v16.sql":{
    "    offset integer NOT NULL,":"    page_offset integer NOT NULL,"},
 "newtwcrawler/crawler.py":{
    "SELECT offset FROM crawl_page_signatures":"SELECT page_offset FROM crawl_page_signatures",
    "previous['offset']!=offset":"previous['page_offset']!=offset",
    "crawl_page_signatures(task_key,signature,offset)":"crawl_page_signatures(task_key,signature,page_offset)"},
 "tests/test_v15_pagination.py":{
    "SELECT offset FROM crawl_page_signatures":"SELECT page_offset FROM crawl_page_signatures",
    "{'offset':self.sigs[(key,sig)]}":"{'page_offset':self.sigs[(key,sig)]}"},
}
for file, changes in fixes.items():
 p=Path(file)
 if not p.exists() and file.startswith("tests/"):continue
 s=p.read_text(encoding="utf-8")
 for old,new in changes.items():
  if old in s:
   assert s.count(old)==1,(file,old)
   s=s.replace(old,new)
  elif new not in s:
   raise SystemExit("Unexpected source: "+file+": "+old)
 p.write_text(s,encoding="utf-8")
 print("[PATCHED]",file)
PY
python3 -m compileall -q newtwcrawler
bash -n deploy/upgrade_v15.sh
echo "[READY] Starting existing rollback-safe V1.5 installer with fixed SQL"
bash deploy/upgrade_v15.sh