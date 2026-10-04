#!/usr/bin/env bash
# Tests that need a current Home Assistant Core (hacs-addon/tests_core).
# The main suite pins Core 2023.7 on Python 3.10; these need Python 3.13,
# so they get their own venv, built with uv on first run.
set -euo pipefail
cd "$(dirname "$0")/.."
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
if [ ! -x .venv-core/bin/python ]; then
	"$UV" venv -q --python 3.13 .venv-core
	# websockets: the manifest requirement HA would install for the integration.
	"$UV" pip install -q --python .venv-core/bin/python pytest-homeassistant-custom-component "websockets>=11.0.0"
fi
exec .venv-core/bin/python -m pytest hacs-addon/tests_core -q -p no:cacheprovider -o asyncio_mode=auto "$@"
