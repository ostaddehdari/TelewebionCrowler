"""Channel aggregates shared by private UI and API. Never expose raw JSON."""
from __future__ import annotations

CHANNEL_COUNTS_SQL = """
WITH jobs_by_channel AS (
  SELECT pc.slug,
         COUNT(*) AS programs,
         COUNT(*) FILTER (WHERE j.status='pending') AS pending,
         COUNT(*) FILTER (WHERE j.status='running') AS running,
         COUNT(*) FILTER (WHERE j.status='partial') AS partial,
         COUNT(*) FILTER (WHERE j.status='done') AS done,
         COUNT(*) FILTER (WHERE j.status='failed') AS failed,
         COUNT(*) FILTER (WHERE j.status='empty_review') AS empty_review
  FROM program_channels pc
  LEFT JOIN jobs j ON j.program_key=pc.program_key
  GROUP BY pc.slug
), media_by_channel AS (
  SELECT pc.slug,
         COUNT(*) FILTER (WHERE mi.kind='episode') AS episodes,
         COUNT(*) FILTER (WHERE mi.kind='clip') AS clips
  FROM program_channels pc
  JOIN media_items mi ON mi.program_key=pc.program_key
  GROUP BY pc.slug
)
SELECT c.slug,c.name,c.category,c.kind,c.icon_url,c.source_url,
       COALESCE(j.programs,0)::int AS programs,
       COALESCE(j.pending,0)::int AS pending,
       COALESCE(j.running,0)::int AS running,
       COALESCE(j.partial,0)::int AS partial,
       COALESCE(j.done,0)::int AS done,
       COALESCE(j.failed,0)::int AS failed,
       COALESCE(j.empty_review,0)::int AS empty_review,
       COALESCE(m.episodes,0)::int AS episodes,
       COALESCE(m.clips,0)::int AS clips
FROM channels c
LEFT JOIN jobs_by_channel j USING(slug)
LEFT JOIN media_by_channel m USING(slug)
ORDER BY CASE WHEN c.kind='tv' THEN 0 ELSE 1 END, c.name, c.slug
"""


def all_channels(db):
    return db.execute(CHANNEL_COUNTS_SQL).fetchall()
