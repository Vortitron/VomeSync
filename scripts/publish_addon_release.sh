#!/usr/bin/env bash
# Publish a packaged add-on build as a GitHub Release.
#
# Called from Jenkinsfile.vome-addon-release's post{success{}} once
# vome/package_release.py has written dist/vome-addon-release.txt and the
# matching zip. Needs GITHUB_TOKEN (repo-scoped) and the two values below
# already exported by the caller:
#
#   RELEASE_TAG_RESOLVED   the tag to create, e.g. vome-addon-v0.3.37
#   RELEASE_ARTIFACT       the zip filename inside dist/, e.g. vome-addon-v0.3.37.zip
#
# Kept as its own script rather than inline in the Jenkinsfile: the
# alternative is a `sh '''...'''` block whose JSON payloads and Groovy
# string interpolation fight each other over which quote characters mean
# what, which is exactly the kind of thing that looks right and silently
# isn't.
set -euo pipefail

: "${GITHUB_TOKEN:?GITHUB_TOKEN must be set}"
: "${RELEASE_TAG_RESOLVED:?RELEASE_TAG_RESOLVED must be set}"
: "${RELEASE_ARTIFACT:?RELEASE_ARTIFACT must be set}"

REPO="Vortitron/VomeSync"
AUTH_HEADER="Authorization: token ${GITHUB_TOKEN}"
ARTIFACT_PATH="dist/${RELEASE_ARTIFACT}"

if [ ! -f "${ARTIFACT_PATH}" ]; then
	echo "Expected artefact not found: ${ARTIFACT_PATH}" >&2
	exit 1
fi

payload=$(python3 -c "
import json
print(json.dumps({
    'tag_name': '${RELEASE_TAG_RESOLVED}',
    'name': '${RELEASE_TAG_RESOLVED}',
    'target_commitish': 'main',
    'generate_release_notes': True,
}))
")

response=$(curl -sS -w '\n%{http_code}' \
	-X POST "https://api.github.com/repos/${REPO}/releases" \
	-H "${AUTH_HEADER}" \
	-H 'Accept: application/vnd.github+json' \
	-d "${payload}")
http_code=$(echo "${response}" | tail -n1)
body=$(echo "${response}" | sed '$d')

if [ "${http_code}" != "201" ]; then
	echo "GitHub release creation failed (HTTP ${http_code}):"
	echo "${body}"
	exit 1
fi

upload_url=$(echo "${body}" | python3 -c "import sys,json; print(json.load(sys.stdin)['upload_url'].split('{')[0])")

asset_response=$(curl -sS -w '\n%{http_code}' \
	-X POST "${upload_url}?name=${RELEASE_ARTIFACT}" \
	-H "${AUTH_HEADER}" \
	-H 'Content-Type: application/zip' \
	--data-binary "@${ARTIFACT_PATH}")
asset_http_code=$(echo "${asset_response}" | tail -n1)

if [ "${asset_http_code}" != "201" ]; then
	echo "Uploading the release asset failed (HTTP ${asset_http_code}):"
	echo "${asset_response}" | sed '$d'
	exit 1
fi

echo "Published ${RELEASE_TAG_RESOLVED} with ${RELEASE_ARTIFACT} attached."
