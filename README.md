# Telewebion Crawler / PostgreSQL

Python crawler and dashboard for Telewebion metadata backed by PostgreSQL 18.

## Deployment

The existing Ubuntu 26.04 installation is in `/opt/telewebion-crawler`, served by Nginx at `/twcrawler/`.

### UI Stage 03

Stage 03 adds a Persian RTL dashboard with separate episode and clip counters, per-network metrics, live polling, searchable extraction jobs, a right sidebar, and form-based login. Deploy the `TelewebionCrawler-Stage03-UI.zip` release bundle into the existing app with `bash deploy/install_stage03.sh` (back up first; verify HTTPS is enabled). See `README_STAGE03_FA.md` in the release bundle.

The project is metadata-oriented. Do not commit production SQLite databases, credentials, downloaded media, or secrets.
