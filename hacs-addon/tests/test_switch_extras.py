# flake8: noqa
"""Switch extras: notes, params, properties, commerce, payments and rewards.

The contract is docs/SWITCH_EXTRAS.md in the VomeSync server repository.
What matters most here is that the signed requests are exactly what the
server checks (a note is signed only when there is one), that the webhook
secret never lands in the saved cache, that the WebSocket's note reaches the
entity and the event bus, and that a reward is claimed only when asked for,
only once, and never at the cost of the update itself.
"""
import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from homeassistant.exceptions import HomeAssistantError

from custom_components.vomesync import crypto, switch_extras
from custom_components.vomesync.api_client import VomeSyncAPIClient, VomeSyncAPIError
from custom_components.vomesync.const import DOMAIN, EVENT_STATE_CHANGED
from custom_components.vomesync.coordinator import VomeSyncCoordinator
from custom_components.vomesync.switch_extras import ExtrasError

SEED = crypto.generate_master_seed_b64url()


def _b64(data: str) -> bytes:
	return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _verify(pub_b64: str, sig_b64: str, canonical: dict) -> None:
	"""Raises unless ``sig`` is over the server's canonical JSON of ``canonical``."""
	message = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
	Ed25519PublicKey.from_public_bytes(_b64(pub_b64)).verify(_b64(sig_b64), message.encode())


# ── Signed requests ──────────────────────────────────────────────────────────


def test_set_state_signs_the_note_only_when_there_is_one():
	pub = crypto.switch_pubkey_b64url(SEED, 3)
	plain = crypto.build_v2_set_state_request(SEED, uid="vs_a", index=3, state=True, ts=1, nonce="n" * 12)
	assert "note" not in plain
	# Exactly the canonical JSON older servers check: no note key at all.
	_verify(pub, plain["sigSwitch"], {
		"v": 2, "action": "set_state", "uid": "vs_a", "ts": 1, "nonce": "n" * 12,
		"state": True, "params": {},
	})

	noted = crypto.build_v2_set_state_request(
		SEED, uid="vs_a", index=3, state=False, params={"song": "SOS"},
		ts=2, nonce="m" * 12, note="Now playing: Abba",
	)
	assert noted["note"] == "Now playing: Abba"
	_verify(pub, noted["sigSwitch"], {
		"v": 2, "action": "set_state", "uid": "vs_a", "ts": 2, "nonce": "m" * 12,
		"state": False, "params": {"song": "SOS"}, "note": "Now playing: Abba",
	})

	empty = crypto.build_v2_set_state_request(SEED, uid="vs_a", index=3, state=True, ts=1, nonce="n" * 12, note="")
	assert "note" not in empty


def test_set_state_signs_whole_floats_as_javascript_writes_them():
	# A light's hs_color is (30.0, 100.0); the server parses 30.0 to 30 and
	# its canonical JSON says 30, so the request must say 30 too.
	req = crypto.build_v2_set_state_request(
		SEED, uid="vs_a", index=0, state=True, params={"hs_color": (30.0, 99.5), "brightness": 200.0},
		ts=1, nonce="n" * 12,
	)
	assert req["params"] == {"hs_color": [30, 99.5], "brightness": 200}
	assert json.dumps(req["params"]) == '{"hs_color": [30, 99.5], "brightness": 200}'


def test_reward_claim_is_owner_signed_over_the_contract_shape():
	pub = crypto.owner_pubkey_b64url(SEED)
	req = crypto.build_v2_reward_claim_request(SEED, uid="vs_pub", change_at=1730000000000, entities=2, ts=5, nonce="c" * 12)
	assert set(req) == {"ownerPubKey", "ts", "nonce", "sigOwner", "changeAt", "entities"}
	assert req["ownerPubKey"] == pub
	_verify(pub, req["sigOwner"], {
		"v": 2, "action": "reward_claim", "uid": "vs_pub", "ownerPubKey": pub,
		"ts": 5, "nonce": "c" * 12, "changeAt": 1730000000000, "entities": 2,
	})


def test_reward_claims_list_carries_the_uid_and_signs_it():
	pub = crypto.owner_pubkey_b64url(SEED)
	req = crypto.build_v2_reward_claims_list_request(SEED, uid="vs_mine", ts=5, nonce="l" * 12)
	assert req["uid"] == "vs_mine"
	_verify(pub, req["sigOwner"], {
		"v": 2, "action": "reward_claims_list", "uid": "vs_mine", "ownerPubKey": pub,
		"ts": 5, "nonce": "l" * 12,
	})


@pytest.mark.parametrize("builder,action", [
	(crypto.build_v2_connect_onboard_request, "connect_onboard"),
	(crypto.build_v2_connect_status_request, "connect_status"),
	(crypto.build_v2_my_rewards_request, "my_rewards"),
])
def test_owner_actions_sign_the_plain_owner_shape(builder, action):
	pub = crypto.owner_pubkey_b64url(SEED)
	req = builder(SEED, ts=7, nonce="o" * 12)
	assert set(req) == {"ownerPubKey", "ts", "nonce", "sigOwner"}
	_verify(pub, req["sigOwner"], {"v": 2, "action": action, "ownerPubKey": pub, "ts": 7, "nonce": "o" * 12})


def test_update_switch_signs_exactly_the_extras_sent():
	pub = crypto.owner_pubkey_b64url(SEED)
	updates = {"commerce": {"toggleMode": "public"}, "rotateWebhookSecret": True}
	req = crypto.build_v2_update_switch_request(SEED, uid="vs_a", updates=updates, ts=9, nonce="u" * 12)
	assert req["commerce"] == {"toggleMode": "public"} and req["rotateWebhookSecret"] is True
	_verify(pub, req["sigOwner"], {
		"v": 2, "action": "update_switch", "uid": "vs_a", "ownerPubKey": pub,
		"ts": 9, "nonce": "u" * 12, "payload": updates,
	})


def _client_with(response):
	client = VomeSyncAPIClient("https://sync.test", auth_mode="crypto", crypto_seed=SEED)
	client._make_request = AsyncMock(return_value=response)
	return client


@pytest.mark.asyncio
async def test_api_client_routes():
	client = _client_with({"url": "https://connect.stripe.com/x"})
	assert (await client.start_connect_onboarding())["url"].startswith("https://")
	assert client._make_request.call_args[0][:2] == ("POST", "/api/v2/owner/connect")

	await client.get_connect_status()
	assert client._make_request.call_args[0][:2] == ("POST", "/api/v2/owner/connect/status")

	await client.claim_reward("vs_pub", 123, 2)
	method, path, body = client._make_request.call_args[0][:3]
	assert (method, path) == ("POST", "/api/v2/switch/vs_pub/reward-claims")
	assert body["changeAt"] == 123 and body["entities"] == 2

	await client.list_reward_claims("vs_mine")
	assert client._make_request.call_args[0][:2] == ("POST", "/api/v2/owner/reward-claims")

	await client.get_my_rewards()
	assert client._make_request.call_args[0][:2] == ("POST", "/api/v2/owner/my-rewards")

	await client.set_switch_state_v2("vs_a", 0, True, params={}, note="hello")
	assert client._make_request.call_args[0][2]["note"] == "hello"


@pytest.mark.asyncio
async def test_access_key_toggle_body_is_empty_unless_extras_are_given():
	client = VomeSyncAPIClient("https://sync.test")
	client._make_request = AsyncMock(return_value={})
	await client.toggle_switch_with_access_key("vs_a", "key")
	assert client._make_request.call_args[0][2] == {}
	await client.toggle_switch_with_access_key("vs_a", "key", state=False, note="hi", params={"a": 1})
	assert client._make_request.call_args[0][2] == {"state": False, "note": "hi", "params": {"a": 1}}


@pytest.mark.asyncio
async def test_owner_calls_need_a_signing_key():
	client = VomeSyncAPIClient("https://sync.test", "personal")
	for call in (client.start_connect_onboarding(), client.get_connect_status(),
			client.get_my_rewards(), client.list_reward_claims("vs_a"), client.claim_reward("vs_a", 1, 1)):
		with pytest.raises(VomeSyncAPIError):
			await call


# ── Contract checks done before signing ──────────────────────────────────────


def test_note_is_cleaned_and_limited():
	assert switch_extras.clean_note("  Now​ playing\n\tAbba  ") == "Now playing Abba"
	assert switch_extras.clean_note(None) == ""
	assert len(switch_extras.clean_note("x" * 280)) == 280
	with pytest.raises(ExtrasError, match="280"):
		switch_extras.clean_note("x" * 281)


def test_params_the_server_would_clip_are_sent_with_a_warning_not_refused():
	params = {
		"song": "SOS",
		"lyrics": "x" * 300,
		"nested": {"a": 1},
		"bad-key": 1,
		"rgb_color": [255, 0, 0],
	}
	cleaned = switch_extras.clean_params(params)
	assert cleaned == params  # signed as sent; the server clips its stored copy
	reasons = switch_extras.params_clipping(cleaned)
	assert any("lyrics" in r for r in reasons)
	assert any("nested" in r for r in reasons)
	assert any("bad-key" in r for r in reasons)
	assert not any("rgb_color" in r or "song" in r for r in reasons)
	assert switch_extras.params_clipping({"brightness": 200, "rgb_color": [1, 2, 3]}) == []
	many = {f"k{i}": i for i in range(20)}
	assert any("first 16" in r for r in switch_extras.params_clipping(many))


def test_params_that_are_not_json_are_refused():
	with pytest.raises(ExtrasError):
		switch_extras.clean_params(["not", "an", "object"])
	with pytest.raises(ExtrasError, match="finite"):
		switch_extras.clean_params({"x": float("nan")})


def test_properties_merge_replace_and_remove():
	current = {"wattage": 40, "colour": "warm white"}
	assert switch_extras.clean_properties({"radio": "P3", "colour": None}, current=current) == {
		"wattage": 40, "radio": "P3",
	}
	assert switch_extras.clean_properties({"radio": " P3  "}, current=current, replace=True) == {"radio": "P3"}
	assert switch_extras.clean_properties({"wattage": 40.0}) == {"wattage": 40}
	with pytest.raises(ExtrasError, match="lower case"):
		switch_extras.clean_properties({"Wattage": 1})
	with pytest.raises(ExtrasError, match="12"):
		switch_extras.clean_properties({f"p{i}": i for i in range(13)})
	with pytest.raises(ExtrasError, match="200"):
		switch_extras.clean_properties({"a": "x" * 201})
	with pytest.raises(ExtrasError):
		switch_extras.clean_properties({"a": {"nested": 1}})


def test_commerce_keeps_unmentioned_settings_and_drops_what_is_not_a_setting():
	current = {
		"toggleMode": "owner", "action": "toggle", "holdSeconds": 0, "cooldownSeconds": 10,
		"price": None, "tips": False, "allowMessage": False, "note": "",
		"payLink": "", "webhookUrl": "https://hooks.example/abc",
		"canTakePayments": True,  # public summary field, not a setting
	}
	merged = switch_extras.clean_commerce({"toggleMode": "public", "holdSeconds": 60}, current=current)
	assert merged["toggleMode"] == "public" and merged["holdSeconds"] == 60
	assert merged["webhookUrl"] == "https://hooks.example/abc"
	assert "canTakePayments" not in merged
	with pytest.raises(ExtrasError, match="price"):
		switch_extras.clean_commerce({"toggleMode": "paid"}, current=current)
	paid = switch_extras.clean_commerce({"toggleMode": "paid", "price": {"amount": 1000, "currency": "SEK"}})
	assert paid["price"] == {"amount": 1000, "currency": "sek"}
	with pytest.raises(ExtrasError, match="between 1000"):
		switch_extras.clean_commerce({"price": {"amount": 500, "currency": "sek"}})
	with pytest.raises(ExtrasError, match="https"):
		switch_extras.clean_commerce({"webhookUrl": "http://hooks.example"})
	with pytest.raises(ExtrasError):
		switch_extras.clean_commerce({"holdSeconds": 3601})
	with pytest.raises(ExtrasError, match="Unknown"):
		switch_extras.clean_commerce({"webhookSecret": "x"})


def test_reward_defaults_and_limits():
	assert switch_extras.clean_reward({"amount": 200, "currency": "sek"}) == {
		"amount": 200, "currency": "sek", "on": "on", "windowSeconds": 900, "maxPerDay": 5, "note": "",
	}
	assert switch_extras.clean_reward(None) is None
	with pytest.raises(ExtrasError):
		switch_extras.clean_reward({"amount": 0, "currency": "eur"})
	with pytest.raises(ExtrasError):
		switch_extras.clean_reward({"amount": 1, "currency": "eur", "windowSeconds": 30})
	with pytest.raises(ExtrasError):
		switch_extras.clean_reward({"amount": 1, "currency": "eur", "on": "sometimes"})


def test_reward_qualifies_by_direction():
	assert switch_extras.reward_qualifies({"on": "on"}, True)
	assert not switch_extras.reward_qualifies({"on": "on"}, False)
	assert switch_extras.reward_qualifies({"on": "off"}, False)
	assert switch_extras.reward_qualifies({"on": "any"}, False)
	assert not switch_extras.reward_qualifies(None, True)


def test_attributes_show_the_commerce_summary_never_the_webhook():
	attrs = switch_extras.extras_attributes({
		"properties": {"wattage": 40},
		"note": "Now playing: Abba",
		"lastParams": {"song": "SOS"},
		"lastSource": "public",
		"commerce": {"toggleMode": "public", "webhookUrl": "https://hooks.example/secret-token", "canTakePayments": False},
		"reward": {"amount": 200, "currency": "sek"},
	})
	assert attrs["properties"] == {"wattage": 40}
	assert attrs["last_note"] == "Now playing: Abba"
	assert attrs["last_params"] == {"song": "SOS"}
	assert attrs["last_source"] == "public"
	assert attrs["commerce"] == {"toggleMode": "public", "canTakePayments": False}
	assert attrs["reward"] == {"amount": 200, "currency": "sek"}
	assert switch_extras.extras_attributes({}) == {
		"properties": {}, "last_note": "", "last_params": {}, "last_source": None,
		"commerce": None, "reward": None,
	}


def test_switch_entity_carries_the_extras(hass, config_entry):
	from custom_components.vomesync.switch import VomeSyncSwitch
	coordinator = MagicMock()
	coordinator.config_entry = config_entry
	coordinator.get_switch_data = MagicMock(return_value={
		"name": "Lamp", "state": True, "note": "hello", "lastSource": "owner",
		"commerce": {"toggleMode": "owner", "webhookUrl": "https://hooks.example/x"},
	})
	entity = VomeSyncSwitch(coordinator, "vs_a", "Lamp", True, config_entry)
	attrs = entity.extra_state_attributes
	assert attrs["last_note"] == "hello"
	assert attrs["last_source"] == "owner"
	assert "webhookUrl" not in json.dumps(attrs)


def test_status_sensor_carries_the_extras(hass, config_entry):
	from custom_components.vomesync.sensor import VomeSyncSensor
	coordinator = MagicMock()
	coordinator.get_switch_data = MagicMock(return_value={
		"name": "Grid peak", "state": True, "reward": {"amount": 200, "currency": "sek", "on": "on"},
		"properties": {"operator": "Hamnen AB"},
	})
	sensor = VomeSyncSensor(coordinator, "vs_pub", "Grid peak")
	attrs = sensor.extra_state_attributes
	assert attrs["reward"]["amount"] == 200
	assert attrs["properties"] == {"operator": "Hamnen AB"}


# ── Coordinator: WebSocket changes, the event and reward claims ──────────────


def _coordinator(hass, entry, api=None):
	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=api or AsyncMock()):
		coordinator = VomeSyncCoordinator(hass, entry)
	coordinator.switches = {}
	coordinator.subscriptions = {}
	hass.services = MagicMock()
	hass.services.async_call = AsyncMock()
	return coordinator


def _scheduled(hass):
	"""The coroutines handed to hass.async_create_task, in order."""
	return [c.args[0] for c in hass.async_create_task.call_args_list]


@pytest.mark.asyncio
async def test_a_pushed_change_carries_its_note_and_fires_the_event(hass, config_entry):
	coordinator = _coordinator(hass, config_entry)
	coordinator.subscriptions = {"vs_pub": {"state": False, "is_owner": False, "note": "old"}}

	await coordinator._handle_websocket_message("vs_pub", {
		"type": "state_update", "uid": "vs_pub", "state": True, "timestamp": 1000,
		"note": "Now playing: Abba", "params": {"song": "SOS"}, "source": "paid",
		"paid": {"amount": 100, "currency": "eur"},
	})

	data = coordinator.subscriptions["vs_pub"]
	assert data["note"] == "Now playing: Abba"
	assert data["lastParams"] == {"song": "SOS"}
	assert data["lastSource"] == "paid"
	assert data["lastPaid"] == {"amount": 100, "currency": "eur"}
	assert data["lastChangeAt"] == 1000

	fired = [c for c in hass.bus.async_fire.call_args_list if c.args[0] == EVENT_STATE_CHANGED]
	assert len(fired) == 1
	payload = fired[0].args[1]
	assert payload["uid"] == "vs_pub" and payload["state"] == "on"
	assert payload["note"] == "Now playing: Abba" and payload["source"] == "paid"
	assert payload["params"] == {"song": "SOS"}
	assert payload["paid"] == {"amount": 100, "currency": "eur"}
	assert "entity_id" in payload

	# The next change without a note clears it: stale text must not linger.
	await coordinator._handle_websocket_message("vs_pub", {
		"type": "state_update", "uid": "vs_pub", "state": False, "timestamp": 2000, "source": "auto",
	})
	assert data["note"] == "" and data["lastParams"] == {} and data["lastSource"] == "auto"


@pytest.mark.asyncio
async def test_a_reconnect_replay_fires_no_event_and_keeps_the_note(hass, config_entry):
	coordinator = _coordinator(hass, config_entry)
	coordinator.switches = {"vs_a": {"state": True, "is_owner": True, "lastToggled": 1000, "note": "hello"}}

	# The server resends the current state on connect, without the note.
	await coordinator._handle_websocket_message("vs_a", {
		"type": "state_update", "uid": "vs_a", "state": True, "timestamp": 1000,
	})
	assert coordinator.switches["vs_a"]["note"] == "hello"

	hass.bus.async_fire.reset_mock()
	await coordinator._handle_websocket_message("vs_a", {
		"type": "state_update", "uid": "vs_a", "state": True, "timestamp": 1000,
	})
	assert not [c for c in hass.bus.async_fire.call_args_list if c.args[0] == EVENT_STATE_CHANGED]


@pytest.mark.asyncio
async def test_trigger_linked_entities_counts_what_switched(hass, config_entry):
	config_entry.options = {"linked_entities": {"vs_pub": {"entities": ["light.a", "switch.b", "fan.c"]}}}
	coordinator = _coordinator(hass, config_entry)

	async def _call(domain, service, service_data=None, blocking=False):
		if domain == "fan":
			raise RuntimeError("no such fan")
	hass.services.async_call = AsyncMock(side_effect=_call)

	assert await coordinator._trigger_linked_entities("vs_pub", True, {}) == 2
	config_entry.options = {"linked_entities": {}}
	assert await coordinator._trigger_linked_entities("vs_other", True, {}) == 0


def _reward_setup(hass, crypto_config_entry, claim=True, reward_on="on", api=None):
	crypto_config_entry.options = {
		"linked_entities": {"vs_pub": {"entities": ["switch.heat_pump"]}},
		**({"claim_rewards": True} if claim else {}),
	}
	api = api or AsyncMock()
	coordinator = _coordinator(hass, crypto_config_entry, api)
	coordinator.subscriptions = {"vs_pub": {
		"state": False, "is_owner": False,
		"reward": {"amount": 200, "currency": "sek", "on": reward_on, "windowSeconds": 900, "maxPerDay": 5},
	}}
	return coordinator, api


def _change(state=True, at=None):
	return {
		"type": "state_update", "uid": "vs_pub", "state": state,
		"timestamp": at if at is not None else int(time.time() * 1000),
	}


@pytest.mark.asyncio
async def test_reward_is_claimed_after_linked_entities_switched(hass, crypto_config_entry):
	coordinator, api = _reward_setup(hass, crypto_config_entry)
	message = _change()
	await coordinator._handle_websocket_message("vs_pub", message)

	coros = _scheduled(hass)
	assert len(coros) == 1
	await coros[0]
	api.claim_reward.assert_awaited_once_with("vs_pub", message["timestamp"], 1)

	# A replay of the same change (a reconnect) never claims it again.
	await coordinator._handle_websocket_message("vs_pub", dict(message))
	assert len(_scheduled(hass)) == 1


@pytest.mark.asyncio
async def test_reward_claim_prefers_last_change_at(hass, crypto_config_entry):
	coordinator, api = _reward_setup(hass, crypto_config_entry)
	now = int(time.time() * 1000)
	await coordinator._handle_websocket_message("vs_pub", {**_change(at=now), "lastChangeAt": now - 5})
	await _scheduled(hass)[0]
	assert api.claim_reward.await_args.args[1] == now - 5


@pytest.mark.asyncio
@pytest.mark.parametrize("claim,reward_on,state,switched_entities", [
	(False, "on", True, True),    # the option is off (the default)
	(True, "on", False, True),    # the offer is for turning on, this turned off
	(True, "on", True, False),    # nothing linked actually switched
])
async def test_reward_is_not_claimed(hass, crypto_config_entry, claim, reward_on, state, switched_entities):
	coordinator, api = _reward_setup(hass, crypto_config_entry, claim=claim, reward_on=reward_on)
	if not switched_entities:
		hass.services.async_call = AsyncMock(side_effect=RuntimeError("gone"))
	await coordinator._handle_websocket_message("vs_pub", _change(state=state))
	assert _scheduled(hass) == []
	api.claim_reward.assert_not_called()


@pytest.mark.asyncio
async def test_reward_is_not_claimed_outside_its_window_or_for_owned_switches(hass, crypto_config_entry):
	coordinator, api = _reward_setup(hass, crypto_config_entry)
	await coordinator._handle_websocket_message("vs_pub", _change(at=int(time.time() * 1000) - 901_000))
	assert _scheduled(hass) == []
	assert coordinator._maybe_claim_reward("vs_owned", True, int(time.time() * 1000), 1) is False


@pytest.mark.asyncio
async def test_reward_is_not_claimed_without_a_signing_key(hass, config_entry):
	coordinator, api = _reward_setup(hass, config_entry)
	await coordinator._handle_websocket_message("vs_pub", _change())
	assert _scheduled(hass) == []


@pytest.mark.asyncio
async def test_a_refused_claim_never_breaks_the_update(hass, crypto_config_entry):
	api = AsyncMock()
	api.claim_reward = AsyncMock(side_effect=VomeSyncAPIError("Reward limit reached for today"))
	coordinator, _ = _reward_setup(hass, crypto_config_entry, api=api)
	coordinator.async_update_listeners = MagicMock()
	await coordinator._handle_websocket_message("vs_pub", _change())
	await _scheduled(hass)[0]  # logs, does not raise
	assert coordinator.subscriptions["vs_pub"]["state"] is True
	coordinator.async_update_listeners.assert_called()

	# Even a claim check that blows up leaves the update intact.
	coordinator._reward_claimed = None  # makes the check itself raise
	await coordinator._handle_websocket_message("vs_pub", _change(state=False))
	await coordinator._handle_websocket_message("vs_pub", _change(state=True, at=int(time.time() * 1000) + 1))
	assert coordinator.subscriptions["vs_pub"]["state"] is True


@pytest.mark.asyncio
async def test_set_switch_state_sends_the_note_and_keeps_it(hass, crypto_config_entry):
	api = AsyncMock()
	api.set_switch_state_v2 = AsyncMock(return_value={"state": True, "timestamp": 5})
	coordinator = _coordinator(hass, crypto_config_entry, api)
	coordinator.switches = {"vs_a": {"state": False, "is_owner": True, "index": 0}}

	assert await coordinator.set_switch_state("vs_a", True, params={"song": "SOS"}, note="Abba")
	api.set_switch_state_v2.assert_awaited_once_with("vs_a", 0, True, params={"song": "SOS"}, note="Abba")
	assert coordinator.switches["vs_a"]["note"] == "Abba"
	assert coordinator.switches["vs_a"]["lastSource"] == "owner"


@pytest.mark.asyncio
async def test_access_key_change_with_a_note_sets_the_state_even_when_unchanged(hass, config_entry):
	api = AsyncMock()
	api.toggle_switch_with_access_key = AsyncMock(return_value={"state": True, "timestamp": 5})
	coordinator = _coordinator(hass, config_entry, api)
	coordinator.subscriptions = {"vs_pub": {"state": True, "is_owner": False}}

	assert await coordinator.toggle_switch_with_access_key("vs_pub", "key", desired_state=True, note="again")
	api.toggle_switch_with_access_key.assert_awaited_once_with(
		"vs_pub", "key", state=True, note="again", params=None,
	)
	assert coordinator.subscriptions["vs_pub"]["lastSource"] == "key"


@pytest.mark.asyncio
async def test_webhook_secret_is_returned_once_and_never_cached(hass, crypto_config_entry):
	api = AsyncMock()
	api.update_switch_v2_metadata = AsyncMock(return_value={
		"uid": "vs_a", "commerce": {"toggleMode": "public", "webhookUrl": "https://hooks.example/x"},
		"webhookSecret": "whsec_once",
	})
	crypto_config_entry.options = {"imported_switches": {"vs_a": {"name": "Lamp", "is_owner": True}}}
	coordinator = _coordinator(hass, crypto_config_entry, api)
	coordinator._async_update_entry_options = AsyncMock()
	coordinator.switches = {"vs_a": {"name": "Lamp", "is_owner": True, "commerce": {
		"toggleMode": "owner", "webhookUrl": "", "tips": True,
	}}}

	result = await coordinator.set_commerce("vs_a", {"toggleMode": "public", "webhookUrl": "https://hooks.example/x"})
	assert result["webhookSecret"] == "whsec_once"

	sent = api.update_switch_v2_metadata.await_args.args[1]
	assert sent["commerce"]["tips"] is True  # kept from the current settings
	assert sent["commerce"]["toggleMode"] == "public"

	assert "whsec_once" not in json.dumps(coordinator.switches)
	saved = coordinator._async_update_entry_options.await_args.args[0]
	assert "whsec_once" not in json.dumps(saved)


@pytest.mark.asyncio
async def test_properties_merge_onto_what_the_switch_has(hass, crypto_config_entry):
	api = AsyncMock()
	api.update_switch_v2_metadata = AsyncMock(side_effect=lambda uid, updates, **kw: dict(updates))
	coordinator = _coordinator(hass, crypto_config_entry, api)
	coordinator.switches = {"vs_a": {"is_owner": True, "properties": {"wattage": 40}}}
	assert await coordinator.set_properties("vs_a", {"colour": "warm white"}) == {
		"wattage": 40, "colour": "warm white",
	}
	assert await coordinator.set_properties("vs_a", {"radio": "P3"}, replace=True) == {"radio": "P3"}


@pytest.mark.asyncio
async def test_only_the_owner_may_change_extras(hass, crypto_config_entry, config_entry):
	coordinator = _coordinator(hass, crypto_config_entry)
	coordinator.subscriptions = {"vs_pub": {"is_owner": False}}
	with pytest.raises(VomeSyncAPIError, match="owner"):
		await coordinator.set_reward("vs_pub", {"amount": 1, "currency": "eur"})
	plain = _coordinator(hass, config_entry)
	plain.switches = {"vs_a": {"is_owner": True}}
	with pytest.raises(VomeSyncAPIError, match="signing key"):
		await plain.set_properties("vs_a", {"a": 1})


# ── Actions ─────────────────────────────────────────────────────────────────


def _handlers(hass, coordinator):
	from custom_components.vomesync import _register_services
	hass.data = {DOMAIN: {"entry": coordinator}}
	hass.services = MagicMock()
	handlers, kwargs = {}, {}

	def _capture(domain, name, handler, **kw):
		handlers[name] = handler
		kwargs[name] = kw
	hass.services.async_register = MagicMock(side_effect=_capture)
	_register_services(hass)
	return handlers, kwargs


def _mock_coordinator(owner=True, data=None):
	coordinator = MagicMock(spec=VomeSyncCoordinator)
	coordinator.get_switch_data = MagicMock(return_value=data if data is not None else {"is_owner": owner})
	coordinator.is_switch_owner = MagicMock(return_value=owner)
	coordinator.crypto_enabled = True
	coordinator.set_switch_state = AsyncMock(return_value=True)
	coordinator.toggle_switch_with_access_key = AsyncMock(return_value=True)
	coordinator.get_subscription_access_key = MagicMock(return_value=None)
	return coordinator


def _point_at(hass, uid="vs_a"):
	state = MagicMock()
	state.attributes = {"switch_uid": uid}
	hass.states.get = MagicMock(return_value=state)


def _call(**data):
	return MagicMock(data=data)


@pytest.mark.asyncio
async def test_set_state_action(hass, caplog):
	coordinator = _mock_coordinator()
	handlers, kwargs = _handlers(hass, coordinator)
	_point_at(hass)

	# The schema turns "on"/"off" into a bool and one entity into a list.
	schema = kwargs["set_state"]["schema"]
	data = schema({"entity_id": "switch.lamp", "state": "on", "note": "Abba", "params": {"song": "SOS"}})
	assert data["state"] is True and data["entity_id"] == ["switch.lamp"]

	await handlers["set_state"](_call(**data))
	coordinator.set_switch_state.assert_awaited_once_with("vs_a", True, params={"song": "SOS"}, note="Abba")

	with pytest.raises(HomeAssistantError, match="280"):
		await handlers["set_state"](_call(entity_id=["switch.lamp"], state=True, note="x" * 281))

	# Params the server will clip still go; the log says what will be cut.
	coordinator.set_switch_state.reset_mock()
	await handlers["set_state"](_call(entity_id=["switch.lamp"], state=True, params={"lyrics": "x" * 300}))
	coordinator.set_switch_state.assert_awaited_once()
	assert "clip" in caplog.text

	coordinator.set_switch_state = AsyncMock(return_value=False)
	with pytest.raises(HomeAssistantError, match="did not change"):
		await handlers["set_state"](_call(entity_id=["switch.lamp"], state=False))


@pytest.mark.asyncio
async def test_set_state_on_a_watched_switch_needs_its_access_key(hass):
	coordinator = _mock_coordinator(owner=False)
	handlers, _ = _handlers(hass, coordinator)
	_point_at(hass, "vs_pub")
	with pytest.raises(HomeAssistantError, match="access key"):
		await handlers["set_state"](_call(entity_id=["switch.pub"], state=True, note="hi"))
	coordinator.get_subscription_access_key = MagicMock(return_value="key")
	await handlers["set_state"](_call(entity_id=["switch.pub"], state=True, note="hi"))
	coordinator.toggle_switch_with_access_key.assert_awaited_once_with(
		"vs_pub", "key", desired_state=True, note="hi", params=None,
	)


@pytest.mark.asyncio
async def test_an_entity_that_is_not_a_vome_switch_is_refused(hass):
	coordinator = _mock_coordinator()
	handlers, _ = _handlers(hass, coordinator)
	hass.states.get = MagicMock(return_value=None)
	with pytest.raises(HomeAssistantError, match="not a Vome switch"):
		await handlers["set_state"](_call(entity_id=["light.kitchen"], state=True))


@pytest.mark.asyncio
async def test_set_commerce_action_returns_the_secret_and_maps_fields(hass):
	coordinator = _mock_coordinator(data={"is_owner": True, "commerce": {"price": {"amount": 500, "currency": "gbp"}}})
	coordinator.set_commerce = AsyncMock(return_value={"commerce": {"toggleMode": "paid"}, "webhookSecret": "whsec"})
	handlers, kwargs = _handlers(hass, coordinator)
	_point_at(hass)

	data = kwargs["set_commerce"]["schema"]({
		"entity_id": "switch.lamp", "toggle_mode": "paid", "price": 300, "action": True,
		"webhook_url": "https://hooks.example/x",
	})
	assert data["action"] == "on"  # YAML's bare on
	result = await handlers["set_commerce"](_call(**data))
	assert result == {"commerce": {"toggleMode": "paid"}, "webhook_secret": "whsec"}
	fields = coordinator.set_commerce.await_args.args[1]
	assert fields["toggleMode"] == "paid" and fields["action"] == "on"
	assert fields["price"] == {"amount": 300, "currency": "gbp"}  # keeps the current currency
	assert fields["webhookUrl"] == "https://hooks.example/x"

	with pytest.raises(HomeAssistantError, match="at least one"):
		await handlers["set_commerce"](_call(entity_id=["switch.lamp"], rotate_webhook_secret=False))

	coordinator.set_commerce = AsyncMock(side_effect=ExtrasError("Paid mode needs a price"))
	with pytest.raises(HomeAssistantError, match="Paid mode"):
		await handlers["set_commerce"](_call(entity_id=["switch.lamp"], toggle_mode="paid"))


@pytest.mark.asyncio
async def test_set_reward_action_merges_or_clears(hass):
	coordinator = _mock_coordinator(data={"is_owner": True, "reward": {"amount": 200, "currency": "sek", "on": "on"}})
	coordinator.set_reward = AsyncMock(return_value=None)
	handlers, _ = _handlers(hass, coordinator)
	_point_at(hass)

	await handlers["set_reward"](_call(entity_id=["switch.lamp"], on="any", clear=False))
	assert coordinator.set_reward.await_args.args[1] == {"amount": 200, "currency": "sek", "on": "any"}
	await handlers["set_reward"](_call(entity_id=["switch.lamp"], clear=True))
	assert coordinator.set_reward.await_args.args[1] is None


@pytest.mark.asyncio
async def test_set_properties_action(hass):
	coordinator = _mock_coordinator()
	coordinator.set_properties = AsyncMock(return_value={"wattage": 40})
	handlers, _ = _handlers(hass, coordinator)
	_point_at(hass)
	await handlers["set_properties"](_call(entity_id=["switch.lamp"], properties={"wattage": 40}, replace=True))
	coordinator.set_properties.assert_awaited_once_with("vs_a", {"wattage": 40}, replace=True)


@pytest.mark.asyncio
async def test_connect_payments_notifies_with_the_link(hass):
	coordinator = _mock_coordinator()
	coordinator.start_connect_onboarding = AsyncMock(return_value={"url": "https://connect.stripe.com/setup/x"})
	handlers, _ = _handlers(hass, coordinator)
	with patch("custom_components.vomesync.services_extras.persistent_notification.async_create") as notify:
		result = await handlers["connect_payments"](_call())
	assert result == {"url": "https://connect.stripe.com/setup/x"}
	assert "https://connect.stripe.com/setup/x" in notify.call_args.args[1]

	coordinator.start_connect_onboarding = AsyncMock(side_effect=VomeSyncAPIError("Payments are not enabled"))
	with pytest.raises(HomeAssistantError, match="not enabled"):
		await handlers["connect_payments"](_call())


@pytest.mark.asyncio
async def test_reading_actions_answer_with_the_server_data(hass):
	coordinator = _mock_coordinator()
	coordinator.get_connect_status = AsyncMock(return_value={
		"connected": True, "ready": False, "requirementsDue": ["external_account"],
	})
	coordinator.list_reward_claims = AsyncMock(return_value={"claims": [{"id": "c1"}], "totals": {}})
	coordinator.get_my_rewards = AsyncMock(return_value={"claims": [], "totals": {"sek": 400}})
	handlers, kwargs = _handlers(hass, coordinator)
	_point_at(hass)

	assert await handlers["payments_status"](_call()) == {
		"connected": True, "ready": False, "requirements_due": ["external_account"],
	}
	assert (await handlers["reward_claims"](_call(entity_id=["switch.lamp"])))["claims"] == [{"id": "c1"}]
	assert (await handlers["my_rewards"](_call()))["totals"] == {"sek": 400}


def test_response_types(hass):
	from homeassistant.core import SupportsResponse
	_, kwargs = _handlers(hass, _mock_coordinator())
	expected = {
		"set_state": SupportsResponse.NONE,
		"set_properties": SupportsResponse.NONE,
		"set_reward": SupportsResponse.NONE,
		"set_commerce": SupportsResponse.OPTIONAL,
		"connect_payments": SupportsResponse.OPTIONAL,
		"payments_status": SupportsResponse.ONLY,
		"reward_claims": SupportsResponse.ONLY,
		"my_rewards": SupportsResponse.ONLY,
	}
	for name, kind in expected.items():
		assert kwargs[name]["supports_response"] is kind, name


def test_every_action_is_in_services_yaml():
	import yaml
	from pathlib import Path
	from custom_components.vomesync.services_extras import EXTRAS_SERVICES
	path = Path(__file__).resolve().parents[2] / "custom_components" / "vomesync" / "services.yaml"
	declared = yaml.safe_load(path.read_text())
	assert EXTRAS_SERVICES <= set(declared)
	# A bare on in YAML would be read as true; the field must be named "on".
	assert "on" in declared["set_reward"]["fields"]


# ── Options ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_rewards_option_is_off_until_turned_on(hass, config_entry):
	from homeassistant.data_entry_flow import FlowResultType
	from custom_components.vomesync.config_flow import VomeSyncOptionsFlow

	config_entry.options = {"imported_switches": {}}
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	saved = {}

	async def _save(options):
		saved.update(options)
		config_entry.options = options
	flow._async_update_entry_options = _save
	flow.async_step_init = AsyncMock(return_value={"type": FlowResultType.MENU})

	result = await flow.async_step_switch_rewards(None)
	assert result["type"] == FlowResultType.FORM
	(field,) = list(result["data_schema"].schema)
	assert str(field) == "claim_rewards" and field.default() is False

	await flow.async_step_switch_rewards({"claim_rewards": True})
	assert saved["claim_rewards"] is True
	assert saved["imported_switches"] == {}


def test_claim_rewards_step_has_its_words():
	from pathlib import Path
	path = Path(__file__).resolve().parents[2] / "custom_components" / "vomesync" / "translations" / "en.json"
	strings = json.loads(path.read_text())["options"]["step"]
	assert "switch_rewards" in strings["more"]["menu_options"]
	assert strings["switch_rewards"]["data"]["claim_rewards"]
