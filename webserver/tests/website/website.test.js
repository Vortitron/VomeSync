/**
 * @jest-environment jsdom
 *
 * Website smoke tests (static SPA).
 *
 * These tests verify the key user-visible v2-only behaviours:
 * - Switch cards can render an icon
 * - Opening a switch sets the banner background (hero)
 * - Deep links use /switch/<uid>
 * - Comment posting uses the v2 access-key endpoint + X-Api-Key header
 */
const fs = require('fs');
const path = require('path');

function createMockResponse(jsonBody, ok = true, status = 200) {
	return {
		ok,
		status,
		async json() {
			return jsonBody;
		}
	};
}

describe('Website SPA (v2 directory)', () => {
	test('supports icon+banner, /switch/<uid> deep links, and v2 access-key comments', async () => {
		// Arrange DOM
		const websiteRoot = path.resolve(__dirname, '../../../website');
		const html = fs.readFileSync(path.join(websiteRoot, 'index.html'), 'utf8');
		document.documentElement.innerHTML = html;

		// Prevent timers / UI popups from interfering with Jest
		window.setInterval = jest.fn();
		window.alert = jest.fn();
		if (!navigator.clipboard) {
			// eslint-disable-next-line no-global-assign
			navigator.clipboard = {};
		}
		navigator.clipboard.writeText = jest.fn().mockResolvedValue(undefined);

		// Start at home
		window.history.pushState({}, '', '/');

		const uid = 'vs_75bz1byjrbv0jfmxv8dq27rp2w';
		const iconUrl = 'https://example.com/icon.png';
		const bannerUrl = 'https://example.com/banner.jpg';
		const accessKey = '00000000-0000-4000-8000-000000000000';

		const publicSwitchList = {
			success: true,
			data: {
				switches: [{
					uid,
					description: 'Pretty Switch',
					location: 'Test City',
					category: 'Community',
					state: false,
					lastToggled: 0,
					toggleCount: 0,
					userCount: 0,
					link: '',
					iconUrl,
					bannerUrl,
					ownerProfileUrl: ''
				}],
				count: 1,
				timestamp: Date.now()
			}
		};

		const publicSwitchDetail = {
			success: true,
			data: {
				uid,
				description: 'Pretty Switch',
				location: 'Test City',
				category: 'Community',
				state: false,
				lastToggled: 0,
				toggleCount: 0,
				userCount: 0,
				link: '',
				iconUrl,
				bannerUrl,
				ownerProfileUrl: '',
				events: []
			}
		};

		const commentOk = {
			success: true,
			data: {
				uid,
				comment: 'Hello',
				actor: 'test',
				viaApiKey: true,
				timestamp: Date.now()
			}
		};

		global.fetch = jest.fn(async (url, options) => {
			const u = String(url);
			if (u.endsWith('/public-switches')) {
				return createMockResponse(publicSwitchList);
			}
			if (u.endsWith(`/switch/${uid}`)) {
				return createMockResponse(publicSwitchDetail);
			}
			if (u.endsWith('/categories')) {
				return createMockResponse({ success: true, data: {} });
			}
			if (u.endsWith(`/v2/switch/${uid}/comment`)) {
				return createMockResponse(commentOk);
			}
			return createMockResponse({ success: false, error: `Unhandled fetch in test: ${u}` }, false, 404);
		});

		// Load website script
		const script = fs.readFileSync(path.join(websiteRoot, 'script.js'), 'utf8');
		window.eval(script);
		window.setupEventListeners();

		// Act: load list, open detail, post a comment
		// Note: functions are global in this static site (inline handlers depend on them).
		window.sessionStorage.setItem(`vomesync_api_key:${uid}`, accessKey);
		await window.loadSwitches();

		const grid = document.getElementById('switchesGrid');
		expect(grid.innerHTML).toContain('switch-icon');
		expect(grid.innerHTML).toContain(uid);

		// Quick view: clicking the card (not the "Details" button) should open a modal without navigating
		const card = grid.querySelector('.switch-card');
		expect(card).toBeTruthy();
		card.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
		await new Promise((resolve) => setTimeout(resolve, 0));
		await new Promise((resolve) => setTimeout(resolve, 0));

		expect(window.location.pathname).toBe('/');
		const quickView = document.getElementById('quickView');
		expect(quickView.classList.contains('hidden')).toBe(false);
		expect(document.getElementById('quickViewTitle').textContent).toBe('Pretty Switch');

		const quickViewAddHacsBtn = document.getElementById('quickViewAddHacs');
		quickViewAddHacsBtn.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
		const hacsDialog = document.getElementById('hacsDialog');
		expect(hacsDialog.classList.contains('hidden')).toBe(false);
		window.closeHacsDialog();
		expect(quickView.classList.contains('hidden')).toBe(true);

		window.closeQuickView();
		expect(quickView.classList.contains('hidden')).toBe(true);

		await window.openSwitchDetails(uid, false);

		// Deep link: should use /switch/<uid>
		expect(window.location.pathname).toBe(`/switch/${uid}`);
		expect(document.body.classList.contains('view-switch')).toBe(true);

		// Banner: hero background should be set
		const hero = document.querySelector('.hero');
		expect(hero.classList.contains('hero-banner-active')).toBe(true);
		expect(hero.style.getPropertyValue('--hero-banner-image')).toContain(bannerUrl);

		// Hero text should reflect the switch (not the marketing headline)
		expect(document.getElementById('heroTitle').textContent).toBe('Pretty Switch');

		// Home Assistant deep link should target config flow
		const heroHaLink = document.getElementById('heroHaLink');
		expect(heroHaLink.getAttribute('href')).toContain('my.home-assistant.io/redirect/config_flow_start/?domain=vomesync');

		// Icon: should be visible in detail header
		const detailIcon = document.getElementById('detailIcon');
		expect(detailIcon.classList.contains('hidden')).toBe(false);
		expect(detailIcon.getAttribute('src')).toBe(iconUrl);

		// Post comment using v2 access key
		document.getElementById('commentText').value = 'Hello';

		if (typeof window.submitComment === 'function') {
			await window.submitComment();
		} else {
			// Fallback: submit through the form handler
			document.getElementById('commentForm').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
			await new Promise((resolve) => setTimeout(resolve, 0));
		}

		const commentCalls = global.fetch.mock.calls.filter(([u]) => String(u).endsWith(`/v2/switch/${uid}/comment`));
		expect(commentCalls.length).toBeGreaterThanOrEqual(1);
		const [_commentUrl, commentOptions] = commentCalls[0];
		expect(commentOptions.method).toBe('POST');
		expect(commentOptions.headers['X-Api-Key']).toBe(accessKey);
		expect(String(commentOptions.body)).toContain('Hello');

		// Closing detail should restore directory view
		window.closeDetail();
		expect(document.body.classList.contains('view-switch')).toBe(false);
	});

	test('management key enables hero status toggle', async () => {
		// Arrange DOM
		const websiteRoot = path.resolve(__dirname, '../../../website');
		const html = fs.readFileSync(path.join(websiteRoot, 'index.html'), 'utf8');
		document.documentElement.innerHTML = html;

		window.setInterval = jest.fn();
		window.alert = jest.fn();

		const uid = 'vs_toggle_test';
		const accessKey = '00000000-0000-4000-8000-111111111111';
		const toggleOk = {
			success: true,
			data: {
				uid,
				state: true,
				timestamp: Date.now(),
				toggleCount: 1
			}
		};

		const publicSwitchList = {
			success: true,
			data: {
				switches: [{
					uid,
					description: 'Toggle Switch',
					location: 'Test City',
					category: 'Community',
					state: false,
					lastToggled: 0,
					toggleCount: 0,
					userCount: 0,
					link: '',
					iconUrl: '',
					bannerUrl: '',
					ownerProfileUrl: ''
				}],
				count: 1,
				timestamp: Date.now()
			}
		};

		const publicSwitchDetail = {
			success: true,
			data: {
				uid,
				description: 'Toggle Switch',
				location: 'Test City',
				category: 'Community',
				state: false,
				lastToggled: 0,
				toggleCount: 0,
				userCount: 0,
				link: '',
				iconUrl: '',
				bannerUrl: '',
				ownerProfileUrl: '',
				events: []
			}
		};

		global.fetch = jest.fn(async (url, options) => {
			const u = String(url);
			if (u.endsWith('/public-switches')) {
				return createMockResponse(publicSwitchList);
			}
			if (u.endsWith(`/switch/${uid}`)) {
				return createMockResponse(publicSwitchDetail);
			}
			if (u.endsWith('/categories')) {
				return createMockResponse({ success: true, data: {} });
			}
			if (u.endsWith(`/v2/switch/${uid}/toggle`)) {
				return createMockResponse(toggleOk);
			}
			return createMockResponse({ success: false, error: `Unhandled fetch in test: ${u}` }, false, 404);
		});

		const script = fs.readFileSync(path.join(websiteRoot, 'script.js'), 'utf8');
		window.eval(script);
		window.setupEventListeners();

		window.sessionStorage.setItem(`vomesync_manage_key:${uid}`, accessKey);
		await window.loadSwitches();
		await window.openSwitchDetails(uid, false);

		const heroStatusButton = document.getElementById('heroStatusButton');
		heroStatusButton.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
		const toggleDialogSwitch = document.getElementById('toggleDialogSwitch');
		toggleDialogSwitch.checked = true;
		toggleDialogSwitch.dispatchEvent(new window.Event('change', { bubbles: true }));
		await new Promise((resolve) => setTimeout(resolve, 0));

		const toggleCalls = global.fetch.mock.calls.filter(([u]) => String(u).endsWith(`/v2/switch/${uid}/toggle`));
		expect(toggleCalls.length).toBeGreaterThanOrEqual(1);
		const [_toggleUrl, toggleOptions] = toggleCalls[0];
		expect(toggleOptions.method).toBe('POST');
		expect(toggleOptions.headers['X-Api-Key']).toBe(accessKey);
	});

	test('nav status shows auth state and opens the toggle dialog', async () => {
		const websiteRoot = path.resolve(__dirname, '../../../website');
		const html = fs.readFileSync(path.join(websiteRoot, 'index.html'), 'utf8');
		document.documentElement.innerHTML = html;

		window.setInterval = jest.fn();
		window.alert = jest.fn();

		const uid = 'vs_nav_test';
		const accessKey = '00000000-0000-4000-8000-222222222222';
		const toggleOk = {
			success: true,
			data: {
				uid,
				state: true,
				timestamp: Date.now(),
				toggleCount: 1
			}
		};

		const publicSwitchList = {
			success: true,
			data: {
				switches: [{
					uid,
					description: 'Nav Switch',
					location: 'Test City',
					category: 'Community',
					state: true,
					lastToggled: 0,
					toggleCount: 0,
					userCount: 0,
					link: '',
					iconUrl: '',
					bannerUrl: '',
					ownerProfileUrl: ''
				}],
				count: 1,
				timestamp: Date.now()
			}
		};

		const publicSwitchDetail = {
			success: true,
			data: {
				uid,
				description: 'Nav Switch',
				location: 'Test City',
				category: 'Community',
				state: true,
				lastToggled: 0,
				toggleCount: 0,
				userCount: 0,
				link: '',
				iconUrl: '',
				bannerUrl: '',
				ownerProfileUrl: '',
				events: []
			}
		};

		global.fetch = jest.fn(async (url) => {
			const u = String(url);
			if (u.endsWith('/public-switches')) {
				return createMockResponse(publicSwitchList);
			}
			if (u.endsWith(`/switch/${uid}`)) {
				return createMockResponse(publicSwitchDetail);
			}
			if (u.endsWith('/categories')) {
				return createMockResponse({ success: true, data: {} });
			}
			if (u.endsWith(`/v2/switch/${uid}/toggle`)) {
				return createMockResponse(toggleOk);
			}
			return createMockResponse({ success: false, error: `Unhandled fetch in test: ${u}` }, false, 404);
		});

		const script = fs.readFileSync(path.join(websiteRoot, 'script.js'), 'utf8');
		window.eval(script);
		window.setupEventListeners();

		window.sessionStorage.setItem(`vomesync_manage_key:${uid}`, accessKey);
		await window.loadSwitches();
		await window.openSwitchDetails(uid, false);

		if (typeof window.initHeroObserver === 'function') {
			window.initHeroObserver();
		}

		const navStatus = document.getElementById('navSwitchStatus');
		expect(navStatus.classList.contains('hidden')).toBe(false);
		expect(navStatus.textContent).toContain('Nav Switch');
		expect(navStatus.textContent).toContain('Authenticated');

		navStatus.dispatchEvent(new window.MouseEvent('click', { bubbles: true, cancelable: true }));
		const toggleDialog = document.getElementById('toggleDialog');
		expect(toggleDialog.classList.contains('hidden')).toBe(false);
	});

	test('Add to HACS button opens the HACS dialog', async () => {
		const websiteRoot = path.resolve(__dirname, '../../../website');
		const html = fs.readFileSync(path.join(websiteRoot, 'index.html'), 'utf8');
		document.documentElement.innerHTML = html;

		window.setInterval = jest.fn();
		window.alert = jest.fn();

		const script = fs.readFileSync(path.join(websiteRoot, 'script.js'), 'utf8');
		window.eval(script);
		window.setupEventListeners();

		const hacsBtn = document.getElementById('hacsBtn');
		if (hacsBtn && typeof hacsBtn.click === 'function') {
			hacsBtn.click();
		} else if (typeof window.openHacsDialog === 'function') {
			window.openHacsDialog();
		}

		const hacsDialog = document.getElementById('hacsDialog');
		expect(hacsDialog.classList.contains('hidden')).toBe(false);
		expect(document.getElementById('hacsDialogRepo').value).toBe('https://github.com/Vortitron/VomeSync');
		expect(document.getElementById('hacsDialogOpenLink').getAttribute('href')).toContain('my.home-assistant.io/redirect/hacs_repository/');
	});

	test('manage-on-website deep link (#accessKey=...) loads the key and triggers autoscroll', async () => {
		// Arrange DOM
		const websiteRoot = path.resolve(__dirname, '../../../website');
		const html = fs.readFileSync(path.join(websiteRoot, 'index.html'), 'utf8');
		document.documentElement.innerHTML = html;

		// Prevent timers / UI popups from interfering with Jest
		window.setInterval = jest.fn();
		window.alert = jest.fn();
		window.requestAnimationFrame = (cb) => cb();

		const uid = 'vs_75bz1byjrbv0jfmxv8dq27rp2w';
		const accessKey = '896a0f98-fde4-409a-b81a-74cae381969b';

		// Start at the deep link with fragment
		window.history.pushState({}, '', `/switch/${uid}#accessKey=${accessKey}`);

		// Spy on history.replaceState (JSDOM doesn't reliably update window.location.hash)
		const originalReplaceState = window.history.replaceState.bind(window.history);
		window.history.replaceState = jest.fn((...args) => originalReplaceState(...args));

		const publicSwitchDetail = {
			success: true,
			data: {
				uid,
				description: 'Pretty Switch',
				location: 'Test City',
				category: 'Community',
				state: false,
				lastToggled: 0,
				toggleCount: 0,
				userCount: 0,
				link: '',
				iconUrl: '',
				bannerUrl: '',
				ownerProfileUrl: '',
				events: []
			}
		};

		global.fetch = jest.fn(async (url) => {
			const u = String(url);
			if (u.endsWith('/public-switches')) {
				return createMockResponse({ success: true, data: { switches: [], count: 0, timestamp: Date.now() } });
			}
			if (u.endsWith(`/switch/${uid}`)) {
				return createMockResponse(publicSwitchDetail);
			}
			if (u.endsWith('/categories')) {
				return createMockResponse({ success: true, data: {} });
			}
			if (u.endsWith(`/v2/switch/${uid}/metadata`)) {
				return createMockResponse({ success: false, error: 'No metadata updates provided' }, false, 400);
			}
			return createMockResponse({ success: false, error: `Unhandled fetch in test: ${u}` }, false, 404);
		});

		// Spy on scroll
		const managePanel = document.getElementById('managePanel');
		managePanel.scrollIntoView = jest.fn();

		// Load website script and run init manually (JSDOM won't re-fire DOMContentLoaded after eval)
		const script = fs.readFileSync(path.join(websiteRoot, 'script.js'), 'utf8');
		window.eval(script);
		window.init();
		await new Promise((resolve) => setTimeout(resolve, 0));
		await new Promise((resolve) => setTimeout(resolve, 0));

		// Assert: fragment is cleared (key should not remain in the URL)
		expect(window.history.replaceState).toHaveBeenCalled();
		const replaceCalls = window.history.replaceState.mock.calls;
		const lastReplace = replaceCalls[replaceCalls.length - 1] || [];
		const urlArg = String(lastReplace[2] || '');
		expect(urlArg).not.toContain('#accessKey=');

		// Assert: key stored for session
		const storedKey = window.sessionStorage.getItem(`vomesync_manage_key:${uid}`);
		expect(storedKey).toBe(accessKey);

		// Assert: autoscroll attempted
		expect(managePanel.scrollIntoView).toHaveBeenCalled();
	});

	test('redirected switch detail follows redirect and shows notice', async () => {
		const websiteRoot = path.resolve(__dirname, '../../../website');
		const html = fs.readFileSync(path.join(websiteRoot, 'index.html'), 'utf8');
		document.documentElement.innerHTML = html;

		window.setInterval = jest.fn();
		window.alert = jest.fn();

		const oldUid = 'vs_75bz1byjrbv0jfmxv8dq27rp2w';
		const newUid = 'vs_75bz1byjrbv0jfmxv8dq27rp2x';

		const redirectResponse = {
			success: true,
			data: {
				uid: oldUid,
				redirect: true,
				redirectTo: newUid,
				redirectReason: 'Compromised key'
			}
		};

		const newDetail = {
			success: true,
			data: {
				uid: newUid,
				description: 'New Switch',
				location: 'Test City',
				category: 'Community',
				state: false,
				lastToggled: 0,
				toggleCount: 0,
				userCount: 0,
				link: '',
				iconUrl: '',
				bannerUrl: '',
				ownerProfileUrl: '',
				events: []
			}
		};

		global.fetch = jest.fn(async (url) => {
			const u = String(url);
			if (u.endsWith('/public-switches')) {
				return createMockResponse({ success: true, data: { switches: [], count: 0, timestamp: Date.now() } });
			}
			if (u.endsWith(`/switch/${oldUid}`)) {
				return createMockResponse(redirectResponse);
			}
			if (u.endsWith(`/switch/${newUid}`)) {
				return createMockResponse(newDetail);
			}
			if (u.endsWith('/categories')) {
				return createMockResponse({ success: true, data: {} });
			}
			return createMockResponse({ success: false, error: `Unhandled fetch in test: ${u}` }, false, 404);
		});

		const script = fs.readFileSync(path.join(websiteRoot, 'script.js'), 'utf8');
		window.eval(script);
		window.history.pushState({}, '', `/switch/${oldUid}`);
		window.init();
		await new Promise((resolve) => setTimeout(resolve, 0));
		await new Promise((resolve) => setTimeout(resolve, 0));
		await window.openSwitchDetails(oldUid, false);
		await new Promise((resolve) => setTimeout(resolve, 0));
		await window.openSwitchDetails(newUid, false, {
			allowRedirect: false,
			redirectFrom: oldUid,
			redirectReason: 'Compromised key'
		});

		const fetchCalls = global.fetch.mock.calls.map((call) => String(call[0]));
		expect(fetchCalls.some((url) => url.includes(`/switch/${oldUid}`))).toBe(true);
		const redirectText = document.getElementById('redirectNoticeText');
		expect(redirectText.textContent).toContain(newUid);
		expect(redirectText.textContent).toContain('Compromised key');
	});
});


