# flake8: noqa
"""Which webhook calls may cross the tunnel from the public internet.

A Home Assistant webhook id *is* the credential — Core authenticates the caller
purely by knowing it. So the matching here is a security boundary, not
convenience routing, and the hostile-path cases below are the point of the
module rather than decoration.
"""
import pytest

from custom_components.vomesync.webhooks import (
	is_forwardable_webhook,
	normalise_webhooks,
	valid_webhook_id,
	webhook_id_for_path,
)

ALLOWED = ["abc123", "second-hook_9"]


class TestWebhookIdValidation:
	@pytest.mark.parametrize("good", ["abc123", "a", "A-b_9", "x" * 128])
	def test_accepts_real_shapes(self, good):
		assert valid_webhook_id(good) is True

	@pytest.mark.parametrize("bad", [
		"", None, 123, "x" * 129, "has space", "a/b", "a.b", "a?b", "a#b",
		"..", "a%2Fb", "a:b",
	])
	def test_rejects_everything_else(self, bad):
		assert valid_webhook_id(bad) is False


class TestPathMatching:
	def test_matches_a_plain_webhook_path(self):
		assert webhook_id_for_path("/api/webhook/abc123") == "abc123"

	def test_query_string_is_allowed_and_ignored(self):
		assert webhook_id_for_path("/api/webhook/abc123?x=1&y=2") == "abc123"

	def test_fragment_is_stripped(self):
		assert webhook_id_for_path("/api/webhook/abc123#frag") == "abc123"

	@pytest.mark.parametrize("hostile", [
		"/api/webhook/abc123/extra",          # extra segment
		"/api/webhook/../states",             # traversal
		"/api/webhook/abc123/../../states",   # traversal after a valid id
		"/api/webhook/",                      # no id at all
		"/api/webhook",                       # prefix only
		"/api/states",                        # different endpoint
		"/api/webhook/%2e%2e/states",         # encoded traversal
		"/api/webhook/abc%2F123",             # encoded slash inside the id
		"api/webhook/abc123",                 # not absolute
		"//api/webhook/abc123",               # protocol-relative
	])
	def test_refuses_anything_that_is_not_exactly_one_id(self, hostile):
		assert webhook_id_for_path(hostile) is None

	def test_percent_encoded_id_is_refused_even_when_it_decodes_valid(self):
		# %61 is 'a'. Decoding then matching would approve a URL that the HTTP
		# client may later normalise differently to the one we checked.
		assert webhook_id_for_path("/api/webhook/%61bc123") is None

	def test_non_string_path_is_safe(self):
		assert webhook_id_for_path(None) is None
		assert webhook_id_for_path(123) is None


class TestForwardDecision:
	def test_allows_a_listed_webhook(self):
		assert is_forwardable_webhook("/api/webhook/abc123", "POST", ALLOWED) is True

	def test_refuses_an_unlisted_webhook(self):
		# The whole reason this is an allowlist: a real, working webhook that
		# the owner has not chosen to publish must stay unreachable.
		assert is_forwardable_webhook("/api/webhook/notlisted", "POST", ALLOWED) is False

	def test_refuses_when_nothing_is_allowlisted(self):
		assert is_forwardable_webhook("/api/webhook/abc123", "POST", []) is False

	@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "HEAD", "post", "get"])
	def test_permits_the_methods_home_assistant_registers(self, method):
		assert is_forwardable_webhook("/api/webhook/abc123", method, ALLOWED) is True

	@pytest.mark.parametrize("method", ["DELETE", "PATCH", "OPTIONS", "TRACE", "", None])
	def test_refuses_other_methods(self, method):
		assert is_forwardable_webhook("/api/webhook/abc123", method, ALLOWED) is False

	def test_traversal_is_refused_even_for_a_listed_id(self):
		assert is_forwardable_webhook(
			"/api/webhook/abc123/../../states", "POST", ALLOWED) is False


class TestNormalise:
	def test_keeps_valid_ids_in_order(self):
		assert normalise_webhooks(["b", "a"]) == ["b", "a"]

	def test_drops_invalid_and_duplicates(self):
		assert normalise_webhooks(["ok", "ok", "bad/id", "", None, 5]) == ["ok"]

	def test_accepts_dict_records_for_forward_compatibility(self):
		# So a label or created-at can be added later without breaking config.
		assert normalise_webhooks([{"id": "abc"}, {"id": "bad/id"}]) == ["abc"]

	def test_non_list_input_is_empty(self):
		assert normalise_webhooks(None) == []
		assert normalise_webhooks("abc") == []

	def test_caps_the_number_exposed(self):
		from custom_components.vomesync.const import WEBHOOK_MAX
		assert len(normalise_webhooks([f"h{i}" for i in range(WEBHOOK_MAX + 10)])) == WEBHOOK_MAX
