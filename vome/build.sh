#!/usr/bin/env bash
# Sync the shared integration into the add-on build context.
# The add-on never maintains a fork of the Python — it always copies from
# custom_components/vomesync (the same tree HACS installs).
#
# The copy under vome/custom_components/vomesync is committed so Home Assistant
# Supervisor can build from GitHub (Docker build context is this folder only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/custom_components/vomesync"
DEST="$(cd "$(dirname "$0")" && pwd)/custom_components/vomesync"

if [[ ! -d "$SRC" ]]; then
	echo "Missing shared integration at $SRC" >&2
	exit 1
fi

rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
cp -a "$SRC"/. "$DEST"/
# Bytecode from whatever interpreter last imported the source tree — cpython-310
# here, while the add-on image runs its own version. It is dead weight in the
# build context, and it makes `diff -qr` between the two trees report
# "Files ... differ" on the .pyc — which reads like real source drift in the
# one check used to confirm the mirror is honest. (The source tree keeps its
# own __pycache__; that shows up as a harmless "Only in ..." line instead.)
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} +
# Drop any accidental staged_integration leftover from older layouts.
rm -rf "$(dirname "$0")/staged_integration"
echo "Synced $SRC → $DEST"
