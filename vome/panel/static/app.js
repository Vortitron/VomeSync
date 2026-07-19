/* Vome add-on control panel — talks to /api/* on the same origin (ingress). */
(function () {
	const viewEl = document.getElementById("view");
	const titleEl = document.getElementById("view-title");
	const bannerEl = document.getElementById("banner");
	let state = null;
	let current = "overview";

	const titles = {
		overview: "Overview",
		forward: "Home Assistant UI",
		lan: "LAN tunnels",
		link: "Link status",
		about: "About",
	};

	function showBanner(msg, isErr) {
		bannerEl.textContent = msg;
		bannerEl.classList.toggle("hidden", !msg);
		bannerEl.classList.toggle("err", !!isErr);
	}

	// API calls must be RELATIVE to the current document. The panel is served
	// under Home Assistant's ingress prefix (/api/hassio_ingress/<token>/), so a
	// leading-slash path would escape that prefix and never reach this panel
	// (which surfaces as "Invalid JSON from panel API"). `apiBase` is the
	// document's directory, so "api/status" resolves under the ingress path just
	// like the relative <script>/<link> tags in index.html already do.
	const apiBase = window.location.pathname.replace(/[^/]*$/, "");

	async function api(path, opts) {
		const url = path.replace(/^\//, "");
		const res = await fetch(apiBase + url, {
			headers: { "Content-Type": "application/json", Accept: "application/json" },
			...opts,
		});
		let data = {};
		try {
			data = await res.json();
		} catch (_err) {
			data = { error: "Invalid JSON from panel API" };
		}
		if (!res.ok) {
			const msg = data.error || data.message || `HTTP ${res.status}`;
			const error = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
			error.data = data;
			throw error;
		}
		return data;
	}

	// "400: Bad Request" with no detail is Home Assistant's answer when the
	// vomesync services don't exist at all — the integration isn't running.
	function friendlyStatusError(err) {
		const msg = String(err.message || err);
		if (/^400\b|Bad Request/i.test(msg)) {
			return "The Vome integration isn't running inside Home Assistant. " +
				"Restart Home Assistant (Settings → System → ⋮ → Restart) to load it. " +
				"If this comes back after a restart, open Settings → System → Logs and search for “vomesync” — the error there is the real cause.";
		}
		return msg;
	}

	function restartNeeded() {
		return !!(state && state.integration_version && state.installed_version
			&& state.integration_version !== state.installed_version);
	}

	async function refresh() {
		showBanner("");
		try {
			state = await api("/api/status");
			if (restartNeeded()) {
				showBanner(`Vome ${state.installed_version} is installed but Home Assistant is still running ${state.integration_version}. Restart Home Assistant (Settings → System → ⋮ → Restart) to finish the update.`, true);
			}
			render();
		} catch (err) {
			showBanner(friendlyStatusError(err), true);
			state = state || { linked: false, lan_routes: [], forward_ui: false };
			if (err.data && typeof err.data === "object") {
				state.installed_version = err.data.installed_version || state.installed_version;
			}
			render();
		}
	}

	function setView(name) {
		current = name;
		titleEl.textContent = titles[name] || name;
		document.querySelectorAll(".tree-item").forEach((btn) => {
			btn.classList.toggle("active", btn.dataset.view === name);
		});
		render();
	}

	function pill(ok, onLabel, offLabel) {
		return `<span class="pill ${ok ? "ok" : "off"}">${ok ? onLabel : offLabel}</span>`;
	}

	function renderOverview() {
		const routes = (state && state.lan_routes) || [];
		const enabled = routes.filter((r) => r.enabled !== false).length;
		const restartCard = restartNeeded() ? `
			<div class="card warn-card">
				<h2>Restart needed to finish updating</h2>
				<p class="muted">The app installed Vome integration <strong>${escapeHtml(state.installed_version)}</strong>, but Home Assistant is still running <strong>${escapeHtml(state.integration_version)}</strong>. Home Assistant only loads integration code at startup.</p>
				<p class="muted">Go to <strong>Settings → System → ⋮ (top right) → Restart Home Assistant</strong>, then come back here.</p>
			</div>` : "";
		viewEl.innerHTML = `${restartCard}
			<div class="card">
				<h2>Status</h2>
				<p class="muted">Same settings as the HACS options menu, laid out as a tree so remote access and LAN tunnels are easier to find.</p>
				<div class="row">
					${pill(!!(state && state.linked), "Linked to Vome", "Not linked")}
					${pill(!!(state && state.forward_ui), "HA UI forwarding on", "HA UI forwarding off")}
					${pill(enabled > 0, `${enabled} LAN tunnel${enabled === 1 ? "" : "s"} on`, "No LAN tunnels")}
					${pill(!!(state && state.addon_marker), "Add-on install", "HACS-only install")}
				</div>
			</div>
			<div class="card">
				<h2>Quick actions</h2>
				<div class="row">
					<button type="button" class="primary" id="qa-rdp">Set up Remote Desktop</button>
					<button type="button" id="qa-lan">LAN tunnels</button>
					<button type="button" id="qa-forward">Home Assistant UI</button>
				</div>
				<p class="muted" style="margin-top:0.8rem">After a friendly domain is active on vome.io: Home Assistant opens at the domain root (needs UI forwarding on), LAN web devices at <code>/t/&lt;slug&gt;/</code>, and Remote Desktop through a tunnel token.</p>
			</div>`;
		document.getElementById("qa-lan").onclick = () => setView("lan");
		document.getElementById("qa-forward").onclick = () => setView("forward");
		document.getElementById("qa-rdp").onclick = () => {
			setView("lan");
			const preset = document.getElementById("preset-rdp");
			if (preset) preset.click();
		};
	}

	function renderForward() {
		const on = !!(state && state.forward_ui);
		viewEl.innerHTML = `
			<div class="card">
				<h2>Full-UI forwarding</h2>
				<p class="muted">Exposes this Home Assistant on your friendly domain. Keep it off unless you need browser access to HA itself. LAN tunnels work independently.</p>
				<div class="toggle">
					<input type="checkbox" id="fwd" ${on ? "checked" : ""}>
					<label for="fwd">Allow full-UI forwarding</label>
				</div>
				<div class="row">
					<button type="button" class="primary" id="save-fwd">Save</button>
				</div>
			</div>`;
		document.getElementById("save-fwd").onclick = async () => {
			try {
				const enabled = document.getElementById("fwd").checked;
				state = await api("/api/forward_ui", {
					method: "POST",
					body: JSON.stringify({ forward_ui: enabled }),
				});
				showBanner(enabled ? "Full-UI forwarding enabled." : "Full-UI forwarding disabled.");
				render();
			} catch (err) {
				showBanner(String(err.message || err), true);
			}
		};
	}

	// Last minted LAN-TCP tunnel token, shown inline after "Get tunnel token"
	// (not persisted — re-minted on demand; tokens are short-lived anyway).
	let lastTunnelToken = null;

	// Which OS tab the connect instructions show. Defaults to the visitor's OS.
	const ua = navigator.platform || navigator.userAgent || "";
	let tunnelOs = /win/i.test(ua) ? "windows" : /mac/i.test(ua) ? "mac" : "linux";

	function tunnelDiagram(route, localPort) {
		const target = route ? `${escapeHtml(route.host)}:${route.port}` : "device";
		return `
			<div class="tunnel-diagram">
				<svg viewBox="0 0 850 96" role="img" aria-label="Tunnel path: your computer, over TLS to the Vome relay, over the existing outbound link to Home Assistant, then over your LAN to the device.">
					<g class="node"><rect x="8" y="22" width="140" height="50" rx="10"/><text x="78" y="43">Your computer</text><text x="78" y="59" class="sub">client → 127.0.0.1:${localPort}</text></g>
					<line class="flow" x1="148" y1="47" x2="238" y2="47"/><text x="193" y="38" class="lbl">wss · TLS</text>
					<g class="node"><rect x="238" y="22" width="140" height="50" rx="10"/><text x="308" y="43">Vome relay</text><text x="308" y="59" class="sub">sync.vome.io</text></g>
					<line class="flow" x1="378" y1="47" x2="468" y2="47"/><text x="423" y="38" class="lbl">existing link</text>
					<g class="node"><rect x="468" y="22" width="140" height="50" rx="10"/><text x="538" y="43">Home Assistant</text><text x="538" y="59" class="sub">Vome App</text></g>
					<line class="flow" x1="608" y1="47" x2="698" y2="47"/><text x="653" y="38" class="lbl">LAN</text>
					<g class="node target"><rect x="698" y="22" width="144" height="50" rx="10"/><text x="770" y="43">${escapeHtml((route && route.name) || "Device")}</text><text x="770" y="59" class="sub">${target}</text></g>
				</svg>
				<p class="muted small">Every hop is outbound and encrypted — nothing on your home network is opened to the internet. Home Assistant already holds the middle link; the token only unlocks this one route, for a limited time.</p>
			</div>`;
	}

	function renderLan() {
		const routes = (state && state.lan_routes) || [];
		const rows = routes.map((r) => `
			<tr>
				<td><strong>${escapeHtml(r.name || r.slug)}</strong><br><code>/t/${escapeHtml(r.slug)}/</code></td>
				<td class="mono">${escapeHtml(r.scheme)}://${escapeHtml(r.host)}:${r.port}</td>
				<td>${r.enabled === false ? pill(false, "", "off") : pill(true, "on", "")}</td>
				<td>
					${r.scheme === "tcp" ? `<button type="button" data-token="${escapeHtml(r.slug)}">Get tunnel token</button>` : ""}
					<button type="button" class="danger" data-remove="${escapeHtml(r.slug)}">Remove</button>
				</td>
			</tr>`).join("");
		const localPort = (lastTunnelToken && lastTunnelToken.localPort) || 3390;
		const cmd = lastTunnelToken
			? `npx @vortitron/home-assistant-mcp@latest tunnel --token ${lastTunnelToken.token} --local-port ${localPort}`
			: "";
		const tokRoute = lastTunnelToken ? routes.find((r) => r.slug === lastTunnelToken.slug) : null;
		const isRdp = !!(tokRoute && Number(tokRoute.port) === 3389);
		const osMeta = {
			windows: {
				label: "Windows",
				install: "winget install OpenJS.NodeJS.LTS",
				installNote: `one-time, in PowerShell — or the installer from <a href="https://nodejs.org" target="_blank" rel="noreferrer">nodejs.org</a>`,
				shell: "PowerShell",
				client: isRdp
					? `<strong>Remote Desktop Connection</strong> is built in: press Start, type <code>mstsc</code>, and connect to <code>127.0.0.1:${localPort}</code>.`
					: `Point your client at <code>127.0.0.1:${localPort}</code>.`,
			},
			mac: {
				label: "macOS",
				install: "brew install node",
				installNote: `one-time, in Terminal — or the installer from <a href="https://nodejs.org" target="_blank" rel="noreferrer">nodejs.org</a>`,
				shell: "Terminal",
				client: isRdp
					? `Install <strong>Windows App</strong> from the App Store (Microsoft's RDP client) → Add PC → <code>127.0.0.1:${localPort}</code>.`
					: `Point your client at <code>127.0.0.1:${localPort}</code>.`,
			},
			linux: {
				label: "Linux",
				install: "sudo apt install nodejs npm",
				installNote: "one-time, Debian/Ubuntu — use your distro's package manager otherwise",
				shell: "a terminal",
				client: isRdp
					? `<strong>Remmina</strong> (with the RDP plugin) → <code>rdp://127.0.0.1:${localPort}</code>.`
					: `Point your client at <code>127.0.0.1:${localPort}</code>.`,
			},
		};
		const os = osMeta[tunnelOs] || osMeta.linux;
		const osTabs = Object.entries(osMeta).map(([key, meta]) =>
			`<button type="button" data-os="${key}" class="${key === tunnelOs ? "active" : ""}">${meta.label}</button>`).join("");
		const tokenCard = lastTunnelToken ? `
			<div class="card">
				<h2>Connect to <code>${escapeHtml(lastTunnelToken.slug)}</code> (${escapeHtml(lastTunnelToken.scheme || "tcp")})</h2>
				${tunnelDiagram(tokRoute, localPort)}
				<div class="os-tabs" role="tablist" aria-label="Operating system">${osTabs}</div>
				<ol class="steps">
					<li>On the ${os.label} machine you want to connect <em>from</em>, install Node.js:
						<div class="cmd-row"><input class="mono cmd" readonly value="${escapeHtml(os.install)}" onclick="this.select()"></div>
						<p class="muted small">${os.installNote}.</p>
					</li>
					<li>Run the tunnel (in ${os.shell}) and leave it running:
						<div class="cmd-row"><input class="mono cmd" id="tunnel-cmd" readonly value="${escapeHtml(cmd)}" onclick="this.select()"><button type="button" id="copy-cmd">Copy</button></div>
						<label class="field small">Local port<input id="tok-port" type="number" min="1" max="65535" value="${localPort}"></label>
					</li>
					<li>${os.client}
						${isRdp ? "" : `<p class="muted small">SSH: <code>ssh -p ${localPort} user@127.0.0.1</code> · VNC: <code>127.0.0.1:${localPort}</code> · databases: same address.</p>`}
					</li>
				</ol>
				<p class="muted small">The token inside the command is valid for ${Math.round(lastTunnelToken.ttlSeconds / 60)} minutes — after that, just mint a new one here. It only reaches this one route.</p>
			</div>` : "";
		viewEl.innerHTML = `
			<div class="card">
				<h2>Configured tunnels</h2>
				<p class="muted"><strong>http/https</strong> routes open in a browser at <code>/t/&lt;slug&gt;/</code> on your friendly domain after Vome sign-in. <strong>tcp</strong> routes (Remote Desktop, SSH, VNC…) aren't browser-reachable — click <em>Get tunnel token</em> on the route for a one-line command that bridges them to your local machine.</p>
				${routes.length ? `<table><thead><tr><th>Route</th><th>Target</th><th>State</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="muted">No LAN routes yet — add one below. For Remote Desktop, use the <strong>RDP</strong> preset.</p>`}
			</div>
			${tokenCard}
			<div class="card">
				<h2>Add a tunnel</h2>
				<div class="row">
					<button type="button" id="preset-rdp">Remote Desktop (RDP) preset</button>
					<span class="muted">fills in a raw-tcp route to port 3389 — just set the host.</span>
				</div>
				<div class="row">
					<label class="field">Slug<input id="slug" placeholder="rdp"></label>
					<label class="field">Name<input id="name" placeholder="Office PC"></label>
					<label class="field">Host<input id="host" placeholder="192.168.1.50"></label>
					<label class="field">Port<input id="port" type="number" value="80" min="1" max="65535"></label>
					<label class="field">Scheme
						<select id="scheme"><option>http</option><option>https</option><option>tcp</option></select>
					</label>
				</div>
				<div class="row">
					<label class="toggle"><input type="checkbox" id="enabled" checked> Enabled</label>
					<label class="toggle" id="websocket-row"><input type="checkbox" id="websocket" checked> WebSocket</label>
					<button type="button" class="primary" id="add-route">Add route</button>
				</div>
				<p class="muted small" id="scheme-hint"></p>
			</div>`;
		document.querySelectorAll("[data-remove]").forEach((btn) => {
			btn.onclick = async () => {
				try {
					state = await api("/api/lan_routes/remove", {
						method: "POST",
						body: JSON.stringify({ slug: btn.dataset.remove }),
					});
					lastTunnelToken = null;
					showBanner(`Removed /t/${btn.dataset.remove}/`);
					render();
				} catch (err) {
					showBanner(String(err.message || err), true);
				}
			};
		});
		document.querySelectorAll("[data-token]").forEach((btn) => {
			btn.onclick = async () => {
				try {
					btn.disabled = true;
					btn.textContent = "Minting…";
					const slug = btn.dataset.token;
					const route = routes.find((r) => r.slug === slug) || {};
					const result = await api("/api/lan_routes/token", {
						method: "POST",
						body: JSON.stringify({ slug }),
					});
					lastTunnelToken = {
						slug,
						scheme: route.scheme,
						token: result.token,
						ttlSeconds: result.ttl_seconds || 3600,
						localPort: route.port === 3389 ? 3390 : (route.port || 3390),
					};
					showBanner(`Tunnel token ready for ${slug} — see the command below.`);
					render();
				} catch (err) {
					btn.disabled = false;
					btn.textContent = "Get tunnel token";
					showBanner(String(err.message || err), true);
				}
			};
		});
		const copyBtn = document.getElementById("copy-cmd");
		if (copyBtn) {
			copyBtn.onclick = () => {
				const input = document.getElementById("tunnel-cmd");
				input.select();
				navigator.clipboard?.writeText(input.value).catch(() => document.execCommand("copy"));
				showBanner("Command copied to clipboard.");
			};
		}
		document.querySelectorAll(".os-tabs [data-os]").forEach((btn) => {
			btn.onclick = () => {
				tunnelOs = btn.dataset.os;
				render();
			};
		});
		const tokPort = document.getElementById("tok-port");
		if (tokPort) {
			tokPort.onchange = () => {
				const p = Number(tokPort.value);
				if (lastTunnelToken && p >= 1 && p <= 65535) {
					lastTunnelToken.localPort = p;
					render();
				}
			};
		}
		const schemeEl = document.getElementById("scheme");
		const portEl = document.getElementById("port");
		const websocketRow = document.getElementById("websocket-row");
		const schemeHint = document.getElementById("scheme-hint");
		const updateSchemeUI = () => {
			const isTcp = schemeEl.value === "tcp";
			websocketRow.classList.toggle("hidden", isTcp);
			schemeHint.textContent = isTcp
				? "Raw TCP: not browser-reachable. After adding, use “Get tunnel token” to connect (RDP = 3389, SSH = 22, VNC = 5900)."
				: "";
		};
		schemeEl.onchange = updateSchemeUI;
		updateSchemeUI();
		document.getElementById("preset-rdp").onclick = () => {
			document.getElementById("slug").value = document.getElementById("slug").value || "rdp";
			document.getElementById("name").value = document.getElementById("name").value || "Remote Desktop";
			schemeEl.value = "tcp";
			portEl.value = 3389;
			updateSchemeUI();
			document.getElementById("host").focus();
		};
		document.getElementById("add-route").onclick = async () => {
			const btn = document.getElementById("add-route");
			try {
				const payload = {
					slug: document.getElementById("slug").value.trim(),
					name: document.getElementById("name").value.trim(),
					host: document.getElementById("host").value.trim(),
					port: Number(document.getElementById("port").value),
					scheme: document.getElementById("scheme").value,
					enabled: document.getElementById("enabled").checked,
					websocket: document.getElementById("websocket").checked,
				};
				if (!payload.slug || !payload.host) {
					showBanner("Slug and host are required.", true);
					return;
				}
				btn.disabled = true;
				btn.textContent = "Adding…";
				state = await api("/api/lan_routes/add", {
					method: "POST",
					body: JSON.stringify(payload),
				});
				showBanner(payload.scheme === "tcp"
					? `Added ${payload.slug} — now click “Get tunnel token” on it to connect.`
					: `Added /t/${payload.slug}/`);
				render();
			} catch (err) {
				btn.disabled = false;
				btn.textContent = "Add route";
				showBanner(String(err.message || err), true);
			}
		};
	}

	function renderLink() {
		viewEl.innerHTML = `
			<div class="card">
				<h2>Vome Home link</h2>
				<p class="muted">Linking is done once in Home Assistant (Devices &amp; services → Vome → Connect to Vome Home). This panel manages remote access after that.</p>
				<div class="row">
					${pill(!!(state && state.linked), "Linked", "Not linked")}
					${state && state.server_id ? `<span class="mono">server_id=${escapeHtml(state.server_id)}</span>` : ""}
				</div>
			</div>`;
	}

	function renderAbout() {
		const running = (state && state.integration_version) || "unknown";
		const installed = (state && state.installed_version) || "unknown";
		viewEl.innerHTML = `
			<div class="card">
				<h2>Versions</h2>
				<p class="muted">Integration running in Home Assistant: <code>${escapeHtml(running)}</code><br>
				Integration installed on disk: <code>${escapeHtml(installed)}</code></p>
				${running !== installed ? `<p class="muted"><strong>They differ — restart Home Assistant to load the installed version.</strong></p>` : ""}
			</div>
			<div class="card">
				<h2>How the app and HACS relate</h2>
				<p class="muted">This app bundles the Vome integration and installs it into Home Assistant on every app start — HACS is <em>not</em> required. If you previously installed it via HACS, the app's copy replaces it (same code, app-managed). Home Assistant only loads integration code at startup, so integration updates always need one HA restart — the panel tells you when.</p>
			</div>`;
	}

	function render() {
		if (current === "overview") renderOverview();
		else if (current === "forward") renderForward();
		else if (current === "lan") renderLan();
		else if (current === "link") renderLink();
		else renderAbout();
	}

	function escapeHtml(value) {
		return String(value ?? "")
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}

	document.querySelectorAll(".tree-item").forEach((btn) => {
		btn.addEventListener("click", () => setView(btn.dataset.view));
	});
	document.getElementById("btn-refresh").addEventListener("click", refresh);
	refresh();
})();
