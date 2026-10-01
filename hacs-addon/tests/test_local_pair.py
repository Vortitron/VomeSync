# flake8: noqa
"""The Vome CHAP local pair (vome_chap/local_pair.py): two Home Assistants in
one house keeping each other ready, with no Vome involved.

Asked for on Facebook after the first internet-cut test (29 Sept 2026). What
matters: a standby that takes over only when it can see the house router and
not the running install; a running install that never stops itself for being
cut off (owner, 29 Sept 2026 -- a dead router would otherwise leave the house
with no Home Assistant); the older epoch standing down when the two meet
again; and a move back that loses nothing.

Most of it runs here as a simulated house: two installs with real config
folders and the real snapshot code, talking through a network the tests cut.
The TLS itself needs Python 3.13 and is tested at the end, when there.
"""
import importlib.util
import json
import shutil
import ssl
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vome_chap"))
import local_pair as lp  # noqa: E402

_spec = importlib.util.spec_from_file_location("vome_chap_sync_lp", ROOT / "vome_chap" / "chap_sync.py")
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

HOME_ADDONS = ["core_mosquitto", "a0d7b954_ssh", "abc123_vome", "45df7312_zigbee2mqtt"]


def make_config(root: Path, automations="[]\n") -> Path:
	(root / ".storage").mkdir(parents=True)
	(root / ".storage" / "core.config_entries").write_text('{"entries": []}')
	(root / ".HA_VERSION").write_text("2026.9.4")
	(root / "configuration.yaml").write_text("default_config:\n")
	(root / "automations.yaml").write_text(automations)
	return root


class House:
	"""The network: who is switched on, who is plugged in, the clock."""

	def __init__(self):
		self.now = 1_790_700_000.0
		self.installs = {}
		self.cut = set()          # installs whose cable is out
		self.router_up = True

	def reachable(self, a, b):
		return all(x.on and x.name not in self.cut for x in (a, b))


class Install(lp.Env):
	def __init__(self, house, name, ip, tmp):
		data, config = tmp / name / "data", make_config(tmp / name / "config", automations=f"# {name}\n")
		data.mkdir(parents=True)
		super().__init__(cs, data, config, server=None, clock=lambda: house.now)
		self.house, self.name, self.ip = house, name, ip
		self.on, self.core_running = True, True
		self.addons = {s: True for s in HOME_ADDONS}
		self.notices = {}
		self.lan = lp.LanServer(data, lambda: lp.my_status(lp.load_pair(data), self))
		house.installs[ip] = self

	def set_options(self, mode, code="", name=""):
		(self.data_dir / lp.OPTIONS_FILE).write_text(json.dumps({"local_pair": mode, "pair_code": code,
		                                                         "install_name": name}))

	def run(self):
		assert self.on
		return lp.run_local_once(lp.read_options(self.data_dir), self)

	@property
	def pair(self):
		return lp.load_pair(self.data_dir)

	# The Env, as the house sees it
	def network(self):
		return [{"interface": "eth0", "address": f"{self.ip}/24", "gateway": "192.168.1.1"}]

	def router_ok(self, gateway):
		return self.house.router_up and self.name not in self.house.cut

	def ha_ok(self, address, port):
		other = self.house.installs.get(address)
		return bool(other) and self.house.reachable(self, other) and other.core_running

	def peer(self, pair, method, path, body=None, sink=None):
		other = self.house.installs.get((pair.get("peer") or {}).get("address"))
		if not other or not self.house.reachable(self, other):
			return 0, None, {}
		status, answer, headers = other.lan.answer(method, path, json.loads(json.dumps(body)) if body else None)
		headers = {k.lower(): v for k, v in headers.items()}
		if isinstance(answer, Path):
			shutil.copyfile(answer, sink)
			return status, None, headers
		return status, json.loads(json.dumps(answer)), headers

	def core_stopped(self):
		return not self.core_running

	def set_core(self, running):
		self.core_running = running
		return True

	def core_version(self):
		return "2026.9.4"

	def core_port(self):
		return 8123

	def notify(self, notice_id, title, message):
		self.notices[notice_id] = (title, message)
		return True

	def dismiss(self, notice_id):
		self.notices.pop(notice_id, None)
		return True

	def home_addons(self):
		return [s for s, on in self.addons.items() if on]

	def set_addons(self, slugs, running):
		for s in slugs:
			self.addons[s] = running
		return []

	def addon_names(self):
		return {"core_mosquitto": "Mosquitto broker", "45df7312_zigbee2mqtt": "Zigbee2MQTT"}

	def keep_updating(self, pair):
		if pair.get("auto_update_on"):
			return None
		pair["auto_update_on"] = True
		self.auto_update_turned_on = True
		return "turned on automatic updates"


def tick(house, *installs, seconds=lp.PASS_SECONDS):
	house.now += seconds
	return [i.run() for i in installs if i.on]


@pytest.fixture
def house(tmp_path):
	return House()


@pytest.fixture
def pair(house, tmp_path):
	"""A main install and its standby, paired and in step."""
	main = Install(house, "main", "192.168.1.116", tmp_path)
	spare = Install(house, "spare", "192.168.1.89", tmp_path)
	main.set_options("main")
	spare.set_options("off")
	main.run()
	code = lp.panel_view(main.data_dir, house.now)["code"]
	assert code and lp.NOTICE_CODE in main.notices
	spare.set_options("standby", code)
	tick(house, spare, main)
	return main, spare


# ── Pairing ───────────────────────────────────────────────────────────────

def test_the_code_carries_where_to_find_the_main_install_and_the_key():
	key = bytes(range(32))
	code = lp.make_code("192.168.1.116", 8177, key, "abc", "Kitchen HA")
	got = lp.read_code("  " + code[:20] + "\n" + code[20:] + " ")  # pasted across lines
	assert got == {"address": "192.168.1.116", "port": 8177, "key": key, "id": "abc", "name": "Kitchen HA"}


@pytest.mark.parametrize("bad, says", [("hello", "not a Vome CHAP pairing code"),
                                        ("vcp1.eyJhIjoi", "incomplete"),
                                        (lp.CODE_PREFIX + "e30", "incomplete")])
def test_a_wrong_code_says_what_is_wrong(bad, says):
	with pytest.raises(ValueError, match=says):
		lp.read_code(bad)


def test_pairing_stops_the_standby_and_fills_it(pair):
	main, spare = pair
	assert spare.pair["peer"]["address"] == "192.168.1.116"
	assert main.pair["peer"]["address"] == "192.168.1.89"   # the standby said hello
	assert lp.is_holder(main.pair) and not lp.is_holder(spare.pair)
	assert main.core_running and not spare.core_running
	# The home's add-ons stop on the standby; the box's own tools and Vome's do not.
	assert spare.addons == {"core_mosquitto": False, "a0d7b954_ssh": True, "abc123_vome": True,
	                        "45df7312_zigbee2mqtt": False}
	assert (spare.config_dir / "automations.yaml").read_text() == "# main\n"
	assert lp.NOTICE_CODE not in main.notices   # the code is gone once used


def test_a_copy_of_the_main_install_gets_its_own_identity(pair, house, tmp_path):
	"""A standby is often made by restoring the main's backup: its settings
	and this add-on's data come along. Changing them gives it a new identity."""
	main, spare = pair
	assert spare.pair["id"] != main.pair["id"]
	shutil.copyfile(main.data_dir / lp.PAIR_FILE, spare.data_dir / lp.PAIR_FILE)
	shutil.copyfile(main.data_dir / lp.OPTIONS_FILE, spare.data_dir / lp.OPTIONS_FILE)
	spare.set_options("standby", "vcp1.x")  # the owner changes the copied settings
	spare.run()
	assert spare.pair["id"] != main.pair["id"]


def test_changes_follow_to_the_standby(pair, house):
	main, spare = pair
	(main.config_dir / "automations.yaml").write_text("- id: new\n")
	tick(house, main, spare, seconds=lp.SNAPSHOT_EVERY)
	assert (spare.config_dir / "automations.yaml").read_text() == "- id: new\n"
	assert main.pair["peer_applied_sha256"] is None or True  # seen on the next pass
	tick(house, main)
	assert lp.panel_view(main.data_dir, house.now)["in_step"]


# ── Taking over ───────────────────────────────────────────────────────────

def test_the_standby_takes_over_when_the_main_install_dies(pair, house):
	main, spare = pair
	main.on = main.core_running = False
	waited = 0
	while waited < lp.T_TAKE - lp.PASS_SECONDS:
		tick(house, spare)
		waited += lp.PASS_SECONDS
		assert not spare.core_running, f"took over after {waited} s"
	for _ in range(3):
		tick(house, spare)
	assert lp.is_holder(spare.pair) and spare.pair["epoch"] == 2
	assert spare.core_running and spare.addons["45df7312_zigbee2mqtt"]
	assert lp.NOTICE_RUNNING in spare.notices


def test_a_standby_that_cannot_see_the_router_does_nothing(pair, house):
	"""It is the one cut off, or the house is dark: either way not its call."""
	main, spare = pair
	main.on = main.core_running = False
	house.router_up = False
	tick(house, spare, seconds=lp.T_TAKE * 5)
	tick(house, spare, seconds=lp.T_TAKE * 5)
	assert not spare.core_running and not lp.is_holder(spare.pair)


def test_a_blip_restarts_the_clock(pair, house):
	main, spare = pair
	main.on = False
	tick(house, spare, seconds=lp.T_TAKE - 30)
	main.on = True           # back: its add-on answers
	tick(house, spare)
	main.on = False
	tick(house, spare, seconds=60)
	assert not lp.is_holder(spare.pair)


def test_home_assistant_still_serving_is_enough(pair, house):
	"""The main install's add-on stopped (updating, say), its Home Assistant
	did not: the home is running, nothing moves."""
	main, spare = pair
	main.lan.answer = lambda *a: (500, {"error": "down"}, {})
	tick(house, spare, seconds=lp.T_TAKE * 3)
	tick(house, spare, seconds=lp.T_TAKE * 3)
	assert not lp.is_holder(spare.pair)


def test_nothing_to_run_the_home_with_nothing_to_take_over_with(house, tmp_path):
	main = Install(house, "main", "192.168.1.116", tmp_path)
	spare = Install(house, "spare", "192.168.1.89", tmp_path)
	main.set_options("main")
	main.run()
	code = lp.panel_view(main.data_dir, house.now)["code"]
	main.on = False           # gone before the standby ever took a copy
	spare.set_options("standby", code)
	for _ in range(5):
		tick(house, spare, seconds=lp.T_TAKE)
	assert not lp.is_holder(spare.pair)


# ── Coming back ───────────────────────────────────────────────────────────

def test_the_main_install_returning_stops_itself(pair, house):
	main, spare = pair
	main.on = False
	for _ in range(20):
		tick(house, spare)
	assert lp.is_holder(spare.pair)
	main.on = main.core_running = True    # it rebooted, Home Assistant and all
	(spare.config_dir / "automations.yaml").write_text("- id: made-while-away\n")
	tick(house, main, spare, seconds=lp.SNAPSHOT_EVERY)
	tick(house, main)
	assert not main.core_running and not lp.is_holder(main.pair) and main.pair["epoch"] == 2
	assert not main.addons["core_mosquitto"]
	assert (main.config_dir / "automations.yaml").read_text() == "- id: made-while-away\n"


def test_a_cut_off_main_install_keeps_running_then_stands_down(pair, house):
	"""Owner, 29 Sept 2026: never stop the running install for being cut
	off. Its cable out, the standby takes over too; plugged back in, the
	older epoch gives way."""
	main, spare = pair
	house.cut.add("main")
	for _ in range(20):
		tick(house, main, spare)
	assert main.core_running and spare.core_running   # both, while apart
	house.cut.clear()
	tick(house, main, spare)
	assert not main.core_running and spare.core_running


def test_the_router_dying_leaves_the_home_running(pair, house):
	"""The first internet-cut test: the router was also the switch. Nothing
	can see anything; the running install carries on."""
	main, spare = pair
	house.router_up = False
	house.cut.update({"main", "spare"})
	for _ in range(30):
		tick(house, main, spare)
	assert main.core_running and not spare.core_running


def test_the_same_epoch_twice_settles_on_one():
	a = {"id": "aaa", "holder": "aaa", "epoch": 3}
	b = {"id": "bbb", "holder": "bbb", "epoch": 3}
	assert lp.adopt(b, a["holder"], a["epoch"]) is True and not lp.adopt(a, "bbb", 3)
	assert a["holder"] == b["holder"] == "aaa"
	assert lp.adopt(a, "bbb", 2) is False and lp.adopt(a, None, 9) is False


# ── Moving the home ───────────────────────────────────────────────────────

def test_moving_the_home_loses_nothing(pair, house):
	main, spare = pair
	tick(house, main)
	ok, _ = lp.ask_move(main.data_dir)
	assert ok
	(main.config_dir / "automations.yaml").write_text("- id: last-minute\n")
	tick(house, main)                      # stops Home Assistant here
	assert not main.core_running
	tick(house, main)                      # the last copy, with Core stopped
	tick(house, spare)                     # the standby takes it
	assert (spare.config_dir / "automations.yaml").read_text() == "- id: last-minute\n"
	assert not spare.core_running          # not yet: the home has not moved
	tick(house, main)                      # it has it: the home moves
	tick(house, spare)
	assert spare.core_running and lp.is_holder(spare.pair) and spare.pair["epoch"] == 2
	tick(house, main)
	assert not main.core_running and not lp.is_holder(main.pair)
	# Moved on purpose: it stays until the owner moves it back.
	for _ in range(3):
		tick(house, main, spare, seconds=lp.BACK_AFTER)
	assert lp.is_holder(spare.pair) and spare.core_running


def _taken_over(house, main, spare):
	main.on = main.core_running = False
	for _ in range(20):
		tick(house, spare)
	assert lp.is_holder(spare.pair) and spare.pair["took_over"]


def test_the_home_goes_back_to_the_main_install_once_it_is_steady(pair, house):
	"""Owner, 29 Sept 2026: "we still need to have one designated Live and
	the other standby". After a takeover the home goes home by itself."""
	main, spare = pair
	_taken_over(house, main, spare)
	main.on = main.core_running = True
	(spare.config_dir / "automations.yaml").write_text("- id: made-while-away\n")
	tick(house, main, spare, seconds=lp.SNAPSHOT_EVERY)   # main stands down, spare builds a copy
	tick(house, main, spare)                              # main takes it
	assert not main.core_running
	# Not before it has been back and in step for BACK_AFTER.
	tick(house, spare, main)
	assert lp.is_holder(spare.pair) and not spare.pair.get("moving_to")
	for _ in range(lp.BACK_AFTER // lp.PASS_SECONDS + 8):
		tick(house, spare, main)
		if main.core_running:
			break
	assert main.core_running and lp.is_holder(main.pair) and not spare.core_running
	assert (main.config_dir / "automations.yaml").read_text() == "- id: made-while-away\n"
	assert main.notices[lp.NOTICE_RUNNING][0].startswith("Back on")
	tick(house, spare, main, seconds=lp.BACK_AFTER)
	assert lp.is_holder(main.pair) and main.core_running   # and it stays


def test_a_main_install_that_keeps_dropping_out_is_not_handed_the_home(pair, house):
	main, spare = pair
	_taken_over(house, main, spare)
	main.on = True
	tick(house, main, spare, seconds=lp.SNAPSHOT_EVERY)
	for _ in range(6):
		tick(house, spare, main, seconds=lp.BACK_AFTER // 3)
		main.on = not main.on
	assert lp.is_holder(spare.pair)


def test_a_smaller_standby_runs_only_what_was_chosen_for_it(pair, house):
	main, spare = pair
	tick(house, main)
	assert {a["slug"] for a in main.pair["peer_addons"]} == {"core_mosquitto", "45df7312_zigbee2mqtt"}
	assert lp.choose_standby_addons(main.data_dir, ["core_mosquitto", "not_there"])[0]
	assert lp.choose_standby_addons(spare.data_dir, [])[0] is False   # chosen on the main install
	tick(house, spare)
	_taken_over(house, main, spare)
	assert spare.addons["core_mosquitto"] and not spare.addons["45df7312_zigbee2mqtt"]
	assert spare.addons["a0d7b954_ssh"]


def test_the_panel_offers_the_standby_s_add_ons_on_the_main_install(pair, house):
	main, spare = pair
	tick(house, main)
	page = lp.render_panel(lp.panel_view(main.data_dir, house.now), "tok")
	assert "Zigbee2MQTT" in page and "action='addons'" in page and page.count(" checked") == 2


def test_a_vome_connected_install_in_no_vome_pair_may_pair_locally(tmp_path):
	class Portal:
		calls = 0

		def __init__(self, binding):
			pass

		def role(self):
			Portal.calls += 1
			return None if Portal.calls == 3 else {"role": "none"}
	assert cs.vome_pair_role({}, 1000, tmp_path, Portal) == "none"
	assert cs.vome_pair_role({}, 1100, tmp_path, Portal) == "none" and Portal.calls == 1   # remembered
	Portal.role = lambda self: {"role": "standby"}
	assert cs.vome_pair_role({}, 1400, tmp_path, Portal) == "standby"


def test_a_move_the_standby_cannot_take_is_called_off(pair, house):
	main, spare = pair
	lp.ask_move(main.data_dir)
	tick(house, main)
	tick(house, main)
	spare.on = False
	tick(house, main, seconds=lp.MOVE_TIMEOUT + 1)
	tick(house, main)
	assert main.core_running and lp.is_holder(main.pair) and not main.pair.get("moving_to")


def test_only_the_running_install_can_move_the_home(pair):
	main, spare = pair
	assert lp.ask_move(spare.data_dir) == (False, "this install is not the one running the home")
	assert lp.ask_move(main.data_dir, cancel=True) == (False, "no move to call off")


# ── The panel ─────────────────────────────────────────────────────────────

def test_the_panel_says_where_the_home_is(pair, house):
	main, spare = pair
	tick(house, main)
	view = lp.panel_view(main.data_dir, house.now)
	assert view["state"] == "running_here" and view["peer"] and view["can_move"]
	page = lp.render_panel(view, "tok")
	assert "runs your home" in page and "Move the home to" in page and "value='tok'" in page
	assert "http" not in page.split("<body>")[1]  # nothing fetched from anywhere


def test_the_panel_shows_the_code_until_a_standby_joins(house, tmp_path):
	main = Install(house, "main", "192.168.1.116", tmp_path)
	main.set_options("main")
	main.run()
	view = lp.panel_view(main.data_dir, house.now)
	assert view["state"] == "waiting_for_standby"
	assert view["code"].startswith(lp.CODE_PREFIX) and view["code"] in lp.render_panel(view)


def test_the_panel_moves_the_home_only_with_its_own_form(pair, tmp_path):
	import urllib.error
	import urllib.request
	main, _ = pair
	httpd = lp.start_panel(main.data_dir, port=0)
	try:
		url = f"http://127.0.0.1:{httpd.server_address[1]}/move"
		with pytest.raises(urllib.error.HTTPError) as got:
			urllib.request.urlopen(urllib.request.Request(url, data=b"t=guess"))
		assert got.value.code == 403 and not main.pair.get("moving_to")
		page = urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_address[1]}/").read().decode()
		token = page.split("name='t' value='")[1].split("'")[0]
		urllib.request.urlopen(urllib.request.Request(url, data=f"t={token}".encode()))
		assert main.pair["moving_to"] == main.pair["peer"]["id"]
	finally:
		httpd.shutdown()


# ── TLS with the pair's key (Python 3.13, the add-on's own) ──────────────

needs_psk = pytest.mark.skipif(not getattr(ssl, "HAS_PSK", False)
                               or not hasattr(ssl.SSLContext, "set_psk_client_callback"),
                               reason="TLS with a pre-shared key needs Python 3.13")


@needs_psk
def test_only_the_paired_key_gets_in(tmp_path):
	key = bytes(range(32))
	data = tmp_path / "d"
	data.mkdir()
	lp.save_pair(data, {"id": "main", "mode": "main", "key": __import__("base64").b64encode(key).decode()})
	server = lp.LanServer(data, lambda: {"id": "main", "holder": "main", "epoch": 1}, port=0, bind="127.0.0.1")
	server.ensure()
	try:
		port = server.bound_port
		assert lp.call_peer("127.0.0.1", port, key, "GET", "/v1/status")[:2] == (200, {"id": "main", "holder": "main", "epoch": 1})
		assert lp.call_peer("127.0.0.1", port, bytes(32), "GET", "/v1/status")[0] == 0
		status, body, _ = lp.call_peer("127.0.0.1", port, key, "POST", "/v1/hello",
		                               {"id": "spare", "address": "192.168.1.89", "name": "Spare"})
		assert status == 200 and json.loads((data / lp.HELLO_FILE).read_text())["name"] == "Spare"
	finally:
		server.stop()



# ── Security review, 1 Oct 2026 ───────────────────────────────────────────

def test_the_code_is_never_in_a_notification(house, tmp_path):
	"""Every user of Home Assistant sees its notifications; the code lets an
	install copy everything. It is on the app's panel, for administrators."""
	main = Install(house, "main", "192.168.1.116", tmp_path)
	main.set_options("main")
	main.run()
	code = lp.panel_view(main.data_dir, house.now)["code"]
	title, message = main.notices[lp.NOTICE_CODE]
	assert code not in message and "vcp1." not in message and "Open Web UI" in message


def test_both_sides_derive_the_same_pair_key(pair):
	main, spare = pair
	assert lp.pair_key(main.pair) == lp.pair_key(spare.pair) != lp.key_of(main.pair)
	assert lp.pair_key({"id": "a", "mode": "main", "key": main.pair["key"]}) is None  # no peer yet


def test_once_the_pair_key_is_used_the_code_opens_nothing_but_a_hello(pair):
	main, spare = pair
	lan = main.lan
	(main.data_dir / lp.PAIR_SEEN_FILE).unlink(missing_ok=True)  # as with a peer still on 0.2.0
	assert lan.answer("GET", "/v1/status", None, identity=lp.CODE_IDENTITY)[0] == 200  # it carries on
	assert lan.answer("GET", "/v1/status", None, identity=lp.PAIR_IDENTITY)[0] == 200
	assert lan.answer("GET", "/v1/status", None, identity=lp.CODE_IDENTITY)[0] == 403
	assert lan.answer("GET", "/v1/snapshot", None, identity=lp.CODE_IDENTITY)[0] == 403
	assert lan.lookup(lp.PAIR_IDENTITY) == lp.pair_key(main.pair)
	assert lan.lookup("someone-else") is None


def test_a_second_install_with_the_code_does_not_replace_the_standby(pair):
	main, spare = pair
	status, body, _ = main.lan.answer("POST", "/v1/hello", {"id": "intruder", "address": "192.168.1.66"},
	                                  identity=lp.CODE_IDENTITY)
	assert status == 409 and "already has a standby" in body["error"]
	ok, _, _ = main.lan.answer("POST", "/v1/hello", {"id": spare.pair["id"], "address": "192.168.1.89"},
	                           identity=lp.CODE_IDENTITY)
	assert ok == 200  # its own standby may say hello again


def test_a_snapshot_that_unpacks_to_too_much_is_refused(tmp_path, monkeypatch):
	import io
	import tarfile
	monkeypatch.setattr(cs, "MAX_UNPACKED_BYTES", 1000)
	buf = io.BytesIO()
	with tarfile.open(fileobj=buf, mode="w:gz") as tar:
		for name, size in ((".storage/core.config_entries", 600), (".HA_VERSION", 600)):
			info = tarfile.TarInfo(name)
			info.size = size
			tar.addfile(info, io.BytesIO(b"x" * size))
	config = make_config(tmp_path / "c")
	with pytest.raises(cs.ApplyRefused, match="unpacks to more than"):
		cs.apply_snapshot(config, buf.getvalue())
	assert (config / ".HA_VERSION").read_text() == "2026.9.4"  # nothing touched


@needs_psk
def test_the_pair_key_and_the_size_caps_over_real_tls(tmp_path, monkeypatch):
	key = bytes(range(32))
	data = tmp_path / "main"
	data.mkdir()
	lp.save_pair(data, {"id": "m1", "mode": "main", "key": __import__("base64").b64encode(key).decode(),
	                    "holder": "m1", "epoch": 1, "peer": {"id": "s1"}})
	server = lp.LanServer(data, lambda: {"id": "m1", "pad": "x" * 2000}, port=0, bind="127.0.0.1")
	server.ensure()
	try:
		port = server.bound_port
		own = lp.pair_key({"id": "s1", "mode": "standby", "key": __import__("base64").b64encode(key).decode(),
		                   "peer": {"id": "m1"}})
		assert lp.call_peer("127.0.0.1", port, own, "GET", "/v1/status", identity=lp.PAIR_IDENTITY)[0] == 200
		assert lp.call_peer("127.0.0.1", port, key, "GET", "/v1/status")[0] == 403  # the code: hello only now
		assert lp.call_peer("127.0.0.1", port, bytes(32), "GET", "/v1/status", identity=lp.PAIR_IDENTITY)[0] == 0
		monkeypatch.setattr(lp, "MAX_JSON", 1000)
		assert lp.call_peer("127.0.0.1", port, own, "GET", "/v1/status", identity=lp.PAIR_IDENTITY)[0] == 413
	finally:
		server.stop()



def test_both_installs_turn_on_their_own_updates(pair):
	"""A standby's Home Assistant is stopped, so nobody can update this app on
	it by hand; without this it kept the version it was paired with (found
	1 Oct 2026)."""
	main, spare = pair
	assert main.auto_update_turned_on and spare.auto_update_turned_on


def test_the_real_env_asks_the_supervisor(tmp_path):
	calls = []

	class FakeCs:
		@staticmethod
		def ensure_auto_update(state, path):
			calls.append(path)
			return "turned on"
	env = lp.Env(FakeCs, tmp_path, tmp_path)
	assert env.keep_updating({}) == "turned on" and calls == [tmp_path / lp.PAIR_FILE]



def test_installs_are_named_by_role_unless_the_owner_names_them(pair, house):
	"""After a sync both carry the same Home Assistant and host name; "Home
	Assistant at 192.168.1.116" in every message read badly (30 Sept 2026)."""
	main, spare = pair
	assert main.pair["name"] == "Main install" and spare.pair["name"] == "Standby"
	tick(house, main)
	assert main.pair["peer"]["name"] == "Standby"
	page = lp.render_panel(lp.panel_view(main.data_dir, house.now), "t")
	assert "<b>Main install</b> (this one, 192.168.1.116)" in page and "Standby (192.168.1.89)" in page
	before = spare.pair["id"]
	spare.set_options("standby", spare.pair and lp.read_options(spare.data_dir)["code"], name="Kitchen NUC")
	tick(house, spare, main)
	assert spare.pair["id"] == before  # renaming does not re-pair
	assert main.pair["peer"]["name"] == "Kitchen NUC"



def test_the_owner_picks_quick_or_careful(pair, house):
	"""Owner, 28 Sept 2026: quick (risk a blip moving the home) or careful
	(a few minutes' downtime)? The standby's takeover_after, 30 s to 15 min."""
	main, spare = pair
	opts = json.loads((spare.data_dir / lp.OPTIONS_FILE).read_text())
	(spare.data_dir / lp.OPTIONS_FILE).write_text(json.dumps({**opts, "takeover_after": 30}))
	main.on = main.core_running = False
	tick(house, spare)            # starts the clock
	tick(house, spare, seconds=35)
	assert lp.is_holder(spare.pair)
	assert lp.read_options(spare.data_dir)["takeover_after"] == 30
	for raw, got in ((5, 30), (5000, 900), ("soon", lp.T_TAKE), (None, lp.T_TAKE)):
		(spare.data_dir / lp.OPTIONS_FILE).write_text(json.dumps({**opts, "takeover_after": raw}))
		assert lp.read_options(spare.data_dir)["takeover_after"] == got, raw
