# flake8: noqa
"""Tests against a real, current Home Assistant Core.

``hacs-addon/tests`` runs on the pinned Core 2023.7 (Python 3.10) with a
mock ``hass``. Anything that depends on a newer Core API — web_rtc's ICE
servers arrived in 2024.11 — is checked here instead, on a real ``hass``
from pytest-homeassistant-custom-component, which needs Python 3.13.

Run with ``scripts/run-core-tests.sh``.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

pytest_plugins = ["pytest_homeassistant_custom_component"]
