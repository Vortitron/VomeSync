# flake8: noqa
"""YAML is changed by the Vome app, never by the integration.

HACS review (20 Oct 2026): an integration must not write the owner's YAML
configuration.  The relay's file API still reads it and still changes any
other file, but a write, delete or move of YAML goes to the Vome app
(``vome/panel/integration_channel.py``) over the channel in
``custom_components/vomesync/app_channel.py``, and without the app it is
refused with the reason.
"""
import asyncio
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.vomesync import app_channel, file_ops
from custom_components.vomesync.const import DOMAIN
from custom_components.vomesync.relay_client import RelayClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vome" / "panel"))
import integration_channel  # noqa: E402


def _client(config_dir, executor=None):
	hass = SimpleNamespace(config=SimpleNamespace(path=lambda *a: str(config_dir)), data={})

	async def _executor(fn, *args):
		return fn(*args)

	hass.async_add_executor_job = executor or _executor
	return RelayClient(hass, server_id="rly-1", secret="sek", session=AsyncMock())


# ── The relay, with no Vome app ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_yaml_is_read_but_not_changed_without_the_app(tmp_path):
	cfg = tmp_path / "configuration.yaml"
	cfg.write_text("homeassistant:\n", encoding="utf-8")
	(tmp_path / "draft.yml").write_text("a: 1\n", encoding="utf-8")
	client = _client(tmp_path)

	status, body, _error = await client.execute("GET", "/read?path=configuration.yaml", None, "files")
	assert status == 200 and json.loads(body)["content"] == "homeassistant:\n"

	for method, path, body in (
		("POST", "/write?path=configuration.yaml", {"content": "x: 1\n"}),
		("POST", "/write?path=packages/new.YAML", {"content": "x: 1\n"}),
		("POST", "/delete?path=draft.yml", None),
		("POST", "/move?path=draft.yml&to=kept.txt", None),
	):
		status, _body, error = await client.execute(method, path, body, "files")
		assert status == 0, path
		assert error == app_channel.APP_MISSING, path
	assert cfg.read_text() == "homeassistant:\n"
	assert (tmp_path / "draft.yml").exists()
	assert not (tmp_path / "packages").exists() and not (tmp_path / "kept.txt").exists()


@pytest.mark.asyncio
async def test_other_files_are_still_changed_without_the_app(tmp_path):
	client = _client(tmp_path)
	status, _body, error = await client.execute(
		"POST", "/write?path=www/card.js", {"content": "x"}, "files")
	assert status == 200 and error is None
	assert (tmp_path / "www" / "card.js").read_text() == "x"
	status, _body, error = await client.execute(
		"POST", "/move?path=www/card.js&to=www/old.js", None, "files")
	assert status == 200 and error is None


@pytest.mark.asyncio
async def test_a_link_with_another_name_cannot_hide_yaml(tmp_path):
	cfg = tmp_path / "configuration.yaml"
	cfg.write_text("homeassistant:\n", encoding="utf-8")
	(tmp_path / "notes.txt").symlink_to(cfg)
	(tmp_path / "plain.txt").write_text("x", encoding="utf-8")
	client = _client(tmp_path)
	for method, path, body in (
		("POST", "/write?path=notes.txt", {"content": "gone"}),
		("POST", "/delete?path=notes.txt", None),
		("POST", "/move?path=plain.txt&to=configuration.yaml&overwrite=1", None),
	):
		status, _body, error = await client.execute(method, path, body, "files")
		assert status == 0 and error == app_channel.APP_MISSING, path
	assert cfg.read_text() == "homeassistant:\n"


@pytest.mark.asyncio
async def test_yaml_changes_are_handed_to_the_app(tmp_path, vome_app):
	(tmp_path / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
	client = _client(tmp_path)
	status, body, error = await client.execute(
		"POST", "/write?path=/configuration.yaml", {"content": "homeassistant:\n  name: x\n"}, "files")
	assert status == 200 and error is None
	assert json.loads(body) == {"path": "configuration.yaml", "written": True, "bytes": 25}
	assert vome_app == [("/files", {
		"op": "write", "path": "configuration.yaml",
		"body": {"content": "homeassistant:\n  name: x\n"},
	})]


@pytest.mark.asyncio
async def test_paths_are_resolved_off_the_event_loop(tmp_path, monkeypatch):
	# HACS review: resolve() and is_dir() stat the disk, so they run in the
	# executor with the operation, never before it.
	(tmp_path / "www").mkdir()
	media = tmp_path / "media"
	media.mkdir()
	(media / "a.jpg").write_bytes(b"x")
	inside = {"now": False}

	async def _executor(fn, *args):
		inside["now"] = True
		try:
			return fn(*args)
		finally:
			inside["now"] = False

	client = _client(tmp_path, _executor)
	client._hass.config.media_dirs = {"local": str(media)}
	real = file_ops.resolve_under

	def _checked(*args, **kwargs):
		assert inside["now"], "a path was resolved on the event loop"
		return real(*args, **kwargs)

	monkeypatch.setattr(file_ops, "resolve_under", _checked)
	for path in ("/list?path=www", "/read?path=www", "/media_list?path=", "/media_read?path=a.jpg"):
		status, _body, error = await client.execute("GET", path, None, "files")
		assert error is None or "directory" in error or "Not a file" in error, (path, error)


# ── The integration's end of the channel ────────────────────────────────


def _hass():
	return SimpleNamespace(data={DOMAIN: {}})


@pytest.mark.parametrize("url", [
	"http://a0d7b954-vome:8098",
	"http://local-vome:8098/",
	"http://172.30.33.4:8098",
])
def test_the_app_checks_in_from_home_assistants_network(url):
	hass = _hass()
	answer = app_channel.check_in(hass, url)
	assert len(answer["secret"]) >= 32
	assert app_channel.available(hass)
	assert app_channel.check_in(hass, url)["secret"] == answer["secret"]


@pytest.mark.parametrize("url", [
	"https://a0d7b954-vome:8098", "http://vome.io:8098", "http://8.8.8.8:8098",
	"http://a0d7b954-vome", "http://user:pw@local-vome:8098", "http://local-vome:8098/x",
	"http://local-vome:8098?x=1", "", None, 5,
])
def test_an_address_off_that_network_is_refused(url):
	hass = _hass()
	with pytest.raises(ValueError):
		app_channel.check_in(hass, url)
	assert not app_channel.available(hass)


def test_an_app_that_stopped_checking_in_is_gone(monkeypatch):
	hass = _hass()
	app_channel.check_in(hass, "http://local-vome:8098")
	later = time.monotonic() + app_channel.STALE_AFTER + 1
	monkeypatch.setattr(app_channel.time, "monotonic", lambda: later)
	assert not app_channel.available(hass)


@pytest.mark.asyncio
async def test_the_request_carries_the_secret_and_reports_a_refusal(monkeypatch):
	hass = _hass()
	secret = app_channel.check_in(hass, "http://local-vome:8098")["secret"]
	sent = {}

	class _Resp:
		def __init__(self, status, data):
			self.status, self._data = status, data

		async def __aenter__(self):
			return self

		async def __aexit__(self, *exc):
			return False

		async def json(self, content_type=None):
			return self._data

	answers = iter([(200, {"result": {"written": True}}), (400, {"error": "Only YAML files are handled here."})])

	class _Session:
		def post(self, url, json, timeout, headers):
			sent.update(url=url, json=json, headers=headers)
			return _Resp(*next(answers))

	monkeypatch.setattr(app_channel, "async_get_clientsession", lambda _hass: _Session())
	answer, problem = await app_channel.async_request(hass, "/files", {"op": "write"})
	assert answer == {"result": {"written": True}} and problem is None
	assert sent["url"] == "http://local-vome:8098/files"
	assert sent["headers"] == {"Authorization": f"Bearer {secret}"}
	answer, problem = await app_channel.async_request(hass, "/files", {"op": "write"})
	assert answer is None and problem == "Only YAML files are handled here."


@pytest.mark.asyncio
async def test_no_app_means_no_request():
	answer, problem = await app_channel.async_request(_hass(), "/files", {})
	assert answer is None and problem == app_channel.APP_MISSING


# ── The app's end ───────────────────────────────────────────────────────


@pytest.fixture
def app(monkeypatch):
	monkeypatch.setattr(integration_channel, "INTEGRATION_DIR", ROOT / "custom_components" / "vomesync")
	monkeypatch.setitem(integration_channel._secret, "value", "")
	return integration_channel


def test_the_app_keeps_the_secret_its_check_in_answers(app, monkeypatch):
	monkeypatch.setenv("VOME_APP_HOST", "A0D7B954-vome")
	calls = []

	def call_service(name, data):
		calls.append((name, data))
		return 200, {"service_response": {"secret": "s" * 43}}

	assert app.check_in(call_service, lambda p: p["service_response"])
	assert calls == [("app_check_in", {"url": f"http://a0d7b954-vome:{app.CHANNEL_PORT}"})]
	assert app.authorised("Bearer " + "s" * 43)
	assert not app.authorised("Bearer " + "t" * 43)
	assert not app.authorised(None)


def test_nothing_is_authorised_before_a_check_in(app):
	assert not app.authorised("Bearer ")
	assert not app.check_in(lambda *_: (404, {}), lambda p: p)
	assert not app.authorised("Bearer ")


def test_the_app_changes_only_yaml(app, tmp_path):
	(tmp_path / "configuration.yaml").write_text("homeassistant:\n", encoding="utf-8")
	(tmp_path / "card.js").write_text("x", encoding="utf-8")
	status, answer = app.handle_files({"op": "delete", "path": "card.js"}, str(tmp_path))
	assert status == 400 and "Only YAML" in answer["error"]
	assert (tmp_path / "card.js").exists()
	status, answer = app.handle_files({"op": "delete", "path": "configuration.yaml"}, str(tmp_path))
	assert status == 400 and "Refusing to delete configuration.yaml" in answer["error"]
	status, answer = app.handle_files(
		{"op": "write", "path": "packages/heat.yaml", "body": {"content": "a: 1\n"}}, str(tmp_path))
	assert status == 200 and answer["result"]["written"] is True
	status, answer = app.handle_files({"op": "write", "path": "../escape.yaml",
	                                   "body": {"content": "x"}}, str(tmp_path))
	assert status == 400 and "outside" in answer["error"]
	status, answer = app.handle_files({"op": "write", "path": ".storage/core.yaml",
	                                   "body": {"content": "x"}}, str(tmp_path))
	assert status == 400 and "internal storage" in answer["error"]


def test_the_app_saves_automations_through_home_assistants_editor(app):
	sent = []

	def ha_request(method, path, body=None):
		sent.append((method, path, body))
		return 200, {"result": "ok"}

	assert app.handle_automation({"op": "save", "id": "chat_1", "config": {"alias": "x"}}, ha_request) \
		== (200, {"result": "saved"})
	assert app.handle_automation({"op": "delete", "id": "chat_1"}, ha_request) \
		== (200, {"result": "deleted"})
	assert sent == [
		("POST", "/config/automation/config/chat_1", {"alias": "x"}),
		("DELETE", "/config/automation/config/chat_1", None),
	]
	for bad in ({"op": "save", "id": "../x", "config": {}}, {"op": "save", "id": "a"},
	            {"op": "rename", "id": "a"}):
		assert app.handle_automation(bad, ha_request)[0] == 400
	assert len(sent) == 2
	status, answer = app.handle_automation(
		{"op": "save", "id": "a", "config": {}}, lambda *a, **k: (400, {"message": "Message malformed"}))
	assert status == 400 and "Message malformed" in answer["error"]


def test_the_listener_refuses_anyone_without_the_secret(app, tmp_path, monkeypatch):
	from http.server import ThreadingHTTPServer

	(tmp_path / "x.yaml").write_text("a: 1\n", encoding="utf-8")
	monkeypatch.setitem(app._secret, "value", "k" * 43)
	server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler(lambda: str(tmp_path), None))
	threading.Thread(target=server.serve_forever, daemon=True).start()
	url = f"http://127.0.0.1:{server.server_address[1]}/files"

	def post(headers):
		req = urllib.request.Request(
			url, data=json.dumps({"op": "delete", "path": "x.yaml"}).encode(),
			headers={"Content-Type": "application/json", **headers}, method="POST")
		try:
			with urllib.request.urlopen(req, timeout=5) as resp:
				return resp.status, json.loads(resp.read())
		except urllib.error.HTTPError as err:
			return err.code, json.loads(err.read())

	try:
		assert post({})[0] == 401
		assert post({"Authorization": "Bearer wrong"})[0] == 401
		assert (tmp_path / "x.yaml").exists()
		status, answer = post({"Authorization": "Bearer " + "k" * 43})
		assert status == 200 and answer["result"]["deleted"] is True
		assert not (tmp_path / "x.yaml").exists()
	finally:
		server.shutdown()
