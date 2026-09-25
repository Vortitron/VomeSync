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
		health: "Health score",
		agent: "Coding agent",
		standby: "Standby sync",
		switches: "Switches",
		about: "About",
	};

	function showBanner(msg, kind) {
		bannerEl.textContent = msg;
		bannerEl.classList.toggle("hidden", !msg);
		const err = kind === true || kind === "err";
		bannerEl.classList.toggle("err", err);
		bannerEl.classList.toggle("info", kind === "info" && !err);
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
			data = { error: res.ok ? "Invalid JSON from panel API" : `HTTP ${res.status}` };
		}
		if (!res.ok) {
			const msg = data.error || data.message || `HTTP ${res.status}`;
			const error = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
			error.data = data;
			throw error;
		}
		return data;
	}

	const WAITING_RESTART =
		"Vome is installed. Restart Home Assistant once (Settings → System → ⋮ → Restart) so it can load — this page continues on its own afterwards.";
	const WAITING_HA =
		"Home Assistant is starting. This page continues automatically — no need to click Refresh.";

	const MULTI_ENTRY_MSG =
		"This Home Assistant has more than one Vome integration — often from installing via HACS and adding it again. Keep one and delete the spare under Settings → Devices & Services, then connect here.";

	function classifyHaError(err) {
		const msg = String(err.message || err);
		if (/Multiple Vome entries|Multiple linked entries|pass entry_id/i.test(msg)) {
			return { waiting: false, info: true, message: MULTI_ENTRY_MSG };
		}
		if (/502|503|unreachable|Invalid JSON|Failed to fetch|NetworkError|Load failed|Network request failed/i.test(msg)) {
			return { waiting: true, message: WAITING_HA };
		}
		if (/^400\b|Bad Request/i.test(msg)) {
			return { waiting: true, message: WAITING_RESTART };
		}
		return { waiting: false, message: msg };
	}

	function reportError(err) {
		const kind = classifyHaError(err);
		showBanner(kind.message, (kind.waiting || kind.info) ? "info" : "err");
		if (kind.waiting) {
			waitingForHa = true;
			schedulePoll();
			render();
		}
	}

	function restartNeeded() {
		if (!state || !state.installed_version) return false;
		// Old running code predates the version field entirely — if the disk
		// has a version but the running integration reports none, it's stale.
		if (!state.integration_version) return true;
		return state.integration_version !== state.installed_version;
	}

	function haNotReady() {
		return waitingForHa || restartNeeded();
	}

	// Relay / Vome Home link — not the leftover VomeSync switch-sync entries.
	function vomeHomeLinked() {
		return !!(state && state.linked);
	}

	// A house with more than one Vome home (or one moved between relay and
	// hosted) needs to see *which* one this panel is talking about, not just
	// that a link exists — otherwise "linked" and "forwarding" pills read as
	// generic reassurance rather than a specific, checkable fact.
	function vomeIdentityLine() {
		if (!vomeHomeLinked()) return "";
		const portal = ((state && (state.portal_url || state.default_portal_url)) || "https://vome.io").replace(/\/$/, "");
		const serverId = (state && state.server_id) || "";
		const forwardUrl = (state && state.forward_url) || "";
		const openHref = forwardUrl || (serverId ? `${portal}/servers/${encodeURIComponent(serverId)}` : portal);
		const label = forwardUrl ? forwardUrl.replace(/^https?:\/\//, "").replace(/\/$/, "") : (serverId || "your Vome account");
		// One plain link rather than a code chip glued to prose — a bare
		// server id or friendly domain reads fine on its own, it does not
		// need a monospace box drawn around it to be legible.
		return `<p class="muted" style="margin-top:0.5rem"><a class="link" href="${escapeHtml(openHref)}" target="_blank" rel="noopener">Open ${escapeHtml(label)} in Vome ›</a></p>`;
	}

	function syncChrome() {
		const linked = vomeHomeLinked();
		const header = document.getElementById("header-connect");
		if (header) header.classList.toggle("hidden", linked);
		const nav = document.querySelector(".tree-item[data-view=\"link\"]");
		if (nav) nav.classList.toggle("needs-action", !linked);
		const label = document.getElementById("nav-link-label");
		if (label) label.textContent = linked ? "Vome account" : "Connect to Vome";
		titles.link = linked ? "Vome account" : "Connect to Vome";
		if (current === "link" && titleEl) titleEl.textContent = titles.link;
	}

	// Echo the shown entry's id back on writes so they target the same Home
	// Assistant even when several Vome integrations are linked (which would
	// otherwise make the write ambiguous and fail).
	function withEntry(obj) {
		const id = state && state.entry_id;
		return id ? { ...obj, entry_id: id } : obj;
	}

	// Filled only from About → Run diagnostics. Status failures used to dump
	// this on every page (HTTP 502, version mismatch, service lists) which
	// made a normal first-load look broken. Keep it opt-in.
	let lastDiag = null;
	let waitingForHa = false;
	let pollTimer = null;
	let pollAttempts = 0;
	let refreshInFlight = false;
	const POLL_MS = 4000;
	const POLL_MAX = 90;

	function stopPolling() {
		if (pollTimer) {
			clearTimeout(pollTimer);
			pollTimer = null;
		}
	}

	function schedulePoll() {
		stopPolling();
		if (pollAttempts >= POLL_MAX) return;
		pollTimer = setTimeout(() => {
			pollAttempts += 1;
			refresh({ quiet: true });
		}, POLL_MS);
	}

	function diagVerdict(d) {
		if (!d.config_mounted) {
			return "The app cannot see Home Assistant's config folder, so the integration can never be installed from here. Update the Vome app (0.3.1 fixed the folder mapping), then restart Home Assistant.";
		}
		if (!d.integration_on_disk) {
			return "The integration isn't installed on disk. Restart the Vome app (it installs on every start), then check the app's Log tab for install errors.";
		}
		if (!d.vomesync_services || d.vomesync_services.length === 0) {
			return `Vome ${d.installed_version || "?"} is on disk but Home Assistant has not loaded it yet. Restart Home Assistant once; this page continues automatically.`;
		}
		if (!d.vomesync_services.includes("mint_lan_tcp_token")) {
			return `Home Assistant is running an OLD Vome integration (it lacks the tunnel services). Version ${d.installed_version || "?"} is installed on disk — restart Home Assistant to load it.`;
		}
		// Disk vs running mismatch is the decisive tell: writes fail because HA
		// is executing stale code even though the new files are on disk.
		if (d.installed_version && d.running_version !== d.installed_version) {
			return `Home Assistant is still running Vome ${d.running_version || "an older build"}; ${d.installed_version} is on disk. Restart Home Assistant once to finish the update.`;
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
			<div class="card info-card">
				<h2>Diagnostics</h2>
				<p class="muted"><strong>${escapeHtml(diagVerdict(d))}</strong></p>
				<details>
					<summary>Technical details</summary>
					<p class="muted small mono">running in HA: ${escapeHtml(d.running_version || "unknown (old build)")} · on disk: ${d.integration_on_disk ? (d.installed_version || "yes") : "NO"} · app bundles: ${escapeHtml(d.bundled_version || "?")} · HA ${escapeHtml(d.ha_version || "?")}<br>config mount: ${escapeHtml(String(d.config_root))} · Core API: ${escapeHtml(String(d.core_api))}<br>write probe: HTTP ${escapeHtml(String(d.write_probe_status))} — ${escapeHtml(String(d.write_probe_body || ""))}<br>vomesync services loaded: ${escapeHtml(svc)}</p>
				</details>
			</div>`;
	}

	async function runDiagnostics() {
		try {
			lastDiag = await api("api/diag");
		} catch (_err) {
			lastDiag = null;
		}
	}

	async function refresh(opts) {
		const quiet = !!(opts && opts.quiet);
		if (refreshInFlight) return;
		refreshInFlight = true;
		if (!quiet) showBanner("");
		try {
			state = await api("/api/status");
			waitingForHa = false;
			pollAttempts = 0;
			if (restartNeeded()) {
				showBanner(WAITING_RESTART, "info");
				schedulePoll();
			} else {
				lastDiag = null;
				stopPolling();
				if (state.warning) {
					const multi = /more than one Vome/i.test(state.warning);
					showBanner(state.warning, multi ? "info" : true);
				} else showBanner("");
			}
			render();
		} catch (err) {
			const kind = classifyHaError(err);
			waitingForHa = kind.waiting;
			showBanner(kind.message, (kind.waiting || kind.info) ? "info" : "err");
			state = state || { linked: false, lan_routes: [], forward_ui: false };
			if (err.data && typeof err.data === "object") {
				state.installed_version = err.data.installed_version || state.installed_version;
			}
			if (kind.waiting) schedulePoll();
			else stopPolling();
			render();
		} finally {
			refreshInFlight = false;
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
		if (name === "health" && healthData === null) loadHealth(true);
		if (name === "agent" && agentData === null) loadAgentKey(true);
		if (name === "standby") loadChap();
		render();
	}

	function pill(ok, onLabel, offLabel) {
		return `<span class="pill ${ok ? "ok" : "off"}">${ok ? onLabel : offLabel}</span>`;
	}

	// Neither of these failures announces itself — the relay stays connected
	// and the status stays green while things quietly break at the edges — so
	// surface them here, where someone is already looking for an explanation.
	function connectionWarningCard() {
		if (!state || !state.linked) return "";
		const ext = state.external_url || {};
		if (ext.ok === false) {
			// Offer the fix, but show the manual route too: this is Home
			// Assistant's own setting and it affects more than Vome, so nobody
			// should have to take our word for what the button did.
			const target = ext.expected || "";
			return `
			<div class="card warn-card">
				<h2>Home Assistant doesn't know its own address</h2>
				<p class="muted">${escapeHtml(ext.hint)}</p>
				<p class="muted">Set <strong>External URL</strong> under Settings → System → Network${
					target ? ` to <code>${escapeHtml(target)}</code>` : ""
				}, or let Vome do it:</p>
				<div class="row">
					<button id="fix-external-url" class="btn"${target ? "" : " disabled"}>
						${target ? `Set it to ${escapeHtml(target)}` : "Waiting for a remote visit"}
					</button>
				</div>
				${target ? "" : `<p class="muted">Open Home Assistant on your Vome address once so we can see which name you reach it on.</p>`}
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

	function extraEntriesCard() {
		const entries = (state && state.vome_entries) || [];
		if (entries.length < 2) return "";
		const disabled = entries.filter((e) => e.disabled);
		const enabled = entries.filter((e) => !e.disabled);
		const pool = enabled.length ? enabled : entries;
		const chosen = pool.find((e) => e.entry_id === (state && state.entry_id)) || pool[0];
		const names = pool.map((e) => e.title || "Vome").join(", ");
		const disabledNote = disabled.length
			? ` ${disabled.length} extra ${disabled.length === 1 ? "row is" : "rows are"} disabled and can be deleted.`
			: "";
		const leftover = "Those VomeSync rows are switch-sync leftovers — they are not a Vome Home remote-access link.";
		if (enabled.length <= 1 && disabled.length) {
			return `
			<div class="card info-card">
				<h2>Old VomeSync rows</h2>
				<p class="muted">${leftover}${disabledNote} Connect to Vome to attach this home. Delete the spares under Settings → Devices & Services when you are ready.</p>
			</div>`;
		}
		return `
			<div class="card info-card">
				<h2>More than one Vome integration</h2>
				<p class="muted">This Home Assistant has ${pool.length} VomeSync integrations (${escapeHtml(names)}). ${leftover} Connecting here uses <strong>${escapeHtml((chosen && chosen.title) || "Vome")}</strong>.${disabledNote} Remove the spare under Settings → Devices & Services so Devices and this panel don't issue different codes.</p>
			</div>`;
	}

	function renderOverview() {
		const routes = (state && state.lan_routes) || [];
		const enabled = routes.filter((r) => r.enabled !== false).length;
		const restartCard = haNotReady() ? `
			<div class="card info-card">
				<h2>${waitingForHa && !restartNeeded() ? "Waiting for Home Assistant" : "Restart Home Assistant once"}</h2>
				<p class="muted">${waitingForHa && !restartNeeded()
					? "Home Assistant is starting or still loading Vome. This page checks automatically and will continue when it is ready — you do not need to click Refresh."
					: `Vome ${escapeHtml((state && state.installed_version) || "")} is ready. Home Assistant only loads integrations at startup, so it needs one restart (Settings → System → ⋮ → Restart). This page continues on its own afterwards.`}</p>
			</div>` : "";
		// VomeSync config entries (switch sync) are not a Vome Home link.
		// Never hide this card because a restart is pending — that made Remote
		// Desktop look like the next step.
		const hideConnect = vomeHomeLinked();
		const linkCard = hideConnect ? "" : `
			<div class="card warn-card">
				<h2>Connect to Vome to get started</h2>
				<p class="muted">Link this Home Assistant to your Vome account. It takes about a minute, opens no ports, and is how remote access and LAN tunnels are turned on. Existing VomeSync rows in Devices & Services are leftover switch-sync installs, not this link.</p>
				<div class="row"><button type="button" class="primary" id="ov-connect">Connect to Vome</button></div>
			</div>`;
		viewEl.innerHTML = `${linkCard}${restartCard}${extraEntriesCard()}${connectionWarningCard()}
			<div class="card">
				<h2>Status</h2>
				<p class="muted">Same settings as the HACS options menu, laid out as a tree so remote access and LAN tunnels are easier to find.</p>
				<div class="row">
					${pill(vomeHomeLinked(), "Linked to Vome", "Not linked")}
					${pill(!!(state && state.forward_ui), (state && state.hosted) ? "HA UI forwarding on (hosted by Vome)" : "HA UI forwarding on", "HA UI forwarding off")}
					${pill(enabled > 0, `${enabled} LAN tunnel${enabled === 1 ? "" : "s"} on`, "No LAN tunnels")}
					${pill(!!(state && state.addon_marker), "Add-on install", "HACS-only install")}
				</div>
				${vomeIdentityLine()}
				${currentRemoteUrl() ? `<p class="muted" style="margin-top:0.5rem">Reachable at <code>${escapeHtml(currentRemoteUrl())}</code></p>` : ""}
				${(state && !state.forward_ui) ? `<p class="muted" style="margin-top:0.5rem">Full-UI forwarding is off, but a friendly domain still reaches devices — <a class="link" href="#" id="ov-device-urls">manage device URLs</a>.</p>` : ""}
			</div>
			<div class="card">
				<h2>Quick actions</h2>
				<div class="row">
					${hideConnect ? "" : `<button type="button" class="primary" id="qa-connect">Connect to Vome</button>`}
					<button type="button" id="qa-remote">Get a remote address</button>
					<button type="button" id="qa-health">Check my health score</button>
					<button type="button" id="qa-rdp">Set up Remote Desktop</button>
					<button type="button" id="qa-lan">LAN tunnels</button>
					<button type="button" id="qa-forward">Home Assistant UI</button>
				</div>
				<p class="muted" style="margin-top:0.8rem">After a friendly domain is active on vome.io: Home Assistant opens at the domain root (needs UI forwarding on), LAN web devices at <code>/t/&lt;slug&gt;/</code>, and Remote Desktop through a tunnel token.</p>
			</div>`;
		const deviceUrlsLink = document.getElementById("ov-device-urls");
		if (deviceUrlsLink) deviceUrlsLink.onclick = (e) => { e.preventDefault(); setView("lan"); };
		document.getElementById("qa-health").onclick = () => setView("health");
		const qaRemote = document.getElementById("qa-remote");
		if (qaRemote) qaRemote.onclick = () => setView("health");
		document.getElementById("qa-lan").onclick = () => setView("lan");
		document.getElementById("qa-forward").onclick = () => setView("forward");
		document.getElementById("qa-rdp").onclick = () => {
			setView("lan");
			const preset = document.getElementById("preset-rdp");
			if (preset) preset.click();
		};
		const goConnect = () => setView("link");
		const connectBtn = document.getElementById("ov-connect");
		if (connectBtn) connectBtn.onclick = goConnect;
		const qaConnect = document.getElementById("qa-connect");
		if (qaConnect) qaConnect.onclick = goConnect;
		// Only present while the external-URL warning is showing.
		const fixExternal = document.getElementById("fix-external-url");
		if (fixExternal) fixExternal.onclick = async () => {
			fixExternal.disabled = true;
			try {
				state = await api("/api/external_url", {
					method: "POST",
					body: JSON.stringify(withEntry({ external_url: "" })),
				});
				showBanner(`Home Assistant now calls itself ${(state.external_url || {}).current || ""}.`);
				render();
			} catch (err) {
				fixExternal.disabled = false;
				reportError(err);
			}
		};
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
				reportError(err);
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
					reportError(err);
				}
			};
		});
	}

	function renderForward() {
		const hosted = !!(state && state.hosted);
		const on = !!(state && state.forward_ui);
		const localUrl = (state && state.local_url) || "";
		const override = (state && state.local_url_override) || "";
		// A hosted VM has no relay tunnel for this flag to gate — the
		// friendly domain always shows the HA UI, so the toggle would be
		// inert. Explain that instead of offering a control that does
		// nothing when saved.
		const forwardCard = hosted ? `
			<div class="card info-card">
				<h2>Full-UI forwarding</h2>
				<p class="muted">This Home Assistant is hosted by Vome, so its friendly domain always shows the full HA UI — there is no relay tunnel here to gate, and nothing to switch off.</p>
			</div>` : `
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
		viewEl.innerHTML = `
			${forwardCard}
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
				reportError(err);
			}
		};
		const saveFwd = document.getElementById("save-fwd");
		if (saveFwd) saveFwd.onclick = async () => {
			try {
				const enabled = document.getElementById("fwd").checked;
				state = await api("/api/forward_ui", {
					method: "POST",
					body: JSON.stringify(withEntry({ forward_ui: enabled })),
				});
				showBanner(enabled ? "Full-UI forwarding enabled." : "Full-UI forwarding disabled.");
				render();
			} catch (err) {
				reportError(err);
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
					reportError(err);
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
					reportError(err);
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
				reportError(err);
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
		const host = portalHostFromUrl(configuredPortalUrl());
		const safe = escapeHtml(host);
		return `
			<ol class="steps">
				<li>This Home Assistant asks <strong data-portal-host>${safe}</strong> for a short code.</li>
				<li>You sign in on that site (new tab) and type the code to approve <em>this</em> home.</li>
				<li>Home Assistant then <strong>dials out</strong> to Vome and keeps that link — nothing is opened on your router.</li>
			</ol>
			<div class="tunnel-diagram">
				<svg viewBox="0 0 640 100" role="img" aria-label="You approve on the Vome site; this Home Assistant dials out. No inbound ports.">
					<g class="node"><rect x="8" y="24" width="150" height="52" rx="10"/><text x="83" y="46">You</text><text x="83" y="62" class="sub">sign in &amp; approve</text></g>
					<line class="flow" x1="158" y1="50" x2="230" y2="50"/><text x="194" y="40" class="lbl">code</text>
					<g class="node"><rect x="230" y="24" width="180" height="52" rx="10"/><text x="320" y="46">Vome account</text><text x="320" y="62" class="sub" data-portal-host>${safe}</text></g>
					<line class="flow" x1="500" y1="50" x2="410" y2="50"/><text x="455" y="40" class="lbl">dials out</text>
					<g class="node target"><rect x="500" y="24" width="132" height="52" rx="10"/><text x="566" y="46">This HA</text><text x="566" y="62" class="sub">no open ports</text></g>
				</svg>
			</div>`;
	}

	const DEFAULT_PORTAL = "https://vome.io";

	function configuredPortalUrl() {
		return (state && state.addon_portal_url) || DEFAULT_PORTAL;
	}

	function portalHostFromUrl(raw) {
		try {
			const value = (raw || "").trim();
			return new URL(value.includes("://") ? value : "https://" + value).host || "vome.io";
		} catch (_err) {
			return "vome.io";
		}
	}

	async function startLink() {
		const btn = document.getElementById("link-start");
		if (btn) btn.disabled = true;
		try {
			const portalUrl = configuredPortalUrl();
			const res = await api("/api/link/start", {
				method: "POST", body: JSON.stringify(withEntry({ portal_url: portalUrl })),
			});
			if (res.status === "already_linked") {
				await refresh();
				return;
			}
			const uri = res.verification_uri || (portalUrl.replace(/\/$/, "") + "/account/link-ha");
			const expectedHost = portalHostFromUrl(portalUrl);
			const gotHost = portalHostFromUrl(uri);
			if (expectedHost && gotHost && expectedHost !== gotHost) {
				throw new Error(
					"Home Assistant asked for a code from " + gotHost + ", not " + expectedHost +
					". Restart Home Assistant, then try Connect again."
				);
			}
			linkFlow = {
				userCode: res.user_code || "",
				uri: uri,
				interval: Math.max(3, Number(res.interval) || 5),
				message: "",
			};
			render();
			scheduleLinkPoll();
		} catch (err) {
			reportError(err);
			if (btn) btn.disabled = false;
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
			reportError(err);
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
			reportError(err);
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
							<option>Other</option><option>Community</option><option>Personal</option><option>Event</option><option>Transport</option><option>Government</option><option>Holiday</option><option>Weather</option><option>IsUp</option><option>Test</option>
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
					reportError(err);
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
					reportError(err);
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
				reportError(err);
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
				reportError(err);
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
			const waitNote = haNotReady() ? `
				<div class="card info-card">
					<h2>${waitingForHa && !restartNeeded() ? "Waiting for Home Assistant" : "Restart Home Assistant once"}</h2>
					<p class="muted">${waitingForHa && !restartNeeded() ? WAITING_HA : WAITING_RESTART} You can still start connecting; if it fails, finish the restart and try again.</p>
				</div>` : "";
			const host = escapeHtml(portalHostFromUrl(configuredPortalUrl()));
			viewEl.innerHTML = `${waitNote}${extraEntriesCard()}
				<div class="card">
					<h2>Connect this Home Assistant to Vome</h2>
					<p class="muted">This is how remote access starts: you approve <em>this</em> home in your Vome account, then Home Assistant keeps an outbound connection. About a minute; no router ports.</p>
					${linkDiagram()}
					<p class="muted small">Connecting to <code>${host}</code>. A different site is set on this add-on's Configuration tab.</p>
					<div class="row"><button type="button" class="primary" id="link-start">Connect to Vome</button></div>
					<p class="muted small">You'll sign in to Vome in a new tab and approve a short code shown here.</p>
				</div>`;
			document.getElementById("link-start").onclick = startLink;
			return;
		}
		let host = linkFlow.uri;
		try { host = new URL(linkFlow.uri).host; } catch (_err) { /* keep full uri */ }
		// Carrying the code in the link means clicking it is the whole flow:
		// sign in if needed, then Vome submits this code itself — no coming
		// back here to paste it. The code stays shown too, for a link opened
		// on a different device than the one that will type it.
		let linkUrl = linkFlow.uri;
		if (linkFlow.userCode) {
			try {
				const u = new URL(linkFlow.uri);
				u.searchParams.set("code", linkFlow.userCode);
				linkUrl = u.toString();
			} catch (_err) { /* keep bare uri */ }
		}
		viewEl.innerHTML = `
			<div class="card">
				<h2>Approve this Home Assistant</h2>
				<ol class="steps">
					<li>Open <a href="${escapeHtml(linkUrl)}" target="_blank" rel="noreferrer">${escapeHtml(host)}</a> and sign in to Vome — it links this code automatically.
						<div class="muted small">Nothing opened? (The Home Assistant app often will not open a new tab.) Copy this address into any browser:</div>
						<div class="cmd-row"><input class="mono cmd" id="link-url" readonly value="${escapeHtml(linkUrl)}" onclick="this.select()"><button type="button" id="copy-url">Copy</button></div>
					</li>
					<li>On a different device? Enter this code by hand instead:
						<div class="cmd-row"><input class="mono cmd" id="link-code" readonly value="${escapeHtml(linkFlow.userCode)}" onclick="this.select()"><button type="button" id="copy-code">Copy</button></div>
					</li>
					<li><span class="pill warn">Waiting for approval…</span> This page updates itself the moment you approve — leave it open.</li>
				</ol>
				<p class="muted small">This code only works on <code>${escapeHtml(host)}</code> — not on a different Vome site.</p>
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
const cu = document.getElementById("copy-url");
		if (cu) {
			cu.onclick = () => {
				const i = document.getElementById("link-url");
				i.focus();
				i.select();
				try {
					if (document.execCommand("copy")) showBanner("Address copied to clipboard.");
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
				Integration installed on disk: <code>${escapeHtml(installed)}</code><br>
				This panel: <code>${escapeHtml((state && state.addon_version) || "unknown")}</code><br>
				Vome site: <code>${escapeHtml((state && state.addon_portal_url) || "https://vome.io")}</code></p>
				${running !== installed ? `<p class="muted">Home Assistant will load the installed version after one restart. This page continues automatically once that happens.</p>` : ""}
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

	// ── Health score ────────────────────────────────────────────────────
	// The only things in this panel that work before the home is linked to
	// anything: a random web address, and a health check on the same tunnel.
	// That deal has a day-long clock on it, so every state that shows a
	// guest run also shows the clock — see health_score.py / guest_remote.py.

	let healthData = null;
	let healthBusy = false;

	async function loadHealth(quiet) {
		try {
			healthData = await api("/api/health_score");
		} catch (err) {
			if (!quiet) showBanner(err.message || "Could not read the health score", true);
			healthData = healthData || null;
		}
		if (current === "health") render();
	}

	function healthToneClass(score) {
		if (score === null || score === undefined) return "";
		if (score >= 80) return "ok";
		if (score >= 55) return "warn";
		return "bad";
	}

	function currentRemoteUrl() {
		return (healthData && healthData.remote_url)
			|| (state && state.remote_url)
			|| "";
	}

	function remoteAddressCard() {
		const url = currentRemoteUrl();
		if (url) {
			return `
			<div class="card">
				<h2>A web address, without a domain</h2>
				<p class="muted">This Home Assistant is reachable at</p>
				<p><code>${escapeHtml(url)}</code></p>
				<p class="muted">Point the Home Assistant app at it. Sign in to Home Assistant as you usually would — no router ports, no domain to buy.</p>
				<div class="row">
					<a class="btn primary" href="${escapeHtml(url)}" target="_blank" rel="noopener">Open</a>
				</div>
			</div>`;
		}
		return `
			<div class="card">
				<h2>Get a remote address</h2>
				<p class="muted">A private web address for this Home Assistant. No domain to buy, no router ports, no Vome account until you want to keep it. It lasts a day unless you sign in from the link that appears with it.</p>
				<div class="row">
					<button type="button" class="primary" id="remote-run"${healthBusy ? " disabled" : ""}>${healthBusy ? "Working…" : "Get a remote address"}</button>
				</div>
			</div>`;
	}

	function healthClockCard() {
		if (!healthData || healthData.saved_to_account !== false) return "";
		const url = healthData.keep_it_url || healthData.online_url || "";
		const secs = healthData.deleted_in_seconds || 0;
		const hours = Math.round(secs / 3600);
		const left = hours >= 20
			? "a day"
			: (hours >= 2 ? `about ${hours} hours` : `about ${Math.max(0, Math.round(secs / 60))} minutes`);
		return `
		<div class="card warn-card">
			<h2>This check is not saved</h2>
			<p class="muted">Vome deletes it — and the temporary link to this Home Assistant — in ${left} unless you sign in and keep it. The report stays here either way.</p>
			${url ? `<div class="row"><a class="btn primary" href="${escapeHtml(url)}" target="_blank" rel="noopener">Open it online and sign in</a></div>` : ""}
		</div>`;
	}

	// Links come from the health-score API (this house's own credentials),
	// not from the last /api/status payload. After a guest run the status
	// can still say "not linked" while the check already has a URL.
	function healthUrls() {
		const portal = (
			(healthData && healthData.portal_url)
			|| (state && (state.portal_url || state.default_portal_url))
			|| "https://vome.io"
		).replace(/\/$/, "");
		const serverId = (healthData && healthData.server_id) || (state && state.server_id) || "";
		const guest = !!(healthData && healthData.saved_to_account === false);
		const claim = (healthData && (healthData.keep_it_url || healthData.online_url)) || "";
		const health = (healthData && healthData.health_url) || (serverId && !guest ? `${portal}/servers/${encodeURIComponent(serverId)}/health` : "");
		const share = (healthData && healthData.share_url) || "";
		const card = (healthData && healthData.card_url) || (guest ? (claim || `${portal}/score`) : (health ? `${health}#score-card` : `${portal}/score`));
		const online = (healthData && healthData.online_url) || claim || share || health;
		return { portal, serverId, guest, claim, health, share, card, online };
	}

	function healthActionRow() {
		const report = (healthData && healthData.report) || null;
		if (!report) return "";
		const urls = healthUrls();
		const included = !!(healthData && healthData.card_included);
		const shareLabel = urls.share
			? "Open the shareable card"
			: (included ? "Publish this as a card — included" : "Publish or buy a shareable card");
		const shareHref = urls.share || urls.card || `${urls.portal}/score`;
		const doctorHref = urls.health || urls.claim || urls.online || urls.portal;
		// Always resolves to something, down to the bare portal URL — this is
		// the funnel into a Vome account, so it must never quietly disappear.
		const openHref = urls.online || urls.health || urls.claim || urls.portal;
		const openLabel = urls.guest ? "Open in Vome — create your account" : "Open in Vome";
		return `
			<div class="row health-actions">
				<a class="btn primary" href="${escapeHtml(openHref)}" target="_blank" rel="noopener">${openLabel}</a>
				<a class="btn" href="${escapeHtml(doctorHref)}" target="_blank" rel="noopener">Ask the AI Doctor</a>
				<a class="btn" href="${escapeHtml(shareHref)}" target="_blank" rel="noopener">${shareLabel}</a>
			</div>
			<p class="muted" style="margin-top:0.5rem">${urls.guest ? "One click links this Home Assistant to a new Vome account and keeps this result — nothing is kept until you do. " : ""}A public card shows the number and three badges — never device names. ${included ? "Included with hosting and Connect." : "49 kr to publish, or free with hosting, Connect, or a voucher."} The check itself stays free.</p>`;
	}

	function healthFindingsCard() {
		const report = (healthData && healthData.report) || null;
		if (!report) return "";
		const urls = healthUrls();
		const doctorBase = urls.health || urls.claim || urls.online;
		// Each finding gets its own #finding-<id> anchor on the report page
		// (health_report.html scrolls to and highlights it) — without this
		// every "Ask the AI Doctor about this" link was identical and just
		// dropped the visitor at the top of the report, not at the finding
		// they clicked from.
		const doctorFor = (findingId) => {
			if (!doctorBase) return "";
			return findingId ? `${doctorBase}#finding-${encodeURIComponent(findingId)}` : doctorBase;
		};
		// Critical/warn findings open by default — those are the ones worth
		// seeing without a tap. Everything else (advice/unknown/info) starts
		// collapsed so a long, healthy-ish report still reads as tidy.
		const rows = (report.findings || []).map((f) => {
			const openAttr = (f.severity === "critical" || f.severity === "warn") ? " open" : "";
			const doctor = doctorFor(f.id);
			return `
			<li>
				<details${openAttr}>
					<summary>
						<strong>${escapeHtml(f.title || "")}</strong>
						<span class="pill ${escapeHtml(f.severity || "info")}">${escapeHtml(f.severity || "info")}</span>
					</summary>
					<div class="muted">${escapeHtml(f.evidence || "")}</div>
					${f.recommendation ? `<div class="muted"><em>${escapeHtml(f.recommendation)}</em></div>` : ""}
					${doctor ? `<div><a class="link" href="${escapeHtml(doctor)}" target="_blank" rel="noopener">Ask the AI Doctor about this</a></div>` : ""}
				</details>
			</li>`;
		}).join("");
		const score = report.score;
		return `
		<div class="card">
			<h2>Score</h2>
			<p class="health-score ${healthToneClass(score)}">${score === null || score === undefined ? "—" : escapeHtml(String(score))}<span class="muted"> / 100</span></p>
			${report.summary ? `<p class="muted">${escapeHtml(report.summary)}</p>` : ""}
			${healthActionRow()}
			${rows ? `<ul class="health-findings">${rows}</ul>` : `<p class="muted">Nothing to report — that is the good outcome.</p>`}
		</div>`;
	}

	function renderHealth() {
		const report = (healthData && healthData.report) || null;
		const linked = vomeHomeLinked();
		viewEl.innerHTML = `
			${remoteAddressCard()}
			${healthClockCard()}
			<div class="card">
				<h2>Check this Home Assistant</h2>
				<p class="muted">Vome reads this instance and writes up what it finds — noisy sensors, a database that is growing, entities that stopped reporting, backups that stopped happening — and scores it out of 100.${linked ? "" : " You do not need a Vome account: this links itself temporarily, and nothing is kept unless you sign in afterwards."}</p>
				<p class="muted">Only the findings are sent to be written up — never your states, history, configuration or backups.</p>
				<div class="row">
					<button type="button" class="primary" id="health-run"${healthBusy ? " disabled" : ""}>${healthBusy ? "Checking…" : (report ? "Check again" : "Check my health score")}</button>
					<label class="inline"><input type="checkbox" id="health-ai" checked> Let the AI write it up</label>
				</div>
			</div>
			${healthFindingsCard()}
			${report ? "" : `<div class="card info-card"><h2>No check yet</h2><p class="muted">The first one takes a minute or two. The result appears here, on <code>sensor.vome_health_score</code>, and as a notification.</p></div>`}`;

		const run = document.getElementById("health-run");
		if (run) run.onclick = async () => {
			healthBusy = true;
			render();
			showBanner("Running the check — this takes a minute or two.", "info");
			try {
				const useAi = document.getElementById("health-ai");
				const started = await api("/api/health_score/run", {
					method: "POST",
					body: JSON.stringify(withEntry({ use_ai: !useAi || useAi.checked })),
				});
				if (started && started.claim_url) {
					healthData = Object.assign({}, healthData, {
						saved_to_account: false,
						keep_it_url: started.claim_url,
						online_url: started.claim_url,
						card_url: started.claim_url,
						remote_url: started.remote_url || (healthData && healthData.remote_url) || "",
						server_id: started.server_id || (healthData && healthData.server_id) || "",
						deleted_in_seconds: Math.max(
							0, (started.expires_at || 0) - Math.floor(Date.now() / 1000),
						),
					});
				}
				try {
					const next = await api("/api/status");
					state = next;
					syncChrome();
				} catch (_err) { /* keep the last status if this races HA */ }
				// The check runs at Vome's end; poll for the write-up rather
				// than holding this request open for two minutes.
				watchHealth();
			} catch (err) {
				showBanner(err.message || "Could not start the check", true);
			} finally {
				healthBusy = false;
				render();
			}
		};

		const remoteRun = document.getElementById("remote-run");
		if (remoteRun) remoteRun.onclick = async () => {
			healthBusy = true;
			render();
			showBanner("Asking Vome for an address…", "info");
			try {
				const started = await api("/api/remote_address", {
					method: "POST",
					body: JSON.stringify(withEntry({})),
				});
				healthData = Object.assign({}, healthData, {
					remote_url: (started && started.remote_url) || (healthData && healthData.remote_url) || "",
					keep_it_url: (started && started.claim_url) || (healthData && healthData.keep_it_url) || "",
					online_url: (started && started.claim_url) || (healthData && healthData.online_url) || "",
					saved_to_account: !(started && started.guest),
					server_id: (started && started.server_id) || (healthData && healthData.server_id) || "",
					deleted_in_seconds: started && started.expires_at
						? Math.max(0, started.expires_at - Math.floor(Date.now() / 1000))
						: (healthData && healthData.deleted_in_seconds) || 0,
				});
				try {
					const next = await api("/api/status");
					state = next;
					syncChrome();
				} catch (_err) { /* keep the last status if this races HA */ }
				if (started && started.status === "opened") watchHealth();
			} catch (err) {
				showBanner(err.message || "Could not get a remote address", true);
			} finally {
				healthBusy = false;
				render();
			}
		};
	}

	function watchHealth() {
		let attempts = 0;
		const tick = async () => {
			attempts += 1;
			await loadHealth(true);
			if (healthData && healthData.report) {
				showBanner("");
				return;
			}
			if (attempts < 40) window.setTimeout(tick, 15000);
			else showBanner("The check is taking longer than usual. It will appear here when it lands.", "info");
		};
		window.setTimeout(tick, 15000);
	}

	// ── Coding agent ────────────────────────────────────────────────────
	// The key is shown exactly once, here, and kept nowhere: Vome stores a
	// hash and the integration stores nothing, so there is no second place
	// to steal it from — and no way to re-show it, which is why Replace
	// key exists.
	let agentData = null;
	let agentBusy = false;
	let agentIssued = null;

	const AGENT_SCOPES = [
		{
			id: "ha:read",
			label: "See everything",
			detail: "Entities, states, history, logs, automations and the config check. Every other permission needs this one.",
		},
		{
			id: "ha:write",
			label: "Control devices",
			detail: "Call services — turn things on and off. Vome refuses locks, alarms, covers and other sensitive domains whatever the key says.",
		},
		{
			id: "ha:config",
			label: "Edit automations",
			detail: "Create, change and delete automations, scripts, scenes and dashboards.",
		},
		{
			id: "ha:files",
			label: "Read and write config files",
			detail: "Files under /config — including secrets.yaml, where this instance keeps its credentials. Off unless you turn it on.",
		},
	];

	async function loadAgentKey(quiet) {
		try {
			agentData = await api("/api/agent_key");
		} catch (err) {
			if (!quiet) showBanner(err.message || "Could not read the agent key", true);
			agentData = agentData || null;
		}
		if (current === "agent") render();
	}

	function agentCheckedScopes() {
		return AGENT_SCOPES
			.map((s) => s.id)
			.filter((id) => {
				const el = document.getElementById("scope-" + id.replace(":", "-"));
				return el && el.checked;
			});
	}

	function agentScopeList(selected) {
		const chosen = new Set(selected || []);
		return AGENT_SCOPES.map((scope) => {
			const id = "scope-" + scope.id.replace(":", "-");
			return `
				<label class="inline scope-row">
					<input type="checkbox" id="${id}" value="${scope.id}"${chosen.has(scope.id) ? " checked" : ""}>
					<span><strong>${escapeHtml(scope.label)}</strong> <code>${escapeHtml(scope.id)}</code><br>
					<span class="muted">${escapeHtml(scope.detail)}</span></span>
				</label>`;
		}).join("");
	}

	function agentClock(seconds) {
		const left = Math.max(0, Number(seconds) || 0);
		if (!left) return "expired";
		const hours = Math.floor(left / 3600);
		const mins = Math.floor((left % 3600) / 60);
		if (hours >= 1) return `${hours}h ${mins}m left`;
		return `${mins}m left`;
	}

	function agentKeyCard() {
		if (!agentIssued) return "";
		const json = agentIssued.mcp && agentIssued.mcp.json ? agentIssued.mcp.json : "";
		if (!json) return "";
		return `
			<div class="card">
				<h2>Your <code>mcp.json</code></h2>
				<p class="muted">Paste this into <code>~/.cursor/mcp.json</code>, VS Code's <code>mcp.json</code>, or your agent's MCP config, and restart it. Nothing to install.</p>
				<pre id="agent-json" class="pre-scroll">${escapeHtml(json)}</pre>
				<div class="row">
					<button type="button" class="primary" id="agent-copy">Copy</button>
				</div>
				<p class="muted"><strong>Shown once.</strong> Vome keeps only a hash of the key and this add-on keeps no copy at all. If you lose it, use Replace key — that keeps the same permissions and the same clock.</p>
			</div>`;
	}

	function agentIntro() {
		return `
			<div class="card">
				<h2>Work on this Home Assistant from your editor</h2>
				<p class="muted">Cursor, VS Code, Claude and anything else that speaks MCP can read this instance and change it — entity ids, live state, logs and automations — instead of you pasting them back and forth.</p>
				<p class="muted">The key below is <strong>not</strong> a Home Assistant token. It reaches this instance only, only through Vome, and only within the permissions you tick here, which Vome enforces at its end and writes to an audit log. Widening it means coming back to this page.</p>
			</div>`;
	}

	function renderAgent() {
		const data = agentData || {};
		const offer = data.offer || "issue";

		if (offer === "guest_link_first") {
			viewEl.innerHTML = `
				${agentIntro()}
				<div class="card info-card">
					<h2>Finish with the health score first</h2>
					<p class="muted">This Home Assistant is on a temporary link opened by the health check. Keep it by signing in from the link in the score, or let it run out, and then come back here for a key.</p>
					<div class="row"><button type="button" class="ghost" onclick="document.querySelector('[data-view=health]').click()">Go to the health score</button></div>
				</div>`;
			return;
		}

		if (offer === "linked_account") {
			const portal = escapeHtml(data.portal_url || "https://vome.io");
			viewEl.innerHTML = `
				${agentIntro()}
				<div class="card info-card">
					<h2>This Home Assistant has a Vome account</h2>
					<p class="muted">Keys for a linked home live on the account, so they survive, can be listed and can be revoked from anywhere. Mint one under Account → API tokens, ticking this instance.</p>
					<div class="row"><a class="btn ghost" href="${portal}/account/api-tokens" target="_blank" rel="noopener">Open API tokens</a></div>
				</div>`;
			return;
		}

		if (offer === "manage" && (data.active === false || !(data.seconds_left > 0))) {
			// The clock is Vome's, and it has run out. The key stopped
			// working the moment it did — nothing here can revive it — so
			// the only honest buttons are clearing up and starting again.
			viewEl.innerHTML = `
				${agentIntro()}
				<div class="card info-card">
					<h2>This key has expired <span class="pill off">two days up</span></h2>
					<p class="muted">It stopped working at Vome's end. Clearing it releases the temporary link and the throwaway account behind it, and you can issue another — or connect a Vome account, where keys have no clock.</p>
					<div class="row">
						<button type="button" class="primary" id="agent-revoke"${agentBusy ? " disabled" : ""}>Clear it and start again</button>
						<button type="button" class="ghost" id="agent-connect">Connect to Vome</button>
					</div>
				</div>`;

			const connectExpired = document.getElementById("agent-connect");
			if (connectExpired) connectExpired.onclick = () => document.querySelector("[data-view=link]").click();
			const clear = document.getElementById("agent-revoke");
			if (clear) clear.onclick = () => agentAction(
				"/api/agent_key/revoke", {}, "Clearing up…",
				() => {
					agentIssued = null;
					showBanner("Cleared. You can issue a new key.", "info");
					return null;
				},
			);
			return;
		}

		if (offer === "manage") {
			const scopes = data.scopes || [];
			const left = data.seconds_left || 0;
			viewEl.innerHTML = `
				${agentIntro()}
				${agentKeyCard()}
				<div class="card">
					<h2>Key active <span class="pill ${left > 3600 ? "ok" : "warn"}">${escapeHtml(agentClock(left))}</span></h2>
					<p class="muted">Connect a Vome account to keep it: the same key carries over and the clock comes off, so nothing has to be pasted again.</p>
					<div class="row"><button type="button" class="primary" id="agent-connect">Connect to Vome</button></div>
				</div>
				<div class="card">
					<h2>What it may do</h2>
					${agentScopeList(scopes)}
					<div class="row">
						<button type="button" class="primary" id="agent-save"${agentBusy ? " disabled" : ""}>Save permissions</button>
						<button type="button" class="ghost" id="agent-replace"${agentBusy ? " disabled" : ""}>Replace key</button>
					</div>
					<p class="muted">Saving does not change the key itself, so your <code>mcp.json</code> keeps working.</p>
				</div>
				<div class="card">
					<h2>Revoke</h2>
					<p class="muted">Ends the key, the temporary link it opened and the throwaway account behind it, now. This Home Assistant goes back to not being connected to anything.</p>
					<div class="row"><button type="button" class="danger" id="agent-revoke"${agentBusy ? " disabled" : ""}>Revoke the key</button></div>
				</div>`;

			const connect = document.getElementById("agent-connect");
			if (connect) connect.onclick = () => document.querySelector("[data-view=link]").click();

			const save = document.getElementById("agent-save");
			if (save) save.onclick = () => agentAction(
				"/api/agent_key/scopes",
				{ scopes: agentCheckedScopes() },
				"Saving…",
				(res) => { showBanner("Permissions saved. Your mcp.json is unchanged.", "info"); return res; },
			);

			const replace = document.getElementById("agent-replace");
			if (replace) replace.onclick = () => agentAction(
				"/api/agent_key/reissue", {}, "Issuing a new key…",
				(res) => {
					agentIssued = res;
					showBanner("New key issued. The old one has stopped working.", "info");
					return res;
				},
			);

			const revoke = document.getElementById("agent-revoke");
			if (revoke) revoke.onclick = () => agentAction(
				"/api/agent_key/revoke", {}, "Revoking…",
				() => {
					agentIssued = null;
					showBanner("Revoked. The key and the link are gone.", "info");
					return null;
				},
			);
			return;
		}

		viewEl.innerHTML = `
			${agentIntro()}
			${agentKeyCard()}
			<div class="card">
				<h2>What should it be allowed to do?</h2>
				${agentScopeList(data.default_scopes || ["ha:read", "ha:write", "ha:config"])}
				<div class="row">
					<button type="button" class="primary" id="agent-issue"${agentBusy ? " disabled" : ""}>${agentBusy ? "Issuing…" : "Issue a key"}</button>
				</div>
				<p class="muted">No account needed. The key covers this Home Assistant only and stops working after two days unless you connect a Vome account, which keeps it. Revoke is the only other button.</p>
			</div>`;

		const issue = document.getElementById("agent-issue");
		if (issue) issue.onclick = () => agentAction(
			"/api/agent_key/issue",
			{ scopes: agentCheckedScopes() },
			"Asking Vome for a key…",
			(res) => {
				agentIssued = res;
				showBanner("Key issued. Copy the JSON below — it is shown once.", "info");
				return res;
			},
		);
	}

	async function agentAction(path, body, busyMessage, onDone) {
		agentBusy = true;
		render();
		showBanner(busyMessage, "info");
		try {
			const res = await api(path, {
				method: "POST",
				body: JSON.stringify(withEntry(body)),
			});
			onDone(res);
			await loadAgentKey(true);
			try {
				state = await api("/api/status");
				syncChrome();
			} catch (_err) { /* keep the last status if this races HA */ }
		} catch (err) {
			showBanner(err.message || "That did not work", true);
		} finally {
			agentBusy = false;
			render();
			const pre = document.getElementById("agent-json");
			if (pre) pre.scrollIntoView({ block: "nearest" });
		}
	}

	// Clipboard access can be refused inside Home Assistant's ingress frame,
	// so falling back to selecting the text is not a nicety: without it the
	// button silently does nothing and the key is unreachable.
	function agentCopyJson() {
		const pre = document.getElementById("agent-json");
		if (!pre) return;
		const text = pre.textContent || "";
		const select = () => {
			const range = document.createRange();
			range.selectNodeContents(pre);
			const sel = window.getSelection();
			sel.removeAllRanges();
			sel.addRange(range);
			showBanner("Selected — press Ctrl/Cmd+C to copy.", "info");
		};
		if (!navigator.clipboard || !navigator.clipboard.writeText) {
			select();
			return;
		}
		navigator.clipboard.writeText(text).then(
			() => showBanner("Copied. Paste it into your agent's MCP config.", "info"),
			select,
		);
	}

	document.addEventListener("click", (ev) => {
		if (ev.target && ev.target.id === "agent-copy") agentCopyJson();
	});

	// ── Standby sync (CHAP) ─────────────────────────────────────────────
	// Pairing works with Home Assistant Core stopped — the add-on answers
	// this itself — because a standby spends most of its life that way.
	let chapData = null;
	let chapBusy = false;

	async function loadChap() {
		try {
			chapData = await api("/api/chap");
		} catch (err) {
			showBanner(err.message || "Could not read the standby sync state", true);
		}
		if (current === "standby") render();
	}

	function chapWhen(ts) {
		return ts ? new Date(ts * 1000).toLocaleString() : "never";
	}

	function renderStandby() {
		const d = chapData;
		if (!d) {
			viewEl.innerHTML = `<div class="card"><p class="muted">Loading…</p></div>`;
			return;
		}
		if (!d.installed) {
			viewEl.innerHTML = `
				<div class="card">
					<h2>Keep a hosted standby in step</h2>
					<p class="muted">With CHAP protection, Vome keeps a copy of this Home Assistant ready to take over if this machine fails.</p>
					<p>That part lives in a separate add-on, <strong>Vome CHAP</strong>, because it needs permission to stop and start Home Assistant and to make backups — which this add-on deliberately does not ask for.</p>
					<p class="muted">Install it from the same add-on store (Settings &rarr; Add-ons &rarr; Add-on store &rarr; Vome CHAP), start it, then come back here.</p>
				</div>`;
			return;
		}
		const status = d.paired
			? `<p>${pill(true, "Paired", "")} as <code>${escapeHtml(d.server_id)}</code> with <code>${escapeHtml(d.portal_url)}</code>.</p>
			   <p class="muted">Last sent to the standby: ${escapeHtml(chapWhen(d.uploaded_at))}. Last taken from it: ${escapeHtml(chapWhen(d.applied_at))}.</p>
			   <p class="muted">Latest: ${escapeHtml(d.last_outcome || "")}</p>
			   ${d.core_stopped_by_vome ? `<p><strong>Home Assistant is stopped here</strong> because the hosted standby is the active install. It starts again when you hand back.</p>` : ""}`
			: `<p>${pill(false, "", "Not paired")} ${d.pairing_failed ? `The last code was refused (${escapeHtml(d.pairing_failed)}) — it may have expired. Get a new one.` : ""}</p>`;
		viewEl.innerHTML = `
			<div class="card">
				<h2>Keep a hosted standby in step</h2>
				<p class="muted">With CHAP protection, Vome keeps a copy of this Home Assistant ready to take over if this machine fails. The Vome CHAP add-on sends your configuration to it whenever it changes, and brings back anything changed there when you hand back.</p>
				${status}
			</div>
			<div class="card">
				<h2>${d.paired ? "Pair again" : "Pair this install"}</h2>
				<p class="muted">Usually Vome pairs this for you ("Pair both installs" on your server's Standby sync page). To do it by hand, create a pairing code there and paste it here. It works once, for 30 minutes.</p>
				<div class="row">
					<input type="text" id="chap-code" placeholder="vcp_…" autocomplete="off" spellcheck="false" style="flex:1; min-width:16rem">
					<button type="button" class="primary" id="chap-pair"${chapBusy ? " disabled" : ""}>${chapBusy ? "Pairing…" : "Pair"}</button>
				</div>
			</div>`;
		const btn = document.getElementById("chap-pair");
		if (btn) btn.onclick = async () => {
			const code = (document.getElementById("chap-code").value || "").trim();
			if (!code) return;
			chapBusy = true;
			render();
			showBanner("Handing the code to the Vome CHAP add-on…", "info");
			try {
				await api("/api/chap/pair", { method: "POST", body: JSON.stringify({ code }) });
				// The CHAP add-on redeems it within a few seconds; look again.
				await new Promise((resolve) => setTimeout(resolve, 8000));
				await loadChap();
				showBanner(chapData && chapData.paired ? "Paired. This install now keeps its standby in step."
					: "Handed over — it should show as paired in a moment. Press Refresh.", "info");
			} catch (err) {
				showBanner(err.message || "Pairing did not work", true);
			} finally {
				chapBusy = false;
				render();
			}
		};
	}

	function render() {
		if (current === "overview") renderOverview();
		else if (current === "forward") renderForward();
		else if (current === "lan") renderLan();
		else if (current === "webhooks") renderWebhooks();
		else if (current === "link") renderLink();
		else if (current === "health") renderHealth();
		else if (current === "agent") renderAgent();
		else if (current === "standby") renderStandby();
		else if (current === "switches") renderSwitches();
		else renderAbout();
		if (lastDiag && current === "about") {
			viewEl.insertAdjacentHTML("afterbegin", diagCard());
		}
		syncChrome();
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
	document.getElementById("btn-refresh").addEventListener("click", () => {
		pollAttempts = 0;
		refresh();
	});
	document.addEventListener("visibilitychange", () => {
		if (document.visibilityState === "visible" && haNotReady()) {
			pollAttempts = 0;
			refresh({ quiet: true });
		}
	});
	window.addEventListener("pageshow", () => {
		if (haNotReady()) {
			pollAttempts = 0;
			refresh({ quiet: true });
		}
	});
	refresh();
})();
