#!/usr/bin/env bash
# Runs on a developer machine with GitHub credentials. Never force-pushes.
set -Eeuo pipefail
REPO=https://github.com/ostaddehdari/TelewebionCrowler.git
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
command -v git >/dev/null || { echo 'Git required' >&2; exit 1; }
git clone "$REPO" "$TMP/repo"
# Copy just tracked source files, never copy secrets, databases or the working .git dir.
(cd "$SOURCE" && tar --exclude=.git --exclude=.venv --exclude='*.sqlite3*' --exclude='__pycache__' --exclude='*.zip' --exclude='*.tgz' --exclude='*.tar.gz' -cf - .) | (cd "$TMP/repo" && tar -xf -)
cd "$TMP/repo"
git add -A
if git diff --cached --quiet; then echo 'Repository already up-to-date'; exit 0; fi
git commit -m 'feat: Persian RTL UI, authenticated login, realtime channel and media dashboards'
git push origin HEAD:main
printf 'Pushed commit: '; git rev-parse HEAD
