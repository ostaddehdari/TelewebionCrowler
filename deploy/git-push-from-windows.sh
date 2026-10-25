#!/usr/bin/env bash
# Run in Git Bash Windows after downloading the source archive.
set -Eeuo pipefail
ARCHIVE="${1:-$HOME/Downloads/TelewebionCrowler_PG_source.tar.gz}"
REPO_DIR="${2:-$HOME/Downloads/TelewebionCrowler}"
[[ -f "$ARCHIVE" ]] || { echo "Archive missing: $ARCHIVE";exit 1; }
if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone https://github.com/ostaddehdari/TelewebionCrowler.git "$REPO_DIR"
fi
cd "$REPO_DIR"
# Overlay new files; do not remove unrelated existing tracked project files.
tar -xzf "$ARCHIVE" -C "$REPO_DIR"
git add README.md README_FA.md .gitignore .env.example requirements.txt twpg deploy tests legacy
if git diff --cached --quiet; then
  echo 'No changes to commit';exit 0
fi
git commit -m 'feat: PostgreSQL-backed Telewebion crawler and SQLite migration'
git push origin HEAD
