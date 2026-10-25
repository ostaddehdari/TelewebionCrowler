# TelewebionCrowler — Python / PostgreSQL

Persian-language metadata catalog and resumable Playwright crawler, ported from the user's real SQLite snapshot (October 2026). Keeps original program and media identifiers; provides streaming, idempotent SQLite→PostgreSQL import, isolated crawler worker, Persian FastAPI dashboard, and Ubuntu 26.04 systemd deployment.

**Full instructions:** [README_FA.md](README_FA.md)

**Data handling:** No SQLite file, video, credentials, or scraped `raw_json` is tracked in Git. Imports remain private on the server. Crawler does not download videos or bypass access controls.
