"""Every name the integration uses is defined, and every signed request builds.

Vome 0.3.84 / 0.3.85 shipped a stray ``**(extra or {})`` in
build_v2_my_switches_request: a NameError on every poll, so every home that
updated lost its switches. The unit tests mock the API client, so nothing
built that request for real. These tests would have stopped the release.
"""

import inspect
from pathlib import Path

import pytest
from pyflakes import api as pyflakes_api
from pyflakes import messages as pyflakes_messages
from pyflakes import reporter as pyflakes_reporter

from custom_components.vomesync import crypto

REPO = Path(__file__).resolve().parents[2]
PACKAGES = [
	REPO / "custom_components" / "vomesync",
	REPO / "vome" / "custom_components" / "vomesync",
]

SEED = crypto.generate_master_seed_b64url()

# A plausible value for each required argument of a request builder.
SAMPLE_ARGS = {
	"index": 0,
	"uid": "vs_testswitch",
	"updates": {"name": "Lamp"},
	"key_id": "key1",
	"paused": True,
	"permissions": ["state"],
	"state": True,
	"change_at": 1_700_000_000_000,
	"entities": 2,
}

# Optional arguments a builder still needs one of.
EXTRA_ARGS = {
	"build_v2_revoke_access_key_request": {"key_id": "key1"},
}


class _Collect(pyflakes_reporter.Reporter):
	def __init__(self):
		super().__init__(None, None)
		self.problems = []

	def flake(self, message):
		if isinstance(message, (pyflakes_messages.UndefinedName, pyflakes_messages.UndefinedLocal)):
			self.problems.append(str(message))

	def syntaxError(self, filename, msg, lineno, offset, text):
		self.problems.append(f"{filename}:{lineno}: {msg}")

	def unexpectedError(self, filename, msg):
		self.problems.append(f"{filename}: {msg}")


@pytest.mark.parametrize("package", PACKAGES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_undefined_names(package):
	if not package.exists():
		pytest.skip("app copy not built")
	collect = _Collect()
	for path in sorted(package.rglob("*.py")):
		pyflakes_api.checkPath(str(path), collect)
	assert collect.problems == []


BUILDERS = sorted(
	(name, fn)
	for name, fn in inspect.getmembers(crypto, inspect.isfunction)
	if name.startswith("build_v2_") and name.endswith("_request")
)


def test_the_builders_are_found():
	names = [name for name, _ in BUILDERS]
	assert "build_v2_my_switches_request" in names
	assert len(names) >= 15


@pytest.mark.parametrize("name,builder", BUILDERS, ids=[name for name, _ in BUILDERS])
def test_every_signed_request_builds(name, builder):
	kwargs = dict(EXTRA_ARGS.get(name, {}))
	for param in list(inspect.signature(builder).parameters.values())[1:]:
		if param.default is inspect.Parameter.empty:
			assert param.name in SAMPLE_ARGS, f"{name} needs a sample for {param.name}"
			kwargs[param.name] = SAMPLE_ARGS[param.name]
	request = builder(SEED, **kwargs)
	if not isinstance(request, dict):
		request = vars(request)
	# Signed by the owner key, or by the switch's own key (set_state).
	assert any(key.startswith("sig") and value for key, value in request.items())


def test_my_switches_request_carries_only_the_signed_fields():
	request = crypto.build_v2_my_switches_request(SEED, ts=5, nonce="n" * 12)
	assert set(request) == {"ownerPubKey", "ts", "nonce", "sigOwner"}
	assert request["ownerPubKey"] == crypto.owner_pubkey_b64url(SEED)
