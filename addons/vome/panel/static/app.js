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

	async function api(path, opts) {
		const res = await fetch(path, {
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
			throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
		}
		return data;
	}

	async function refresh() {
		showBanner("");
		try {
			state = await api("/api/status");
			render();
		} catch (err) {
			showBanner(String(err.message || err), true);
			state = state || { linked: false, lan_routes: [], forward_ui: false };
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
		viewEl.innerHTML = `
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
				<h2>Quick paths</h2>
				<p class="muted">After a friendly domain is active on vome.io:</p>
				<ul class="muted">
					<li>Home Assistant → open the domain root (requires HA UI forwarding)</li>
					<li>LAN device → <code>/t/&lt;slug&gt;/</code> on that domain</li>
				</ul>
			</div>`;
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

	function renderLan() {
		const routes = (state && state.lan_routes) || [];
		const rows = routes.map((r) => `
			<tr>
				<td><strong>${escapeHtml(r.name || r.slug)}</strong><br><code>/t/${escapeHtml(r.slug)}/</code></td>
				<td class="mono">${escapeHtml(r.scheme)}://${escapeHtml(r.host)}:${r.port}</td>
				<td>${r.enabled === false ? pill(false, "", "off") : pill(true, "on", "")}</td>
				<td><button type="button" class="danger" data-remove="${escapeHtml(r.slug)}">Remove</button></td>
			</tr>`).join("");
		viewEl.innerHTML = `
			<div class="card">
				<h2>Configured tunnels</h2>
				<p class="muted">Each route is reachable as <code>/t/&lt;slug&gt;/</code> on your friendly domain after Vome sign-in.</p>
				${routes.length ? `<table><thead><tr><th>Route</th><th>Target</th><th>State</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : `<p class="muted">No LAN routes yet.</p>`}
			</div>
			<div class="card">
				<h2>Add a tunnel</h2>
				<div class="row">
					<label class="field">Slug<input id="slug" placeholder="nas"></label>
					<label class="field">Name<input id="name" placeholder="NAS"></label>
					<label class="field">Host<input id="host" placeholder="192.168.1.5"></label>
					<label class="field">Port<input id="port" type="number" value="80" min="1" max="65535"></label>
					<label class="field">Scheme
						<select id="scheme"><option>http</option><option>https</option></select>
					</label>
				</div>
				<div class="row">
					<label class="toggle"><input type="checkbox" id="enabled" checked> Enabled</label>
					<label class="toggle"><input type="checkbox" id="websocket" checked> WebSocket</label>
					<button type="button" class="primary" id="add-route">Add route</button>
				</div>
			</div>`;
		document.querySelectorAll("[data-remove]").forEach((btn) => {
			btn.onclick = async () => {
				try {
					state = await api("/api/lan_routes/remove", {
						method: "POST",
						body: JSON.stringify({ slug: btn.dataset.remove }),
					});
					showBanner(`Removed /t/${btn.dataset.remove}/`);
					render();
				} catch (err) {
					showBanner(String(err.message || err), true);
				}
			};
		});
		document.getElementById("add-route").onclick = async () => {
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
				state = await api("/api/lan_routes/add", {
					method: "POST",
					body: JSON.stringify(payload),
				});
				showBanner(`Added /t/${payload.slug}/`);
				render();
			} catch (err) {
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
		viewEl.innerHTML = `
			<div class="card">
				<h2>One codebase</h2>
				<p class="muted">This add-on installs the same <code>custom_components/vomesync</code> tree HACS uses. Switches and the relay work either way; the add-on adds this control panel and is where heavier companions (browser RDP, …) will live.</p>
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
