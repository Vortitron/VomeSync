#!/usr/bin/env bash
# Stage the shared integration into the add-on build context.
# The add-on never maintains a fork of the Python — it always copies from
# custom_components/vomesync (the same tree HACS installs).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SRC="$ROOT/custom_components/vomesync"
DEST="$(cd "$(dirname "$0")" && pwd)/staged_integration"

if [[ ! -d "$SRC" ]]; then
	echo "Missing shared integration at $SRC" >&2
	exit 1
fi

rm -rf "$DEST"
mkdir -p "$DEST"
cp -a "$SRC"/. "$DEST"/
echo "Staged $SRC → $DEST"
