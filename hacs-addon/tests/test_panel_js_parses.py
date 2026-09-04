# flake8: noqa
"""The panel's JavaScript has to parse, or the whole panel is blank.

This exists because it did not. Commit bb7e035 (2026-08-30) landed
``Math.max(3, Number(res.interval) or 5)`` — Python's ``or``, in a .js
file — in ``vome/panel/static/app.js``. A syntax error anywhere in a
script means the browser runs *none* of it, so every view, every button
and the Connect flow were dead on the Develop branch from that commit
onward, with nothing in the Python test suite able to notice.

Two checks, because the tooling is not guaranteed:

* if node is on the box, ask it to parse the file properly;
* either way, scan for the Python-isms that actually cause this — they
  are what a Python-shaped brain types into a .js file at the end of a
  long day.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = sorted((ROOT / "vome" / "panel" / "static").glob("*.js"))


def test_there_is_panel_javascript_to_check():
	assert SCRIPTS, "no panel scripts found — has the panel moved?"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_can_parse_it(script):
	result = subprocess.run(
		["node", "--check", str(script)], capture_output=True, text=True,
	)
	assert result.returncode == 0, f"{script.name} does not parse:\n{result.stderr}"


# Python keywords and literals that are syntax errors (or silently wrong)
# in JavaScript. Word-bounded, and only outside strings and comments.
PYTHONISMS = (
	(r"\)\s+or\s", "Python `or` — JavaScript wants `||`"),
	(r"\)\s+and\s", "Python `and` — JavaScript wants `&&`"),
	(r"[=!]==\s*(True|False|None)\b", "Python literal — JavaScript wants true/false/null"),
	(r"\belif\b", "Python `elif` — JavaScript wants `else if`"),
	(r"^\s*def\s+\w+\(", "Python `def` — JavaScript wants `function`"),
)


def _strip_strings_and_comments(source: str) -> str:
	"""Blank out everything a keyword may legally sit inside.

	Hand-rolled rather than a regex because this file is full of nested
	template literals (``${cond ? `a` : `b`}``), and a regex that cannot
	count ``${`` depth mistakes prose for code: the first version of this
	test failed on the sentence "run the tunnel and leave it running".
	"""
	out = []
	i, n = 0, len(source)
	# Stack of template-literal depths we are inside; each ``${`` pushes a
	# brace counter so the code inside an interpolation is still scanned.
	template_stack = []
	while i < n:
		ch = source[i]
		nxt = source[i + 1] if i + 1 < n else ""
		if ch == "/" and nxt == "/":
			while i < n and source[i] != "\n":
				i += 1
			continue
		if ch == "/" and nxt == "*":
			i += 2
			while i < n and not (source[i] == "*" and source[i + 1: i + 2] == "/"):
				i += 1
			i += 2
			out.append(" ")
			continue
		if ch in "\"'":
			quote = ch
			i += 1
			while i < n and source[i] != quote:
				i += 2 if source[i] == "\\" else 1
			i += 1
			out.append('""')
			continue
		if ch == "`":
			template_stack.append(0)
			i += 1
			# Skip literal text until the template ends or an interpolation
			# starts; interpolated code falls through to the normal scanner.
			while i < n:
				if source[i] == "\\":
					i += 2
					continue
				if source[i] == "`":
					template_stack.pop()
					i += 1
					break
				if source[i] == "$" and source[i + 1: i + 2] == "{":
					i += 2
					break
				i += 1
			out.append('""')
			continue
		if ch == "}" and template_stack:
			# End of an interpolation: back into literal text.
			i += 1
			while i < n:
				if source[i] == "\\":
					i += 2
					continue
				if source[i] == "`":
					template_stack.pop()
					i += 1
					break
				if source[i] == "$" and source[i + 1: i + 2] == "{":
					i += 2
					break
				i += 1
			out.append(" ")
			continue
		out.append(ch)
		i += 1
	return "".join(out)


def test_the_stripper_keeps_code_and_drops_prose():
	"""The scan is only as good as this — pin both directions."""
	stripped = _strip_strings_and_comments(
		"const a = `run it (in ${os.shell}) and leave it`;\n"
		"if (x) or (y) {}\n"
		"// a comment saying elif\n"
	)
	assert "and leave it" not in stripped, "prose survived the stripper"
	assert "elif" not in stripped, "a comment survived the stripper"
	assert "or" in stripped, "real code was stripped along with the prose"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_python_slipped_into_the_javascript(script):
	code = _strip_strings_and_comments(script.read_text(encoding="utf-8"))
	for pattern, why in PYTHONISMS:
		match = re.search(pattern, code, flags=re.M)
		assert match is None, (
			f"{script.name}: {why} — near {code[max(0, match.start() - 60):match.end() + 20].strip()!r}"
		)
