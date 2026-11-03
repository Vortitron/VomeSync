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
# What the Vome AI entities need loaded (ai_task pulls in conversation and
# camera; stt the Assist pipeline): the requirements of those Core
# components, which pytest-homeassistant-custom-component does not install.
# Pinned to what Core 2026.2 asks for; added to an existing venv too.
if ! .venv-core/bin/python -c "import hassil, turbojpeg, pymicro_vad" 2>/dev/null; then
	"$UV" pip install -q --python .venv-core/bin/python "hassil==3.5.0" \
		"home-assistant-intents==2026.1.28" "PyTurboJPEG==1.8.0" \
		"pymicro-vad==1.0.1" "pyspeex-noise==1.0.2"
fi
exec .venv-core/bin/python -m pytest hacs-addon/tests_core -q -p no:cacheprovider -o asyncio_mode=auto "$@"
