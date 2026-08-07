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
		link: "Vome account",
		switches: "Switches",
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
		if (!state || !state.installed_version) return false;
		// Old running code predates the version field entirely — if the disk
		// has a version but the running integration reports none, it's stale.
		if (!state.integration_version) return true;
		return state.integration_version !== state.installed_version;
	}

	// Echo the shown entry's id back on writes so they target the same Home
	// Assistant even when several Vome integrations are linked (which would
	// otherwise make the write ambiguous and fail).
	function withEntry(obj) {
		const id = state && state.entry_id;
		return id ? { ...obj, entry_id: id } : obj;
	}

	// Filled by /api/diag whenever the status call fails (or on demand from
	// About) — turns raw failures into a plain-language verdict.
	let lastDiag = null;

	function diagVerdict(d) {
		if (!d.config_mounted) {
			return "The app cannot see Home Assistant's config folder, so the integration can never be installed from here. Update the Vome app (0.3.1 fixed the folder mapping), then restart Home Assistant.";
		}
		if (!d.integration_on_disk) {
			return "The integration isn't installed on disk. Restart the Vome app (it installs on every start), then check the app's Log tab for install errors.";
		}
		if (!d.vomesync_services || d.vomesync_services.length === 0) {
			return `Integration ${d.installed_version || "?"} is on disk but Home Assistant hasn't loaded it. Restart Home Assistant; if this persists, open Settings → System → Logs and search “vomesync”.`;
		}
		if (!d.vomesync_services.includes("mint_lan_tcp_token")) {
			return `Home Assistant is running an OLD Vome integration (it lacks the tunnel services). Version ${d.installed_version || "?"} is installed on disk — restart Home Assistant to load it.`;
		}
		// Disk vs running mismatch is the decisive tell: writes fail because HA
		// is executing stale code even though the new files are on disk.
		if (d.installed_version && d.running_version !== d.installed_version) {
			return `Home Assistant is running Vome ${d.running_version || "an older, version-less build"}, but ${d.installed_version} is installed on disk — that mismatch is exactly why writes fail. Restart Home Assistant (Settings → System → ⋮ → Restart) to load ${d.installed_version}.`;
		}
		if (typeof d.write_probe_status === "number" && d.write_probe_status >= 400) {
			return `A test write was rejected by Home Assistant with a bare HTTP ${d.write_probe_status} (“${d.write_probe_body}”) even though running and installed match (${d.installed_version}) on HA ${d.ha_version || "?"}. That's Home Assistant rejecting the call before our code runs — send this whole card and I'll pin it.`;
		}
		return `Writes work — the test write reached our code and was correctly rejected for bad input (“${d.write_probe_body}”). Adding a real route with a valid slug + host should succeed. If a specific add fails, it's about that input (e.g. a duplicate slug).`;
	}

	function diagCard() {
		if (!lastDiag) return "";
		const d = lastDiag;
		const svc = (d.vomesync_services || []).join(", ") || "none";
		return `
			<div class="card warn-card">
				<h2>Diagnostics</h2>
				<p class="muted"><strong>${escapeHtml(diagVerdict(d))}</strong></p>
				<p class="muted small mono">running in HA: ${escapeHtml(d.running_version || "unknown (old build)")} · on disk: ${d.integration_on_disk ? (d.installed_version || "yes") : "NO"} · app bundles: ${escapeHtml(d.bundled_version || "?")} · HA ${escapeHtml(d.ha_version || "?")}<br>config mount: ${escapeHtml(String(d.config_root))} · Core API: ${escapeHtml(String(d.core_api))}<br>write probe: HTTP ${escapeHtml(String(d.write_probe_status))} — ${escapeHtml(String(d.write_probe_body || ""))}<br>vomesync services loaded: ${escapeHtml(svc)}</p>
			</div>`;
	}

	async function runDiagnostics() {
		try {
			lastDiag = await api("api/diag");
		} catch (_err) {
			lastDiag = null;
		}
	}

	async function refresh() {
		showBanner("");
		try {
			state = await api("/api/status");
			lastDiag = null;
			if (restartNeeded()) {
				showBanner(`Vome ${state.installed_version} is installed but Home Assistant is still running ${state.integration_version}. Restart Home Assistant (Settings → System → ⋮ → Restart) to finish the update.`, true);
			} else if (state.warning) {
				showBanner(state.warning, true);
			}
			render();
		} catch (err) {
			showBanner(friendlyStatusError(err), true);
			state = state || { linked: false, lan_routes: [], forward_ui: false };
			if (err.data && typeof err.data === "object") {
				state.installed_version = err.data.installed_version || state.installed_version;
			}
			await runDiagnostics();
			render();
		}
	}

	function setView(name) {
		// Leaving the link view abandons an in-flight approval wait.
		if (current === "link" && name !== "link") {
			linkFlow = null;
			stopLinkPolling();
		}
		current = name;
		titleEl.textContent = titles[name] || name;
		document.querySelectorAll(".tree-item").forEach((btn) => {
			btn.classList.toggle("active", btn.dataset.view === name);
		});
		if (name === "switches" && switchesData === null) loadSwitches();
		render();
	}

	function pill(ok, onLabel, offLabel) {
		return `<span class="pill ${ok ? "ok" : "off"}">${ok ? onLabel : offLabel}</span>`;
	}

	// Home Assistant 2026.8 turned the listen port and trusted proxies into
	// ordinary settings, so both can now change under a running relay. Neither
	// failure announces itself — the relay stays connected and the status stays
	// green while the forwarded requests fail — so surface them here.
	function connectionWarningCard() {
		if (!state || !state.linked) return "";
		const proxy = state.trusted_proxy || {};
		if (proxy.ok === false) {
			return `
			<div class="card warn-card">
				<h2>Home Assistant will refuse forwarded requests</h2>
				<p class="muted">${escapeHtml(proxy.hint)}</p>
			</div>`;
		}
		if (state.local_url_source === "fallback") {
			return `
			<div class="card warn-card">
				<h2>Can't tell which address Home Assistant is on</h2>
				<p class="muted">Vome is guessing <code>${escapeHtml(state.local_url || "")}</code>. If remote access doesn't work, set the address yourself under <strong>Home Assistant UI</strong>.</p>
			</div>`;
		}
		return "";
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
		const linkCard = (state && !state.linked && !restartNeeded()) ? `
			<div class="card warn-card">
				<h2>Connect to Vome to get started</h2>
				<p class="muted">This Home Assistant isn't linked to a Vome account yet, so remote access and LAN tunnels aren't available. Linking takes about a minute and opens no ports.</p>
				<div class="row"><button type="button" class="primary" id="ov-connect">Connect to Vome</button></div>
			</div>` : "";
		viewEl.innerHTML = `${restartCard}${linkCard}${connectionWarningCard()}
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
		const connectBtn = document.getElementById("ov-connect");
		if (connectBtn) connectBtn.onclick = () => setView("link");
	}

	function localUrlNote() {
		const source = (state && state.local_url_source) || "";
		if (source === "override") return "Set by you. Clear the box to go back to detecting it automatically.";
		if (source === "fallback") return "Couldn't be detected — this is a guess. If remote access doesn't work, fill it in yourself.";
		return "Detected from the port Home Assistant is listening on. Leave blank unless remote access doesn't work.";
	}

	function renderWebhooks() {
		const hooks = (state && state.webhooks) || [];
		const max = (state && state.webhook_max) || 32;
		const rows = hooks.length ? hooks.map((id) => `
			<tr>
				<td><code>${escapeHtml(id)}</code></td>
				<td class="right"><button type="button" data-unpub="${escapeHtml(id)}">Unpublish</button></td>
			</tr>`).join("") : `<tr><td colspan="2" class="muted">Nothing published yet.</td></tr>`;

		viewEl.innerHTML = `
			<div class="card">
				<h2>Webhooks callable from the internet</h2>
				<p class="muted">A published webhook can be triggered from anywhere at your Vome address — no login, no Home Assistant UI forwarding. That is what makes it useful to a doorbell, a payment provider or an IFTTT action.</p>
				<p class="muted"><strong>Publish only webhooks you mean to expose.</strong> Home Assistant treats the webhook id itself as the password: anyone who knows it can trigger the automation. Nothing else about this Home Assistant is opened up, and webhooks you don't list stay unreachable.</p>
				<table class="routes">
					<thead><tr><th>Webhook id</th><th></th></tr></thead>
					<tbody>${rows}</tbody>
				</table>
				<p class="muted">${hooks.length} of ${max} published.</p>
			</div>
			<div class="card">
				<h2>Publish a webhook</h2>
				<p class="muted">Copy the id from your automation's webhook trigger (Settings → Automations → the trigger's webhook id).</p>
				<div class="row">
					<input type="text" id="hook-id" placeholder="webhook id" size="34">
					<button type="button" class="primary" id="publish-hook">Publish</button>
				</div>
			</div>`;

		document.getElementById("publish-hook").onclick = async () => {
			const value = document.getElementById("hook-id").value.trim();
			if (!value) { showBanner("Enter a webhook id first.", true); return; }
			try {
				state = await api("/api/webhooks/add", {
					method: "POST",
					body: JSON.stringify(withEntry({ webhook_id: value })),
				});
				showBanner(`Published ${value}.`);
				render();
			} catch (err) {
				showBanner(String(err.message || err), true);
			}
		};
		viewEl.querySelectorAll("[data-unpub]").forEach((btn) => {
			btn.onclick = async () => {
				try {
					state = await api("/api/webhooks/remove", {
						method: "POST",
						body: JSON.stringify(withEntry({ webhook_id: btn.dataset.unpub })),
					});
					showBanner("Unpublished — it can no longer be called from the internet.");
					render();
				} catch (err) {
					showBanner(String(err.message || err), true);
				}
			};
		});
	}

	function renderForward() {
		const on = !!(state && state.forward_ui);
		const localUrl = (state && state.local_url) || "";
		const override = (state && state.local_url_override) || "";
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
			</div>
			<div class="card">
				<h2>How Vome reaches Home Assistant</h2>
				<p class="muted">Everything Vome does — remote access, the assistant, LAN tunnels — goes through this address on your own machine. Vome currently uses <code>${escapeHtml(localUrl)}</code>. ${escapeHtml(localUrlNote())}</p>
				<div class="row">
					<input type="text" id="local-url" placeholder="${escapeHtml(localUrl)}" value="${escapeHtml(override)}" size="30">
					<button type="button" id="save-local-url">Save address</button>
				</div>
			</div>`;
		document.getElementById("save-local-url").onclick = async () => {
			try {
				const value = document.getElementById("local-url").value.trim();
				state = await api("/api/local_url", {
					method: "POST",
					body: JSON.stringify(withEntry({ local_url: value })),
				});
				showBanner(value
					? `Vome will reach Home Assistant at ${state.local_url}.`
					: "Back to detecting the address automatically.");
				render();
			} catch (err) {
				showBanner(String(err.message || err), true);
			}
		};
		document.getElementById("save-fwd").onclick = async () => {
			try {
				const enabled = document.getElementById("fwd").checked;
				state = await api("/api/forward_ui", {
					method: "POST",
					body: JSON.stringify(withEntry({ forward_ui: enabled })),
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
						body: JSON.stringify(withEntry({ slug: btn.dataset.remove })),
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
						body: JSON.stringify(withEntry({ slug })),
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
				input.focus();
				input.select();
				input.setSelectionRange(0, input.value.length);
				// The panel is served over http via ingress, where
				// navigator.clipboard is unavailable — try execCommand first
				// (works in an insecure context), fall back to the async API.
				let copied = false;
				try {
					copied = document.execCommand("copy");
				} catch (_err) {
					copied = false;
				}
				if (copied) {
					showBanner("Command copied to clipboard.");
				} else if (navigator.clipboard) {
					navigator.clipboard.writeText(input.value)
						.then(() => showBanner("Command copied to clipboard."))
						.catch(() => showBanner("Couldn't copy automatically — select the text and press Ctrl/Cmd+C.", true));
				} else {
					showBanner("Select the highlighted text and press Ctrl/Cmd+C to copy.", true);
				}
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
					body: JSON.stringify(withEntry(payload)),
				});
				showBanner(payload.scheme === "tcp"
					? `Added ${payload.slug} — now click “Get tunnel token” on it to connect.`
					: `Added /t/${payload.slug}/`);
				render();
			} catch (err) {
				btn.disabled = false;
				btn.textContent = "Add route";
				showBanner(String(err.message || err), true);
				// A bare 400 here means HA rejected the write pre-handler — run
				// diagnostics so the panel shows WHY (usually a stale running
				// version) instead of the opaque error.
				await runDiagnostics();
				render();
			}
		};
	}

	// In-app "Connect to Vome" device-authorisation flow (null when idle).
	let linkFlow = null;
	let linkTimer = null;

	function stopLinkPolling() {
		if (linkTimer) {
			clearTimeout(linkTimer);
			linkTimer = null;
		}
	}

	function linkDiagram() {
		return `
			<div class="tunnel-diagram">
				<svg viewBox="0 0 640 92" role="img" aria-label="This Home Assistant dials outward to Vome; nothing is opened on your router.">
					<g class="node"><rect x="8" y="24" width="150" height="48" rx="10"/><text x="83" y="45">You · vome.io</text><text x="83" y="61" class="sub">sign in here</text></g>
					<line class="flow" x1="158" y1="48" x2="238" y2="48"/><text x="198" y="39" class="lbl">https</text>
					<g class="node"><rect x="238" y="24" width="150" height="48" rx="10"/><text x="313" y="45">Vome relay</text><text x="313" y="61" class="sub">vome.io</text></g>
					<line class="flow" x1="388" y1="48" x2="468" y2="48"/><text x="428" y="39" class="lbl">dials out</text>
					<g class="node target"><rect x="468" y="24" width="164" height="48" rx="10"/><text x="550" y="45">Home Assistant</text><text x="550" y="61" class="sub">this box</text></g>
				</svg>
				<p class="muted small">Your Home Assistant makes the connection <em>outward</em> to Vome — no ports are opened on your router, and Vome never reaches in uninvited.</p>
			</div>`;
	}

	async function startLink() {
		try {
			const res = await api("/api/link/start", {
				method: "POST", body: JSON.stringify(withEntry({})),
			});
			if (res.status === "already_linked") {
				await refresh();
				return;
			}
			linkFlow = {
				userCode: res.user_code || "",
				uri: res.verification_uri || "https://vome.io/account/link-ha",
				interval: Math.max(3, Number(res.interval) || 5),
				message: "",
			};
			render();
			scheduleLinkPoll();
		} catch (err) {
			showBanner(String(err.message || err), true);
		}
	}

	function scheduleLinkPoll() {
		stopLinkPolling();
		if (linkFlow) {
			linkTimer = setTimeout(pollLink, linkFlow.interval * 1000);
		}
	}

	async function pollLink() {
		if (!linkFlow) return;
		try {
			const res = await api("/api/link/poll", {
				method: "POST", body: JSON.stringify(withEntry({})),
			});
			if (res.status === "linked") {
				linkFlow = null;
				stopLinkPolling();
				showBanner("Connected to Vome — this Home Assistant is now linked.");
				await refresh();
				return;
			}
			if (res.status === "expired" || res.status === "no_pending") {
				linkFlow = null;
				stopLinkPolling();
				showBanner("The code expired before it was approved — please start again.", true);
				render();
				return;
			}
			linkFlow.message = "";
			scheduleLinkPoll();
		} catch (err) {
			// Transient network hiccup: keep waiting, but note the reason.
			if (linkFlow) linkFlow.message = String(err.message || err);
			if (current === "link") render();
			scheduleLinkPoll();
		}
	}

	function cancelLink() {
		linkFlow = null;
		stopLinkPolling();
		render();
	}

	async function unlinkVome() {
		if (!window.confirm("Disconnect this Home Assistant from Vome? Remote access and LAN tunnels stop until you reconnect.")) {
			return;
		}
		try {
			await api("/api/link/unlink", {
				method: "POST", body: JSON.stringify(withEntry({})),
			});
			showBanner("Disconnected from Vome.");
			await refresh();
		} catch (err) {
			showBanner(String(err.message || err), true);
		}
	}

	// Switches (Vome's shareable/subscribable toggles) — separate from the
	// remote-access status payload, so fetched on demand rather than folded
	// into `state`. `null` means "not loaded yet"; `{}` means loaded + empty.
	let switchesData = null;

	async function loadSwitches() {
		try {
			const result = await api("/api/switches");
			switchesData = result.switches || {};
		} catch (err) {
			showBanner(String(err.message || err), true);
			switchesData = switchesData || {};
		}
		if (current === "switches") render();
	}

	function renderSwitches() {
		if (switchesData === null) {
			viewEl.innerHTML = `<div class="card"><p class="muted">Loading switches…</p></div>`;
			return;
		}
		const entries = Object.entries(switchesData);
		const rows = entries.map(([uid, sw]) => {
			const owner = !!sw.is_owner;
			return `
				<tr>
					<td><strong>${escapeHtml(sw.name || uid)}</strong><br><code class="mono small">${escapeHtml(uid)}</code></td>
					<td>${escapeHtml(sw.category || "Other")}</td>
					<td>${pill(!!sw.state, "on", "off")}</td>
					<td>${owner ? pill(true, "Owner", "") : pill(false, "", "Subscribed")}${sw.publicize ? ` ${pill(true, "Public", "")}` : ""}</td>
					<td>${owner
						? `<button type="button" class="danger" data-delete-switch="${escapeHtml(uid)}">Delete</button>`
						: `<button type="button" data-forget-switch="${escapeHtml(uid)}">Remove</button>`}</td>
				</tr>`;
		}).join("");
		viewEl.innerHTML = `
			<div class="card">
				<h2>Your switches</h2>
				<p class="muted">Shareable, subscribable toggles synced through Vome — create one others can subscribe to, or subscribe to one shared with you. Separate from the remote-access tunnels above; toggling a switch once it's here works the same as any other Home Assistant switch entity. <strong>Delete</strong> removes an owned switch for everyone subscribed to it; <strong>Remove</strong> just stops tracking a switch here (nothing changes for anyone else) — the same thing as deleting its device from Settings → Devices & Services.</p>
				${entries.length ? `<table><thead><tr><th>Switch</th><th>Category</th><th>State</th><th>Role</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="muted">No switches yet — create one or subscribe to an existing UID below.</p>`}
			</div>
			<div class="card">
				<h2>Create a switch</h2>
				<div class="row">
					<label class="field">Name<input id="sw-name" placeholder="Porch light status"></label>
					<label class="field">Category
						<select id="sw-category">
							<option>Other</option><option>Community</option><option>Personal</option><option>Event</option><option>Test</option>
						</select>
					</label>
				</div>
				<div class="row">
					<label class="toggle"><input type="checkbox" id="sw-publicize"> List publicly on sync.vome.io</label>
				</div>
				<details>
					<summary class="muted small">More options (description, location, link, images)</summary>
					<div class="row">
						<label class="field">Description<input id="sw-description" placeholder="optional"></label>
						<label class="field">Location<input id="sw-location" placeholder="city, optional"></label>
					</div>
					<div class="row">
						<label class="field">Link<input id="sw-link" placeholder="https://…"></label>
					</div>
					<div class="row">
						<label class="field">Icon URL<input id="sw-icon" placeholder="https://…"></label>
						<label class="field">Banner URL<input id="sw-banner" placeholder="https://…"></label>
					</div>
				</details>
				<div class="row">
					<button type="button" class="primary" id="sw-create">Create switch</button>
				</div>
			</div>
			<div class="card">
				<h2>Subscribe to a switch</h2>
				<p class="muted">Enter a UID someone shared with you to add it here.</p>
				<div class="row">
					<label class="field">Switch UID<input id="sw-sub-uid" placeholder="uid-…"></label>
					<button type="button" id="sw-subscribe">Subscribe</button>
				</div>
			</div>`;

		document.querySelectorAll("[data-delete-switch]").forEach((btn) => {
			btn.onclick = async () => {
				if (!confirm("Delete this switch? This can't be undone for anyone subscribed to it.")) return;
				try {
					await api("/api/switches/delete", {
						method: "POST", body: JSON.stringify(withEntry({ uid: btn.dataset.deleteSwitch })),
					});
					showBanner("Switch deleted.");
					switchesData = null;
					await loadSwitches();
				} catch (err) {
					showBanner(String(err.message || err), true);
				}
			};
		});

		document.querySelectorAll("[data-forget-switch]").forEach((btn) => {
			btn.onclick = async () => {
				try {
					await api("/api/switches/forget", {
						method: "POST", body: JSON.stringify(withEntry({ uid: btn.dataset.forgetSwitch })),
					});
					showBanner("Removed.");
					switchesData = null;
					await loadSwitches();
				} catch (err) {
					showBanner(String(err.message || err), true);
				}
			};
		});

		document.getElementById("sw-create").onclick = async () => {
			const name = document.getElementById("sw-name").value.trim();
			if (!name) {
				showBanner("Name is required.", true);
				return;
			}
			const payload = {
				name,
				category: document.getElementById("sw-category").value,
				publicize: document.getElementById("sw-publicize").checked,
				description: document.getElementById("sw-description").value.trim(),
				location: document.getElementById("sw-location").value.trim(),
				link: document.getElementById("sw-link").value.trim(),
				icon_url: document.getElementById("sw-icon").value.trim(),
				banner_url: document.getElementById("sw-banner").value.trim(),
			};
			try {
				const result = await api("/api/switches/create", {
					method: "POST", body: JSON.stringify(withEntry(payload)),
				});
				showBanner(`Switch created (${result.uid}).`);
				switchesData = null;
				await loadSwitches();
			} catch (err) {
				showBanner(String(err.message || err), true);
			}
		};

		document.getElementById("sw-subscribe").onclick = async () => {
			const uid = document.getElementById("sw-sub-uid").value.trim();
			if (!uid) {
				showBanner("Enter a switch UID.", true);
				return;
			}
			try {
				await api("/api/switches/subscribe", {
					method: "POST", body: JSON.stringify(withEntry({ uid })),
				});
				showBanner("Subscribed.");
				switchesData = null;
				await loadSwitches();
			} catch (err) {
				showBanner(String(err.message || err), true);
			}
		};
	}

	function renderLink() {
		if (state && state.linked) {
			viewEl.innerHTML = `
				<div class="card">
					<h2>Connected to Vome</h2>
					<div class="row">
						${pill(true, "Linked", "")}
						${state.server_id ? `<span class="mono">${escapeHtml(state.server_id)}</span>` : ""}
					</div>
					<p class="muted" style="margin-top:0.8rem">This Home Assistant is connected to your Vome account. Remote access and LAN tunnels are set up from the other tabs.</p>
					<div class="row"><button type="button" class="danger" id="unlink">Disconnect from Vome</button></div>
				</div>`;
			document.getElementById("unlink").onclick = unlinkVome;
			return;
		}
		if (!linkFlow) {
			viewEl.innerHTML = `
				<div class="card">
					<h2>Connect this Home Assistant to Vome</h2>
					<p class="muted">Linking lets you reach this home from vome.io — the Home Assistant UI, LAN devices and Remote Desktop — over one outbound, encrypted connection. It takes about a minute and opens no ports on your router.</p>
					${linkDiagram()}
					<div class="row"><button type="button" class="primary" id="link-start">Connect to Vome</button></div>
					<p class="muted small">You'll sign in to Vome in a new tab and approve a short code shown here.</p>
				</div>`;
			document.getElementById("link-start").onclick = startLink;
			return;
		}
		let host = linkFlow.uri;
		try { host = new URL(linkFlow.uri).host; } catch (_err) { /* keep full uri */ }
		viewEl.innerHTML = `
			<div class="card">
				<h2>Approve this Home Assistant</h2>
				<ol class="steps">
					<li>Open <a href="${escapeHtml(linkFlow.uri)}" target="_blank" rel="noreferrer">${escapeHtml(host)}</a> and sign in to Vome.</li>
					<li>Enter this code when asked:
						<div class="cmd-row"><input class="mono cmd" id="link-code" readonly value="${escapeHtml(linkFlow.userCode)}" onclick="this.select()"><button type="button" id="copy-code">Copy</button></div>
					</li>
					<li><span class="pill warn">Waiting for approval…</span> This page updates itself the moment you approve — leave it open.</li>
				</ol>
				${linkFlow.message ? `<p class="muted small">Still trying… (${escapeHtml(linkFlow.message)})</p>` : ""}
				<div class="row"><button type="button" id="link-cancel">Cancel</button></div>
			</div>`;
		document.getElementById("link-cancel").onclick = cancelLink;
		const cc = document.getElementById("copy-code");
		if (cc) {
			cc.onclick = () => {
				const i = document.getElementById("link-code");
				i.focus();
				i.select();
				try {
					if (document.execCommand("copy")) showBanner("Code copied to clipboard.");
				} catch (_err) { /* selection is enough */ }
			};
		}
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
				<div class="row"><button type="button" id="run-diag">Run diagnostics</button></div>
			</div>`;
		document.getElementById("run-diag").onclick = async () => {
			await runDiagnostics();
			render();
			showBanner(lastDiag ? "Diagnostics complete — see the card above." : "Diagnostics endpoint unreachable (update the app to 0.3.1+).", !lastDiag);
		};
	}

	function render() {
		if (current === "overview") renderOverview();
		else if (current === "forward") renderForward();
		else if (current === "lan") renderLan();
		else if (current === "webhooks") renderWebhooks();
		else if (current === "link") renderLink();
		else if (current === "switches") renderSwitches();
		else renderAbout();
		if (lastDiag) {
			viewEl.insertAdjacentHTML("afterbegin", diagCard());
		}
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
