#!/usr/bin/env python3
"""NewCrawler 1.5.4: ensure program episodes and clips cannot starve in job queue."""
from pathlib import Path
import sys

path=Path(sys.argv[1]) if len(sys.argv)>1 else Path('/opt/newtwcrawler/newtwcrawler/crawler.py')
src=path.read_text(encoding='utf-8')
marker='V1.5.4 type-first fair scheduling'
if marker in src:
    print('[OK] Queue fairness already patched')
    sys.exit(0)
old="""            row=db.execute('''SELECT * FROM crawl_tasks
              WHERE state IN ('queued','waiting') AND next_run<=now()
              ORDER BY CASE WHEN cursor_offset>0 THEN 0 ELSE 1 END,
               CASE WHEN task_type=%s THEN 0 ELSE 1 END,
               priority ASC,next_run ASC,created_at ASC
              FOR UPDATE SKIP LOCKED LIMIT 1''',(preferred or '',)).fetchone()"""
new="""            # V1.5.4 type-first fair scheduling. Offset continuation is
            # prioritized only inside the chosen job type, not globally.
            row=None
            if preferred:
                row=db.execute('''SELECT * FROM crawl_tasks
                  WHERE state IN ('queued','waiting') AND next_run<=now()
                    AND task_type=%s
                  ORDER BY CASE WHEN cursor_offset>0 THEN 0 ELSE 1 END,
                    priority ASC,created_at ASC,next_run ASC
                  FOR UPDATE SKIP LOCKED LIMIT 1''',(preferred,)).fetchone()
            if not row:
                row=db.execute('''SELECT * FROM crawl_tasks
                  WHERE state IN ('queued','waiting') AND next_run<=now()
                  ORDER BY CASE WHEN cursor_offset>0 THEN 0 ELSE 1 END,
                    priority ASC,created_at ASC,next_run ASC
                  FOR UPDATE SKIP LOCKED LIMIT 1''').fetchone()"""
if src.count(old)!=1:
    raise SystemExit('[STOP] Expected exactly one v1.5.3 claim() pattern; no changes made')
result=src.replace(old,new,1)
compile(result,str(path),'exec')
path.write_text(result,encoding='utf-8')
print('[PATCHED] fair per-type queue scheduling; no DB records changed')
