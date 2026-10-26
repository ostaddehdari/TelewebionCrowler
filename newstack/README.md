# Telewebion NewCrawler — standalone HTTP/API catalog

A separate, PostgreSQL-backed, browser-free crawler and dark Persian RTL web catalog that preserves full JSON responses in JSONB and indexes all discovered IDs, relations, and metadata. Designed for Ubuntu 26.04, reverse-proxied under `/newtwcrawler/`, without modifying the pre-existing `/twcrawler/` app or database.

**Not** an internal Telewebion database dump, and not a downloader of videos or protected streams.

See [Persian installation and coverage guide](README_FA.md).

## Layout

- `newtwcrawler/crawler.py`: confirmed Kandoo JSON endpoint scheduler, pagination, checkpoints and rate limiting
- `newtwcrawler/ingest.py`: fully preserved response documents and recursively indexed entities and links
- `newtwcrawler/schema.sql`: independent PostgreSQL schema
- `newtwcrawler/web.py`: public read-only media browsing, redacted entity/JSON viewer, operational dashboard
- `newtwcrawler/static/`: local CSS and JS (no CDN dependency)
- `newtwcrawler/data/channel_seeds.json`: 89 descriptors from user-provided public JSON sample
- `deploy/install_ubuntu26.sh`: standalone deployment, Nginx route addition and rollback
- `tests/`: sample-driven regression checks

## Run tests

```bash
python -m pip install -r requirements.txt pytest
pytest -q tests/
```


V1.1 public catalog: no website login or session; the server-side PostgreSQL credential remains private. Public JSON views redact token/secret fields.
