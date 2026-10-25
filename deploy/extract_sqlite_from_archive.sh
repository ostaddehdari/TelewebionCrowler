#!/usr/bin/env bash
# Safely extract only the exact sqlite member from the user's Windows transfer archive.
set -Eeuo pipefail
[[ $# == 1 ]] || { echo 'Usage: extract_sqlite_from_archive.sh /path/to/telewebion_server_transfer.tar.gz'; exit 2; }
[[ $(id -u) -eq 0 ]] || { echo 'Run as root'; exit 1; }
DEST=/srv/telewebion/private/imports
MEMBER=Telewebion_Episode_Catalog_V1/data/telewebion.sqlite3
install -d -o twcrawler -g twcrawler -m 750 "$DEST"
SOURCE="$(realpath "$1")"
[[ -f "$SOURCE" ]] || { echo "Archive missing: $SOURCE";exit 3; }
TMP="$(mktemp "$DEST/.incoming-XXXXXXXX")"
trap 'rm -f "$TMP"' EXIT
# No extraction of arbitrary tar paths or symbolic links.
tar -xOf "$SOURCE" "$MEMBER" > "$TMP"
[[ "$(head -c 16 "$TMP")" == "SQLite format 3"* ]] || { echo 'Not a SQLite DB';exit 4; }
chown twcrawler:twcrawler "$TMP"
chmod 600 "$TMP"
mv -f "$TMP" "$DEST/telewebion.sqlite3"
trap - EXIT
printf 'SQLite stored privately: %s\n' "$DEST/telewebion.sqlite3"
ls -lh "$DEST/telewebion.sqlite3"
