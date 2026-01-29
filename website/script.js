// VomeSync Website JavaScript (static SPA)

const SWITCH_UID_V2_REGEX = /^vs_[0-9a-hjkmnpqrstvwxyz]{26}$/i;

function normaliseApiBaseUrl(url) {
	if (!url) return '';
	return String(url).trim().replace(/\/+$/, '');
}

function getApiBaseUrlOverride() {
	const params = new URLSearchParams(window.location.search);
	const override = params.get('api');
	return override ? normaliseApiBaseUrl(override) : '';
}

function resolveApiBaseUrl() {
	const override = getApiBaseUrlOverride();
	if (override) return override;
	
	const port = window.location.port;
	const hostname = window.location.hostname;
	
	// When served via the combined proxy (sync.vome.io), the API is available at same-origin /api.
	if (!port || port === '80' || port === '443' || port === '8080' || port === '8443') {
		return `${window.location.origin}/api`;
	}
	
	// Direct website ports (docker-compose defaults)
	if (port === '8112') {
		return `http://${hostname}:3091/api`; // dev webserver port
	}
	if (port === '8111') {
		return `http://${hostname}:3090/api`; // live webserver port
	}
	
	// Fallback to same-origin /api
	return `${window.location.origin}/api`;
}

function resolveEnvBadge(apiBaseUrl) {
	const host = window.location.hostname;
	const port = window.location.port;
	
	if (host === 'sync.vome.io') {
		return { label: 'LIVE', className: 'env-live', api: apiBaseUrl };
	}
	if (port === '8112' || apiBaseUrl.includes(':3091')) {
		return { label: 'DEV', className: 'env-dev', api: apiBaseUrl };
	}
	return { label: 'CUSTOM', className: 'env-custom', api: apiBaseUrl };
}

// API base URL (resolved once at startup)
const API_BASE_URL = resolveApiBaseUrl();
const HACS_REPO_URL = 'https://github.com/Vortitron/VomeSync';
const HACS_ADD_URL = 'https://my.home-assistant.io/redirect/hacs_repository/?owner=Vortitron&repository=VomeSync&category=integration';

let allSwitches = [];
let filteredSwitches = [];
let categories = {};
let currentSwitchId = null;
let currentSwitchDetail = null;

// DOM elements
const heroSection = document.querySelector('.hero');
const heroTitleEl = document.getElementById('heroTitle');
const heroSubtitleEl = document.getElementById('heroSubtitle');
const heroButtonsDefault = document.getElementById('heroButtonsDefault');
const heroButtonsSwitch = document.getElementById('heroButtonsSwitch');
const heroStatusButton = document.getElementById('heroStatusButton');
const heroHaLink = document.getElementById('heroHaLink');
const navSwitchStatus = document.getElementById('navSwitchStatus');
const hacsBtn = document.getElementById('hacsBtn');
const homeBrand = document.getElementById('homeBrand');
const loadingMessage = document.getElementById('loadingMessage');
const errorMessage = document.getElementById('errorMessage');
const emptySwitches = document.getElementById('emptySwitches');
const switchesGrid = document.getElementById('switchesGrid');
const searchBox = document.getElementById('searchBox');
const categoryFilter = document.getElementById('categoryFilter');
const userCountFilter = document.getElementById('userCountFilter');
const refreshBtn = document.getElementById('refreshBtn');
const totalSwitches = document.getElementById('totalSwitches');
const activeSwitches = document.getElementById('activeSwitches');
const lastUpdate = document.getElementById('lastUpdate');
const categoryList = document.getElementById('categoryList');
const detailSection = document.getElementById('switchDetail');
const detailTitle = document.getElementById('detailTitle');
const detailIcon = document.getElementById('detailIcon');
const detailLocation = document.getElementById('detailLocation');
const redirectNotice = document.getElementById('redirectNotice');
const redirectNoticeText = document.getElementById('redirectNoticeText');
const redirectNoticeLink = document.getElementById('redirectNoticeLink');
const detailCategory = document.getElementById('detailCategory');
const detailUsers = document.getElementById('detailUsers');
const detailToggles = document.getElementById('detailToggles');
const detailLastChange = document.getElementById('detailLastChange');
const detailUid = document.getElementById('detailUid');
const detailEvents = document.getElementById('detailEvents');
const backToList = document.getElementById('backToList');
const copySwitchLinkBtn = document.getElementById('copySwitchLink');
const switchWebLink = document.getElementById('switchWebLink');
const ownerWebLink = document.getElementById('ownerWebLink');
const authPanel = document.getElementById('authPanel');
const authLoggedOut = document.getElementById('authLoggedOut');
const authLoggedIn = document.getElementById('authLoggedIn');
const authStatusText = document.getElementById('authStatusText');
const authStatus = document.getElementById('authStatus');
const authLoginBtn = document.getElementById('authLoginBtn');
const authChangeBtn = document.getElementById('authChangeBtn');
const authLogoutBtn = document.getElementById('authLogoutBtn');
const commentSection = document.getElementById('commentSection');
const commentForm = document.getElementById('commentForm');
const commentTextInput = document.getElementById('commentText');
const commentStateLabel = document.getElementById('commentStateLabel');
const commentStateSelect = document.getElementById('commentState');
const commentKeyNote = document.getElementById('commentKeyNote');
const commentStatus = document.getElementById('commentStatus');

// Owner tools (appearance)
const managePanel = document.getElementById('managePanel');
const manageForm = document.getElementById('manageForm');
const manageLinkInput = document.getElementById('manageLink');
const manageIconFileInput = document.getElementById('manageIconFile');
const manageIconUrlInput = document.getElementById('manageIconUrl');
const manageBannerFileInput = document.getElementById('manageBannerFile');
const manageBannerUrlInput = document.getElementById('manageBannerUrl');
const manageStatus = document.getElementById('manageStatus');

// Manage media pickers (previews + replace UX)
const manageIconPreview = document.getElementById('manageIconPreview');
const manageIconPlaceholder = document.getElementById('manageIconPlaceholder');
const manageIconEdit = document.getElementById('manageIconEdit');
const manageIconReplaceBtn = document.getElementById('manageIconReplaceBtn');
const manageIconRemoveBtn = document.getElementById('manageIconRemoveBtn');
const manageIconCancelBtn = document.getElementById('manageIconCancelBtn');

const manageBannerPreview = document.getElementById('manageBannerPreview');
const manageBannerPlaceholder = document.getElementById('manageBannerPlaceholder');
const manageBannerEdit = document.getElementById('manageBannerEdit');
const manageBannerReplaceBtn = document.getElementById('manageBannerReplaceBtn');
const manageBannerRemoveBtn = document.getElementById('manageBannerRemoveBtn');
const manageBannerCancelBtn = document.getElementById('manageBannerCancelBtn');

// Admin tools
const adminPanel = document.getElementById('adminPanel');
const adminKeyInput = document.getElementById('adminKeyInput');
const adminKeySaveBtn = document.getElementById('adminKeySaveBtn');
const adminKeyForgetBtn = document.getElementById('adminKeyForgetBtn');
const adminTools = document.getElementById('adminTools');
const adminStatus = document.getElementById('adminStatus');
const adminDelistForm = document.getElementById('adminDelistForm');
const adminDelistUid = document.getElementById('adminDelistUid');
const adminDeleteForm = document.getElementById('adminDeleteForm');
const adminDeleteUid = document.getElementById('adminDeleteUid');
const adminBlockForm = document.getElementById('adminBlockForm');
const adminBlockType = document.getElementById('adminBlockType');
const adminBlockValue = document.getElementById('adminBlockValue');
const adminBlockAction = document.getElementById('adminBlockAction');
const adminRedirectForm = document.getElementById('adminRedirectForm');
const adminRedirectFrom = document.getElementById('adminRedirectFrom');
const adminRedirectTo = document.getElementById('adminRedirectTo');
const adminRedirectReason = document.getElementById('adminRedirectReason');
const adminRedirectClearForm = document.getElementById('adminRedirectClearForm');
const adminRedirectClearUid = document.getElementById('adminRedirectClearUid');

// Quick view modal
const quickView = document.getElementById('quickView');
const quickViewBackdrop = document.getElementById('quickViewBackdrop');
const quickViewCloseBtn = document.getElementById('quickViewClose');
const quickViewIcon = document.getElementById('quickViewIcon');
const quickViewTitle = document.getElementById('quickViewTitle');
const quickViewSubtitle = document.getElementById('quickViewSubtitle');
const quickViewMeta = document.getElementById('quickViewMeta');
const quickViewCopyUidBtn = document.getElementById('quickViewCopyUid');
const quickViewCopyHaBtn = document.getElementById('quickViewCopyHa');
const quickViewAddHacsBtn = document.getElementById('quickViewAddHacs');
const quickViewOpenDetailsBtn = document.getElementById('quickViewOpenDetails');

const haDialog = document.getElementById('haDialog');
const haDialogBackdrop = document.getElementById('haDialogBackdrop');
const haDialogCloseBtn = document.getElementById('haDialogClose');
const haDialogCode = document.getElementById('haDialogCode');
const haDialogCopyBtn = document.getElementById('haDialogCopyBtn');
const haDialogCopyOpenBtn = document.getElementById('haDialogCopyOpenBtn');
const haDialogOpenLink = document.getElementById('haDialogOpenLink');
const haDialogBadgeLink = document.getElementById('haDialogBadgeLink');

const hacsDialog = document.getElementById('hacsDialog');
const hacsDialogBackdrop = document.getElementById('hacsDialogBackdrop');
const hacsDialogCloseBtn = document.getElementById('hacsDialogClose');
const hacsDialogRepo = document.getElementById('hacsDialogRepo');
const hacsDialogCopyBtn = document.getElementById('hacsDialogCopyBtn');
const hacsDialogOpenLink = document.getElementById('hacsDialogOpenLink');
const hacsDialogBadgeLink = document.getElementById('hacsDialogBadgeLink');

const toggleDialog = document.getElementById('toggleDialog');
const toggleDialogBackdrop = document.getElementById('toggleDialogBackdrop');
const toggleDialogCloseBtn = document.getElementById('toggleDialogClose');
const toggleDialogSwitch = document.getElementById('toggleDialogSwitch');
const toggleDialogState = document.getElementById('toggleDialogState');
const toggleDialogStatus = document.getElementById('toggleDialogStatus');
const toggleDialogEvents = document.getElementById('toggleDialogEvents');

const authDialog = document.getElementById('authDialog');
const authDialogBackdrop = document.getElementById('authDialogBackdrop');
const authDialogCloseBtn = document.getElementById('authDialogClose');
const authDialogKeyInput = document.getElementById('authDialogKey');
const authDialogLoginBtn = document.getElementById('authDialogLoginBtn');
const authDialogStatus = document.getElementById('authDialogStatus');

const DEFAULT_HERO_TITLE_HTML = heroTitleEl ? heroTitleEl.innerHTML : '';
const DEFAULT_HERO_SUBTITLE_TEXT = heroSubtitleEl ? heroSubtitleEl.textContent : '';

let manageIconAction = 'keep';
let manageBannerAction = 'keep';
let manageCurrentIconUrl = '';
let manageCurrentBannerUrl = '';
let manageIconObjectUrl = null;
let manageBannerObjectUrl = null;
let heroToggleInFlight = false;
let pendingAuthToggle = false;
const togglePermissionDeniedByUid = {};
let heroInView = true;

let quickViewUid = null;
let quickViewDetail = null;

// Smarter refresh / update UI
const AUTO_REFRESH_MS = 60_000;
const LAST_UPDATE_TICK_MS = 1_000;

const HOME_ASSISTANT_CONFIG_FLOW_URL = 'https://my.home-assistant.io/redirect/config_flow_start/?domain=vomesync';

let lastSwitchFetchAt = 0;
let autoRefreshTimer = null;
let lastUpdateTimer = null;
let autoRefreshInFlight = false;

// Best-effort realtime updates (state + last toggled) via WebSocket
let wsClient = null;
let wsReconnectTimer = null;
let wsReconnectAttempts = 0;
const wsSubscribedUids = new Set();

let detailRefreshTimer = null;

document.addEventListener('DOMContentLoaded', () => {
	init();
});

function isValidSwitchUid(uid) {
	if (typeof uid !== 'string') return false;
	const trimmed = uid.trim();
	return Boolean(trimmed && SWITCH_UID_V2_REGEX.test(trimmed));
}

function extractSwitchUidFromPathname(pathname) {
	const raw = String(pathname || '');
	const match = raw.match(/^\/(switch|s)\/([^/]+)\/?$/i);
	if (!match) {
		return null;
	}
	try {
		const candidate = decodeURIComponent(match[2]);
		return isValidSwitchUid(candidate) ? candidate : null;
	} catch {
		return null;
	}
}

function cssEscapeUrl(url) {
	return String(url).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function setHeroBanner(bannerUrl) {
	if (!heroSection) return;
	const url = String(bannerUrl || '').trim();
	if (!url) {
		clearHeroBanner();
		return;
	}
	heroSection.classList.add('hero-banner-active');
	heroSection.style.setProperty('--hero-banner-image', `url("${cssEscapeUrl(url)}")`);
}

function clearHeroBanner() {
	if (!heroSection) return;
	heroSection.classList.remove('hero-banner-active');
	heroSection.style.removeProperty('--hero-banner-image');
}

function setHeroText(title, subtitle) {
	if (heroTitleEl) heroTitleEl.textContent = title || '';
	if (heroSubtitleEl) heroSubtitleEl.textContent = subtitle || '';
}

function updateHeroStatusButton(detail) {
	if (!heroStatusButton) return;
	const stateKnown = typeof detail?.state === 'boolean';
	const isOn = stateKnown ? Boolean(detail.state) : false;
	const statusLabel = stateKnown ? `Status: ${isOn ? 'ON' : 'OFF'}` : 'Status: Unknown';

	heroStatusButton.textContent = statusLabel;
	heroStatusButton.classList.toggle('status-on', stateKnown && isOn);
	heroStatusButton.classList.toggle('status-off', stateKnown && !isOn);
	const uid = detail?.uid || currentSwitchId;
	const authed = Boolean(uid && hasAccessKey(uid));
	heroStatusButton.classList.toggle('is-interactive', authed);

	const ts = detail?.lastToggled ? Number(detail.lastToggled) : 0;
	let title = '';
	if (ts) {
		const absolute = new Date(ts).toLocaleString();
		const relative = formatTimeAgo(new Date(ts));
		title = `Last changed: ${absolute} (${relative})`;
	} else if (stateKnown) {
		title = 'No activity yet';
	} else {
		title = 'Status unknown';
	}
	if (authed) {
		title = `${title} — click to toggle`;
	} else if (uid) {
		title = `${title} — authenticate to toggle`;
	}
	heroStatusButton.title = title;

	updateNavSwitchStatus(detail);
}

function updateNavSwitchStatus(detail) {
	if (!navSwitchStatus) return;
	const uid = currentSwitchId;
	if (!uid || heroInView) {
		navSwitchStatus.classList.add('hidden');
		navSwitchStatus.classList.remove('on', 'off');
		return;
	}
	const info = detail || currentSwitchDetail || {};
	const name = getDisplaySwitchName(info.description || info.name || uid) || 'Switch';
	const stateKnown = typeof info.state === 'boolean';
	const isOn = stateKnown ? Boolean(info.state) : false;
	const stateLabel = stateKnown ? (isOn ? 'ON' : 'OFF') : 'Unknown';
	const authLabel = hasAccessKey(uid) ? 'Authenticated' : 'Not authenticated';
	navSwitchStatus.textContent = `${name} · ${stateLabel} · ${authLabel}`;
	navSwitchStatus.classList.toggle('on', stateKnown && isOn);
	navSwitchStatus.classList.toggle('off', stateKnown && !isOn);
	navSwitchStatus.classList.remove('hidden');
}

function initHeroObserver() {
	if (!heroSection || !navSwitchStatus) return;
	if (typeof window.IntersectionObserver === 'function') {
		const observer = new IntersectionObserver((entries) => {
			const entry = entries && entries[0];
			heroInView = entry ? entry.isIntersecting : true;
			updateNavSwitchStatus(currentSwitchDetail || {});
		}, { rootMargin: '-64px 0px 0px 0px', threshold: 0.1 });
		observer.observe(heroSection);
		return;
	}
	const onScroll = () => {
		const rect = heroSection.getBoundingClientRect();
		heroInView = rect.bottom > 64;
		updateNavSwitchStatus(currentSwitchDetail || {});
	};
	window.addEventListener('scroll', onScroll, { passive: true });
	onScroll();
}

function handleStatusAction() {
	if (!currentSwitchId) return;
	if (!hasAccessKey(currentSwitchId)) {
		openAuthDialog(true, 'Authentication required to toggle.');
		return;
	}
	if (!hasTogglePermission(currentSwitchId)) {
		openAuthDialog(true, 'Key lacks toggle permission. Enter a new key.');
		return;
	}
	openToggleDialogForDetail(currentSwitchDetail || { uid: currentSwitchId });
}

function restoreHeroText() {
	if (!heroTitleEl || !heroSubtitleEl) return;
	heroTitleEl.innerHTML = DEFAULT_HERO_TITLE_HTML;
	heroSubtitleEl.textContent = DEFAULT_HERO_SUBTITLE_TEXT;
	updateHeroStatusButton(null);
	if (heroHaLink) {
		heroHaLink.href = HOME_ASSISTANT_CONFIG_FLOW_URL;
	}
}

function setHeroForSwitch(detail) {
	if (!detail) return;
	if (!heroTitleEl || !heroSubtitleEl) return;

	const title = getDisplaySwitchName(detail.description) || 'Switch';
	const subtitleBits = [];
	const location = String(detail.location || '').trim();
	if (location) subtitleBits.push(`📍 ${location}`);
	const category = String(detail.category || '').trim();
	if (category) subtitleBits.push(`🏷️ ${category}`);
	const users = (typeof detail.userCount === 'number') ? detail.userCount : null;
	if (users != null) {
		subtitleBits.push(`👥 ${users} user${users === 1 ? '' : 's'}`);
	}

	setHeroText(title, subtitleBits.join('  ·  ') || DEFAULT_HERO_SUBTITLE_TEXT);
	updateHeroStatusButton(detail);
	if (heroHaLink) {
		heroHaLink.href = HOME_ASSISTANT_CONFIG_FLOW_URL;
	}
}

function init() {
	applyEnvBadge();
	applyDynamicLinks();
	setupEventListeners();
	initHeroObserver();
	importManagementKeyFromHash();
	updateAdminPanelVisibility();
	loadAllData();
	restoreSwitchFromQuery();
}

const MANAGEMENT_KEY_STORAGE_PREFIX = 'vomesync_manage_key:';
const MANAGEMENT_KEY_PERSIST_PREFIX = 'vomesync_manage_key_persist:';
const API_KEY_STORAGE_PREFIX = 'vomesync_api_key:';
const API_KEY_PERSIST_PREFIX = 'vomesync_api_key_persist:';
const ADMIN_KEY_STORAGE = 'vomesync_admin_key';
let managementAutoscrollUid = null;
let redirectNoticeTargetUid = '';

function readPersistentKey(prefix, uid) {
	if (!uid) return '';
	try {
		const raw = localStorage.getItem(`${prefix}${uid}`);
		if (!raw) return '';
		const data = JSON.parse(raw);
		if (!data || typeof data !== 'object') return '';
		if (data.expiresAt && Date.now() > data.expiresAt) {
			localStorage.removeItem(`${prefix}${uid}`);
			return '';
		}
		return String(data.key || '');
	} catch {
		return '';
	}
}

function writePersistentKey(prefix, uid, key, expiresAt) {
	if (!uid || !key) return;
	try {
		localStorage.setItem(`${prefix}${uid}`, JSON.stringify({
			key,
			expiresAt: Number.isFinite(expiresAt) ? expiresAt : 0
		}));
	} catch {
		// ignore
	}
}

function clearPersistentKey(prefix, uid) {
	if (!uid) return;
	try {
		localStorage.removeItem(`${prefix}${uid}`);
	} catch {
		// ignore
	}
}

function getStoredManagementKey(uid) {
	if (!uid) return '';
	try {
		const sessionKey = sessionStorage.getItem(`${MANAGEMENT_KEY_STORAGE_PREFIX}${uid}`) || '';
		if (sessionKey) return sessionKey;
		return readPersistentKey(MANAGEMENT_KEY_PERSIST_PREFIX, uid) || '';
	} catch {
		return '';
	}
}

function setStoredManagementKey(uid, apiKey, options = {}) {
	if (!uid) return;
	try {
		if (!apiKey) {
			sessionStorage.removeItem(`${MANAGEMENT_KEY_STORAGE_PREFIX}${uid}`);
			clearPersistentKey(MANAGEMENT_KEY_PERSIST_PREFIX, uid);
		} else {
			sessionStorage.setItem(`${MANAGEMENT_KEY_STORAGE_PREFIX}${uid}`, apiKey);
			if (options && options.persist) {
				writePersistentKey(MANAGEMENT_KEY_PERSIST_PREFIX, uid, apiKey, options.expiresAt);
			} else {
				clearPersistentKey(MANAGEMENT_KEY_PERSIST_PREFIX, uid);
			}
		}
	} catch {
		// ignore (private mode, etc)
	}
}

function getStoredApiKey(uid) {
	if (!uid) return '';
	try {
		const sessionKey = sessionStorage.getItem(`${API_KEY_STORAGE_PREFIX}${uid}`) || '';
		if (sessionKey) return sessionKey;
		return readPersistentKey(API_KEY_PERSIST_PREFIX, uid) || '';
	} catch {
		return '';
	}
}

function setStoredApiKey(uid, apiKey, options = {}) {
	if (!uid) return;
	try {
		if (!apiKey) {
			sessionStorage.removeItem(`${API_KEY_STORAGE_PREFIX}${uid}`);
			clearPersistentKey(API_KEY_PERSIST_PREFIX, uid);
		} else {
			sessionStorage.setItem(`${API_KEY_STORAGE_PREFIX}${uid}`, apiKey);
			if (options && options.persist) {
				writePersistentKey(API_KEY_PERSIST_PREFIX, uid, apiKey, options.expiresAt);
			} else {
				clearPersistentKey(API_KEY_PERSIST_PREFIX, uid);
			}
		}
	} catch {
		// ignore
	}
}

function getStoredAdminKey() {
	try {
		return sessionStorage.getItem(ADMIN_KEY_STORAGE) || '';
	} catch {
		return '';
	}
}

function setStoredAdminKey(value) {
	try {
		if (!value) {
			sessionStorage.removeItem(ADMIN_KEY_STORAGE);
		} else {
			sessionStorage.setItem(ADMIN_KEY_STORAGE, value);
		}
	} catch {
		// ignore
	}
}

function shouldShowAdminPanel() {
	const params = new URLSearchParams(window.location.search || '');
	return params.get('admin') === '1' || Boolean(getStoredAdminKey());
}

function updateAdminPanelVisibility() {
	if (!adminPanel) return;
	const hasKey = Boolean(getStoredAdminKey());
	const shouldShow = shouldShowAdminPanel();
	adminPanel.classList.toggle('hidden', !shouldShow);
	if (adminTools) adminTools.classList.toggle('hidden', !hasKey);
	if (adminKeyInput && hasKey) adminKeyInput.value = '';
}

function setAdminStatus(message, isError = false) {
	if (!adminStatus) return;
	adminStatus.textContent = message || '';
	adminStatus.classList.toggle('hidden', !message);
	adminStatus.classList.toggle('success', Boolean(message) && !isError);
	adminStatus.classList.toggle('error', Boolean(message) && isError);
}

async function adminRequest(path, options = {}) {
	const adminKey = getStoredAdminKey();
	if (!adminKey) {
		throw new Error('Admin key required');
	}

	const response = await fetch(`${API_BASE_URL}${path}`, {
		method: options.method || 'POST',
		headers: {
			'Content-Type': 'application/json',
			'X-Admin-Key': adminKey
		},
		body: options.body ? JSON.stringify(options.body) : undefined
	});

	let data = {};
	try {
		data = await response.json();
	} catch {
		data = {};
	}

	if (!response.ok || !data.success) {
		throw new Error(data.error || `HTTP ${response.status}`);
	}
	return data.data || {};
}

function getActiveManagementKey(uid) {
	return getStoredManagementKey(uid);
}

function getActiveApiKey(uid) {
	return getStoredApiKey(uid);
}

function resolveCommentAccessKey(uid) {
	return getActiveApiKey(uid) || getActiveManagementKey(uid);
}

function hasAccessKey(uid) {
	return Boolean(getActiveApiKey(uid) || getActiveManagementKey(uid));
}

function getActiveToggleKey(uid) {
	return getActiveApiKey(uid) || getActiveManagementKey(uid);
}

function hasTogglePermission(uid) {
	if (!uid) return false;
	if (!hasAccessKey(uid)) return false;
	return togglePermissionDeniedByUid[uid] !== true;
}

function updateCommentStateVisibility() {
	if (!commentStateSelect || !commentStateLabel) return;
	const uid = currentSwitchId;
	const allowed = Boolean(uid && hasTogglePermission(uid));
	if (allowed) {
		commentStateLabel.classList.remove('hidden');
		commentStateSelect.classList.remove('hidden');
		if (commentKeyNote) {
			commentKeyNote.textContent = 'State changes use your authenticated key.';
		}
	} else {
		commentStateLabel.classList.add('hidden');
		commentStateSelect.classList.add('hidden');
		commentStateSelect.value = '';
		if (commentKeyNote) {
			commentKeyNote.textContent = 'Authenticate to enable state changes.';
		}
	}
}

function showRedirectNotice(fromUid, toUid, reason) {
	if (!redirectNotice || !redirectNoticeText || !redirectNoticeLink) return;
	const safeFrom = fromUid ? String(fromUid) : '';
	const safeTo = toUid ? String(toUid) : '';
	const reasonText = reason ? ` (${String(reason)})` : '';
	redirectNoticeText.textContent = safeTo
		? `This switch moved to ${safeTo}.${reasonText}`
		: `This switch has been redirected.${reasonText}`;
	redirectNoticeTargetUid = safeTo;
	if (safeTo) {
		redirectNoticeLink.href = buildSwitchPath(safeTo);
		redirectNoticeLink.classList.remove('hidden');
	} else {
		redirectNoticeLink.href = '#';
		redirectNoticeLink.classList.add('hidden');
	}
	redirectNotice.classList.remove('hidden');
}

function hideRedirectNotice() {
	if (!redirectNotice) return;
	redirectNotice.classList.add('hidden');
	redirectNoticeTargetUid = '';
	if (redirectNoticeLink) {
		redirectNoticeLink.href = '#';
	}
	if (redirectNoticeText) {
		redirectNoticeText.textContent = '';
	}
}

async function detectManagementPermission(uid, key) {
	if (!uid || !key) return { valid: false, error: 'Access key required.' };
	try {
		const response = await fetch(`${API_BASE_URL}/v2/switch/${uid}/metadata`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'X-Api-Key': key
			},
			body: JSON.stringify({})
		});
		let data = {};
		try {
			data = await response.json();
		} catch {
			data = {};
		}

		if (response.status === 401) {
			return { valid: false, error: data.error || 'Invalid access key.' };
		}
		if (response.status === 403) {
			return { valid: true, isManagement: false };
		}
		if (response.status === 404) {
			return { valid: false, error: 'Switch not found.' };
		}
		if (response.status === 400) {
			return { valid: true, isManagement: true };
		}
		if (response.ok) {
			return { valid: true, isManagement: true };
		}
		return { valid: true, isManagement: false };
	} catch (error) {
		return { valid: false, error: 'Network error while checking key.' };
	}
}

function importManagementKeyFromHash() {
	const hash = String(window.location.hash || '');
	if (!hash || hash.length < 2) return;
	const params = new URLSearchParams(hash.slice(1));
	const apiKey = String(params.get('accessKey') || '').trim();
	const remember = String(params.get('remember') || '') === '1';
	const ttlSecondsRaw = Number.parseInt(params.get('ttlSeconds') || '', 10);
	const ttlSeconds = Number.isFinite(ttlSecondsRaw) && ttlSecondsRaw > 0 ? ttlSecondsRaw : 0;
	if (!apiKey) return;

	const uid = extractSwitchUidFromPathname(window.location.pathname);
	if (!uid) return;

	managementAutoscrollUid = uid;
	detectManagementPermission(uid, apiKey).then((result) => {
		if (!result.valid) {
			setStoredManagementKey(uid, '');
			setStoredApiKey(uid, '');
			setAuthStatus(result.error || 'Access key invalid or expired.', true);
			updateAuthPanel({ uid });
			updateAuthGates({ uid });
			updateCommentStateVisibility();
			return;
		}
		const expiresAt = remember && ttlSeconds ? Date.now() + (ttlSeconds * 1000) : 0;
		if (result.isManagement) {
			setStoredManagementKey(uid, apiKey, { persist: remember, expiresAt });
		} else {
			setStoredApiKey(uid, apiKey, { persist: remember, expiresAt });
		}
		togglePermissionDeniedByUid[uid] = false;
		setAuthStatus('Authenticated via website link.', false);
		updateAuthPanel({ uid });
		updateAuthGates({ uid });
		updateCommentStateVisibility();
		updateManagePanel({ uid });
	});

	// Clear the fragment to avoid leaving the key in the address bar/history.
	try {
		window.history.replaceState({}, '', window.location.pathname + window.location.search);
	} catch {
		// ignore
	}
}

function applyEnvBadge() {
	const badge = document.getElementById('envBadge');
	if (!badge) return;
	
	const info = resolveEnvBadge(API_BASE_URL);
	badge.textContent = info.label;
	badge.className = `env-badge ${info.className}`;
	badge.title = `API: ${info.api}`;
	badge.style.display = 'inline-flex';
}

function applyDynamicLinks() {
	
	const serverStatusLink = document.getElementById('serverStatusLink');
	if (serverStatusLink) {
		serverStatusLink.href = `${API_BASE_URL}/health`;
	}
}

function setupEventListeners() {
	searchBox.addEventListener('input', () => applyFilters());
	categoryFilter.addEventListener('change', () => applyFilters());
	userCountFilter.addEventListener('change', () => applyFilters());
	refreshBtn.addEventListener('click', loadAllData);

	if (homeBrand) {
		homeBrand.addEventListener('click', (e) => {
			e.preventDefault();
			closeQuickView();
			closeDetail();
			try {
				window.scrollTo({ top: 0, behavior: 'smooth' });
			} catch {
				// ignore
			}
		});
	}

	if (redirectNoticeLink) {
		redirectNoticeLink.addEventListener('click', (e) => {
			if (!redirectNoticeTargetUid) return;
			e.preventDefault();
			openSwitchDetails(redirectNoticeTargetUid);
		});
	}

	if (heroHaLink) {
		heroHaLink.addEventListener('click', (e) => {
			if (!currentSwitchId) return;
			e.preventDefault();
			openHaDialogForUid(currentSwitchId);
		});
	}

	if (hacsBtn) {
		hacsBtn.addEventListener('click', () => {
			openHacsDialog();
		});
	}

	if (heroStatusButton) {
		heroStatusButton.addEventListener('click', async () => {
			handleStatusAction();
		});
	}

	if (navSwitchStatus) {
		navSwitchStatus.addEventListener('click', () => {
			handleStatusAction();
		});
		navSwitchStatus.addEventListener('keydown', (event) => {
			if (event.key === 'Enter' || event.key === ' ') {
				event.preventDefault();
				handleStatusAction();
			}
		});
	}

	backToList.addEventListener('click', () => {
		closeDetail();
		scrollToSwitches();
	});

	copySwitchLinkBtn.addEventListener('click', () => {
		if (!currentSwitchId) return;
		const link = `${window.location.origin}${buildSwitchPath(currentSwitchId)}`;
		copyText(link, copySwitchLinkBtn, '🔗 Copy link');
	});

	commentForm.addEventListener('submit', async (e) => {
		e.preventDefault();
		await submitComment();
	});

	if (manageForm) {
		manageForm.addEventListener('submit', async (e) => {
			e.preventDefault();
			await submitManageAppearance();
		});
	}

	if (adminKeySaveBtn) {
		adminKeySaveBtn.addEventListener('click', () => {
			const key = adminKeyInput ? String(adminKeyInput.value || '').trim() : '';
			if (!key) {
				setAdminStatus('Admin key required.', true);
				return;
			}
			setStoredAdminKey(key);
			if (adminKeyInput) adminKeyInput.value = '';
			setAdminStatus('Admin key saved.', false);
			updateAdminPanelVisibility();
		});
	}

	if (adminKeyForgetBtn) {
		adminKeyForgetBtn.addEventListener('click', () => {
			setStoredAdminKey('');
			setAdminStatus('Admin key cleared.', false);
			updateAdminPanelVisibility();
		});
	}

	if (adminDelistForm) {
		adminDelistForm.addEventListener('submit', async (e) => {
			e.preventDefault();
			const uid = adminDelistUid ? String(adminDelistUid.value || '').trim() : '';
			if (!uid) {
				setAdminStatus('Switch UID required.', true);
				return;
			}
			try {
				await adminRequest(`/admin/switch/${encodeURIComponent(uid)}/delist`);
				setAdminStatus(`Delisted ${uid}.`, false);
			} catch (error) {
				setAdminStatus(error.message || 'Failed to delist.', true);
			}
		});
	}

	if (adminDeleteForm) {
		adminDeleteForm.addEventListener('submit', async (e) => {
			e.preventDefault();
			const uid = adminDeleteUid ? String(adminDeleteUid.value || '').trim() : '';
			if (!uid) {
				setAdminStatus('Switch UID required.', true);
				return;
			}
			if (!confirm(`Delete switch ${uid}? This cannot be undone.`)) {
				return;
			}
			try {
				await adminRequest(`/admin/switch/${encodeURIComponent(uid)}/delete`);
				setAdminStatus(`Deleted ${uid}.`, false);
			} catch (error) {
				setAdminStatus(error.message || 'Failed to delete.', true);
			}
		});
	}

	if (adminBlockForm) {
		adminBlockForm.addEventListener('submit', async (e) => {
			e.preventDefault();
			const type = adminBlockType ? String(adminBlockType.value || '').trim() : '';
			const value = adminBlockValue ? String(adminBlockValue.value || '').trim() : '';
			const action = adminBlockAction ? String(adminBlockAction.value || '').trim() : 'block';
			if (!type || !value) {
				setAdminStatus('Block type and value required.', true);
				return;
			}
			try {
				await adminRequest('/admin/blocks', {
					body: {
						action,
						type,
						value
					}
				});
				setAdminStatus(`${action === 'block' ? 'Blocked' : 'Unblocked'} ${type}.`, false);
			} catch (error) {
				setAdminStatus(error.message || 'Block update failed.', true);
			}
		});
	}

	if (adminRedirectForm) {
		adminRedirectForm.addEventListener('submit', async (e) => {
			e.preventDefault();
			const fromUid = adminRedirectFrom ? String(adminRedirectFrom.value || '').trim() : '';
			const toUid = adminRedirectTo ? String(adminRedirectTo.value || '').trim() : '';
			const reason = adminRedirectReason ? String(adminRedirectReason.value || '').trim() : '';
			if (!fromUid || !toUid) {
				setAdminStatus('Both UIDs are required for redirects.', true);
				return;
			}
			try {
				await adminRequest('/admin/redirects', {
					body: { fromUid, toUid, reason }
				});
				setAdminStatus(`Redirected ${fromUid} → ${toUid}.`, false);
			} catch (error) {
				setAdminStatus(error.message || 'Redirect failed.', true);
			}
		});
	}

	if (adminRedirectClearForm) {
		adminRedirectClearForm.addEventListener('submit', async (e) => {
			e.preventDefault();
			const uid = adminRedirectClearUid ? String(adminRedirectClearUid.value || '').trim() : '';
			if (!uid) {
				setAdminStatus('Redirect UID required.', true);
				return;
			}
			try {
				await adminRequest(`/admin/redirects/${encodeURIComponent(uid)}`, { method: 'DELETE' });
				setAdminStatus(`Cleared redirect for ${uid}.`, false);
			} catch (error) {
				setAdminStatus(error.message || 'Clear redirect failed.', true);
			}
		});
	}

	if (authLoginBtn) {
		authLoginBtn.addEventListener('click', () => openAuthDialog(false));
	}

	if (authChangeBtn) {
		authChangeBtn.addEventListener('click', () => openAuthDialog(false));
	}

	if (authLogoutBtn) {
		authLogoutBtn.addEventListener('click', () => {
			if (!currentSwitchId) return;
			setStoredManagementKey(currentSwitchId, '');
			setStoredApiKey(currentSwitchId, '');
			togglePermissionDeniedByUid[currentSwitchId] = false;
			setAuthStatus('Logged out.', false);
			updateAuthPanel(currentSwitchDetail || {});
			updateAuthGates(currentSwitchDetail || {});
			updateCommentStateVisibility();
			updateHeroStatusButton(currentSwitchDetail || {});
			updateManagePanel(currentSwitchDetail || {});
		});
	}

	// Manage icon picker actions
	if (manageIconReplaceBtn) {
		manageIconReplaceBtn.addEventListener('click', () => {
			manageIconAction = 'replace';
			setManageIconEditVisible(true);
			if (manageIconUrlInput) manageIconUrlInput.focus();
		});
	}
	if (manageIconCancelBtn) {
		manageIconCancelBtn.addEventListener('click', () => {
			manageIconAction = 'keep';
			revokeObjectUrl(manageIconObjectUrl);
			manageIconObjectUrl = null;
			if (manageIconFileInput) manageIconFileInput.value = '';
			if (manageIconUrlInput) manageIconUrlInput.value = '';
			setManageIconEditVisible(false);
			setMediaPreviewImage(manageIconPreview, manageIconPlaceholder, manageCurrentIconUrl);
		});
	}
	if (manageIconRemoveBtn) {
		manageIconRemoveBtn.addEventListener('click', () => {
			manageIconAction = 'remove';
			revokeObjectUrl(manageIconObjectUrl);
			manageIconObjectUrl = null;
			if (manageIconFileInput) manageIconFileInput.value = '';
			if (manageIconUrlInput) manageIconUrlInput.value = '';
			setManageIconEditVisible(false);
			setMediaPreviewImage(manageIconPreview, manageIconPlaceholder, '');
		});
	}
	if (manageIconFileInput) {
		manageIconFileInput.addEventListener('change', () => {
			const file = manageIconFileInput.files?.[0] || null;
			if (!file) return;
			manageIconAction = 'replace';
			setManageIconEditVisible(true);
			if (manageIconUrlInput) manageIconUrlInput.value = '';
			revokeObjectUrl(manageIconObjectUrl);
			try {
				manageIconObjectUrl = URL.createObjectURL(file);
			} catch {
				manageIconObjectUrl = null;
			}
			setMediaPreviewImage(manageIconPreview, manageIconPlaceholder, manageIconObjectUrl);
		});
	}
	if (manageIconUrlInput) {
		manageIconUrlInput.addEventListener('input', () => {
			const url = String(manageIconUrlInput.value || '').trim();
			if (!url) return;
			manageIconAction = 'replace';
			setManageIconEditVisible(true);
			revokeObjectUrl(manageIconObjectUrl);
			manageIconObjectUrl = null;
			if (manageIconFileInput) manageIconFileInput.value = '';
			setMediaPreviewImage(manageIconPreview, manageIconPlaceholder, url);
		});
	}

	// Manage banner picker actions
	if (manageBannerReplaceBtn) {
		manageBannerReplaceBtn.addEventListener('click', () => {
			manageBannerAction = 'replace';
			setManageBannerEditVisible(true);
			if (manageBannerUrlInput) manageBannerUrlInput.focus();
		});
	}
	if (manageBannerCancelBtn) {
		manageBannerCancelBtn.addEventListener('click', () => {
			manageBannerAction = 'keep';
			revokeObjectUrl(manageBannerObjectUrl);
			manageBannerObjectUrl = null;
			if (manageBannerFileInput) manageBannerFileInput.value = '';
			if (manageBannerUrlInput) manageBannerUrlInput.value = '';
			setManageBannerEditVisible(false);
			setMediaPreviewImage(manageBannerPreview, manageBannerPlaceholder, manageCurrentBannerUrl);
		});
	}
	if (manageBannerRemoveBtn) {
		manageBannerRemoveBtn.addEventListener('click', () => {
			manageBannerAction = 'remove';
			revokeObjectUrl(manageBannerObjectUrl);
			manageBannerObjectUrl = null;
			if (manageBannerFileInput) manageBannerFileInput.value = '';
			if (manageBannerUrlInput) manageBannerUrlInput.value = '';
			setManageBannerEditVisible(false);
			setMediaPreviewImage(manageBannerPreview, manageBannerPlaceholder, '');
		});
	}
	if (manageBannerFileInput) {
		manageBannerFileInput.addEventListener('change', () => {
			const file = manageBannerFileInput.files?.[0] || null;
			if (!file) return;
			manageBannerAction = 'replace';
			setManageBannerEditVisible(true);
			if (manageBannerUrlInput) manageBannerUrlInput.value = '';
			revokeObjectUrl(manageBannerObjectUrl);
			try {
				manageBannerObjectUrl = URL.createObjectURL(file);
			} catch {
				manageBannerObjectUrl = null;
			}
			setMediaPreviewImage(manageBannerPreview, manageBannerPlaceholder, manageBannerObjectUrl);
		});
	}
	if (manageBannerUrlInput) {
		manageBannerUrlInput.addEventListener('input', () => {
			const url = String(manageBannerUrlInput.value || '').trim();
			if (!url) return;
			manageBannerAction = 'replace';
			setManageBannerEditVisible(true);
			revokeObjectUrl(manageBannerObjectUrl);
			manageBannerObjectUrl = null;
			if (manageBannerFileInput) manageBannerFileInput.value = '';
			setMediaPreviewImage(manageBannerPreview, manageBannerPlaceholder, url);
		});
	}

	// Quick view modal handlers
	if (quickViewCloseBtn) quickViewCloseBtn.addEventListener('click', closeQuickView);
	if (quickViewBackdrop) quickViewBackdrop.addEventListener('click', closeQuickView);
	if (quickViewCopyUidBtn) {
		quickViewCopyUidBtn.addEventListener('click', () => {
			if (!quickViewUid) return;
			copyText(quickViewUid, quickViewCopyUidBtn, '📋 Copy UID');
		});
	}
	if (quickViewOpenDetailsBtn) {
		quickViewOpenDetailsBtn.addEventListener('click', () => {
			if (!quickViewUid) return;
			const uid = quickViewUid;
			closeQuickView();
			openSwitchDetails(uid, false);
		});
	}
	if (quickViewCopyHaBtn) {
		quickViewCopyHaBtn.addEventListener('click', () => {
			if (!quickViewUid) return;
			copyAndOpenHomeAssistant(quickViewUid, quickViewCopyHaBtn, '🏠 Copy UID + Add to HA');
		});
	}
	if (quickViewAddHacsBtn) {
		quickViewAddHacsBtn.addEventListener('click', () => {
			closeQuickView();
			openHacsDialog();
		});
	}
	if (haDialogCloseBtn) haDialogCloseBtn.addEventListener('click', closeHaDialog);
	if (haDialogBackdrop) haDialogBackdrop.addEventListener('click', closeHaDialog);
	if (haDialogCopyBtn) {
		haDialogCopyBtn.addEventListener('click', () => {
			const uid = haDialogCode?.value || '';
			if (!uid) return;
			copyText(uid, haDialogCopyBtn, 'Copy');
		});
	}
	if (hacsDialogCloseBtn) hacsDialogCloseBtn.addEventListener('click', closeHacsDialog);
	if (hacsDialogBackdrop) hacsDialogBackdrop.addEventListener('click', closeHacsDialog);
	if (hacsDialogCopyBtn) {
		hacsDialogCopyBtn.addEventListener('click', () => {
			const repo = hacsDialogRepo?.value || HACS_REPO_URL;
			copyText(repo, hacsDialogCopyBtn, 'Copy repo URL');
		});
	}

	if (toggleDialogCloseBtn) toggleDialogCloseBtn.addEventListener('click', closeToggleDialog);
	if (toggleDialogBackdrop) toggleDialogBackdrop.addEventListener('click', closeToggleDialog);
	if (toggleDialogSwitch) {
		toggleDialogSwitch.addEventListener('change', () => {
			updateToggleDialogStateLabel(toggleDialogSwitch.checked);
			applyToggleDialog();
		});
	}

	if (authDialogCloseBtn) authDialogCloseBtn.addEventListener('click', closeAuthDialog);
	if (authDialogBackdrop) authDialogBackdrop.addEventListener('click', closeAuthDialog);
	if (authDialogLoginBtn) {
		authDialogLoginBtn.addEventListener('click', () => {
			applyAuthDialog();
		});
	}
	if (haDialogCopyOpenBtn) {
		haDialogCopyOpenBtn.addEventListener('click', () => {
			const uid = haDialogCode?.value || '';
			if (!uid) return;
			copyAndOpenHomeAssistant(uid, haDialogCopyOpenBtn, 'Copy and Open');
			closeHaDialog();
		});
	}
	window.addEventListener('keydown', (e) => {
		if (e.key === 'Escape') {
			closeQuickView();
			closeHaDialog();
			closeHacsDialog();
			closeToggleDialog();
			closeAuthDialog();
		}
	});

	window.addEventListener('popstate', () => {
		restoreSwitchFromQuery();
	});

	document.addEventListener('visibilitychange', () => {
		// Kick a refresh when the user returns to the tab
		if (document.visibilityState === 'visible') {
			autoRefreshTick();
		}
	});

	startLastUpdateTicker();
	startAutoRefresh();
}

async function loadAllData() {
	showLoading();
	try {
		await Promise.all([refreshSwitches({ silent: false }), loadCategories()]);
		hideMessages();
		if (allSwitches.length === 0) {
			showEmptyMessage();
		}
	} catch (error) {
		console.error('Error loading data:', error);
		showError();
	}
}

async function loadSwitches() {
	// Backwards-compatible entry point (used by the "Try Again" button)
	try {
		await loadAllData();
	} catch (_err) {
		// loadAllData already shows error UI
	}
}

function startAutoRefresh() {
	if (autoRefreshTimer) return;
	autoRefreshTimer = setInterval(autoRefreshTick, AUTO_REFRESH_MS);
}

async function autoRefreshTick() {
	if (autoRefreshInFlight) return;
	if (document.visibilityState && document.visibilityState !== 'visible') return;

	autoRefreshInFlight = true;
	try {
		await refreshSwitches({ silent: true });
	} catch (error) {
		// Don't flash error UI for background refreshes
		console.warn('Background refresh failed:', error);
	} finally {
		autoRefreshInFlight = false;
	}
}

function startLastUpdateTicker() {
	if (lastUpdateTimer) return;
	lastUpdateTimer = setInterval(updateLastUpdateDisplay, LAST_UPDATE_TICK_MS);
	updateLastUpdateDisplay();
}

function updateLastUpdateDisplay() {
	if (!lastUpdate) return;
	if (!lastSwitchFetchAt) {
		lastUpdate.textContent = '-';
		return;
	}
	lastUpdate.textContent = formatTimeAgo(new Date(lastSwitchFetchAt));
	updateRelativeTimeStamps();
}

function updateRelativeTimeStamps() {
	// Update "Last Changed" timestamps on cards without doing any network requests.
	const stampEls = document.querySelectorAll('[data-field="lastChanged"][data-ts]');
	stampEls.forEach((el) => {
		const tsRaw = el.dataset.ts || '';
		const ts = tsRaw ? Number(tsRaw) : 0;
		if (!ts) {
			el.textContent = 'Never';
			return;
		}
		el.textContent = formatTimeAgo(new Date(ts));
	});
}

function getRenderedSwitchUids() {
	if (!switchesGrid) return [];
	return Array.from(switchesGrid.querySelectorAll('.switch-card[data-uid]'))
		.map((el) => String(el.dataset.uid || '').trim())
		.filter(Boolean);
}

function areUidListsEqual(a, b) {
	if (a === b) return true;
	if (!Array.isArray(a) || !Array.isArray(b)) return false;
	if (a.length !== b.length) return false;
	for (let i = 0; i < a.length; i += 1) {
		if (a[i] !== b[i]) return false;
	}
	return true;
}

function updateSwitchCardElement(card, switchData) {
	if (!card || !switchData) return;
	const uid = String(switchData.uid || '').trim();
	if (!uid) return;

	const stateClass = switchData.state ? 'state-on' : 'state-off';
	card.classList.remove('state-on', 'state-off');
	card.classList.add(stateClass);

	card.dataset.bannerUrl = String(switchData.bannerUrl || '');

	const iconEl = card.querySelector('[data-field="icon"]');
	if (iconEl) {
		const iconUrl = String(switchData.iconUrl || '').trim();
		if (iconUrl) {
			iconEl.src = iconUrl;
			iconEl.classList.remove('hidden');
		} else {
			iconEl.removeAttribute('src');
			iconEl.classList.add('hidden');
		}
	}

	const descEl = card.querySelector('[data-field="description"]');
	if (descEl) {
		descEl.textContent = getDisplaySwitchName(switchData.description) || 'Untitled Switch';
	}

	const stateEl = card.querySelector('[data-field="stateBadge"]');
	if (stateEl) {
		const on = Boolean(switchData.state);
		stateEl.classList.remove('on', 'off');
		stateEl.classList.add(on ? 'on' : 'off');
		stateEl.textContent = on ? 'ON' : 'OFF';
	}

	const usersEl = card.querySelector('[data-field="users"]');
	if (usersEl) {
		const userCount = (typeof switchData.userCount === 'number') ? switchData.userCount : 0;
		usersEl.textContent = `${userCount} user${userCount === 1 ? '' : 's'}`;
	}

	const togglesEl = card.querySelector('[data-field="toggles"]');
	if (togglesEl) {
		const toggleCount = (typeof switchData.toggleCount === 'number') ? switchData.toggleCount : 0;
		togglesEl.textContent = `${toggleCount} toggles`;
	}

	const lastEl = card.querySelector('[data-field="lastChanged"]');
	if (lastEl) {
		const ts = switchData.lastToggled ? Number(switchData.lastToggled) : 0;
		lastEl.dataset.ts = ts ? String(ts) : '';
		lastEl.textContent = ts ? formatTimeAgo(new Date(ts)) : 'Never';
	}
}

function updateRenderedSwitchCards() {
	const uids = getRenderedSwitchUids();
	if (!uids.length) return;

	const byUid = new Map(allSwitches.map((sw) => [sw.uid, sw]));
	uids.forEach((uid) => {
		const sw = byUid.get(uid);
		if (!sw) return;
		const card = switchesGrid.querySelector(`.switch-card[data-uid="${uid}"]`);
		updateSwitchCardElement(card, sw);
	});
}

function computeFilteredSwitches() {
	const searchQuery = searchBox.value.toLowerCase().trim();
	const category = categoryFilter.value;
	const minUsers = userCountFilter.value ? parseInt(userCountFilter.value, 10) : null;

	return allSwitches.filter((switchData) => {
		const description = getDisplaySwitchName(switchData.description || '').toLowerCase();
		const location = (switchData.location || '').toLowerCase();
		const categoryValue = (switchData.category || '').toLowerCase();

		const matchesSearch = !searchQuery || description.includes(searchQuery) || location.includes(searchQuery) || categoryValue.includes(searchQuery);
		const matchesCategory = !category || switchData.category === category;
		const matchesUserCount = !minUsers || (switchData.userCount || 0) >= minUsers;

		return matchesSearch && matchesCategory && matchesUserCount;
	});
}

function applyFilters(options = {}) {
	const shouldRender = options.render !== false;
	filteredSwitches = computeFilteredSwitches();
	if (shouldRender) {
		renderSwitches();
	}
}

async function refreshSwitches(options = {}) {
	const silent = options.silent === true;

	const renderedBefore = getRenderedSwitchUids();

	const response = await fetch(`${API_BASE_URL}/public-switches`);
	if (!response.ok) {
		throw new Error(`HTTP ${response.status}: ${response.statusText}`);
	}

	const data = await response.json();
	if (!data.success) {
		throw new Error(data.error || 'Failed to load switches');
	}

	allSwitches = data.data.switches || [];
	lastSwitchFetchAt = Date.now();
	updateStats();
	updateLastUpdateDisplay();

	// Preserve current filters (don't reset on refresh)
	applyFilters({ render: false });

	// If we're in "quiet" mode and the visible set hasn't changed, update cards in-place (no flicker).
	const renderedAfter = filteredSwitches.map((sw) => sw.uid);
	const canPatchInPlace = silent
		&& switchesGrid
		&& switchesGrid.querySelectorAll('.switch-card[data-uid]').length > 0
		&& areUidListsEqual(renderedBefore, renderedAfter);

	if (canPatchInPlace) {
		updateRenderedSwitchCards();
		wireSwitchCardClicks();
	} else {
		renderSwitches();
	}

	ensureRealtimeSubscriptions();
}

function resolveWebSocketBaseUrl() {
	let api;
	try {
		api = new URL(API_BASE_URL);
	} catch {
		return '';
	}

	const wsProtocol = api.protocol === 'https:' ? 'wss:' : 'ws:';
	let port = api.port || '';

	// When pointing directly at the webserver ports, use the configured WS ports.
	if (port === '3090') port = '3001';
	if (port === '3091') port = '3002';

	const host = api.hostname + (port ? `:${port}` : '');
	return `${wsProtocol}//${host}/ws`;
}

function clearWsReconnectTimer() {
	if (!wsReconnectTimer) return;
	try {
		clearTimeout(wsReconnectTimer);
	} catch {
		// ignore
	}
	wsReconnectTimer = null;
}

function disconnectRealtime() {
	clearWsReconnectTimer();
	wsReconnectAttempts = 0;
	wsSubscribedUids.clear();

	try {
		if (wsClient) {
			wsClient.onopen = null;
			wsClient.onmessage = null;
			wsClient.onclose = null;
			wsClient.onerror = null;
			wsClient.close();
		}
	} catch {
		// ignore
	}
	wsClient = null;
}

function scheduleWsReconnect(desiredUid) {
	clearWsReconnectTimer();
	wsReconnectAttempts = Math.min(wsReconnectAttempts + 1, 8);
	const delayMs = Math.min(30_000, 500 * (2 ** wsReconnectAttempts));
	wsReconnectTimer = setTimeout(() => {
		connectRealtime(desiredUid);
	}, delayMs);
}

function wsSendJson(value) {
	try {
		if (!wsClient || wsClient.readyState !== 1) return false; // 1 = OPEN
		wsClient.send(JSON.stringify(value));
		return true;
	} catch {
		return false;
	}
}

function handleStateUpdate(uid, state, timestamp) {
	const targetUid = String(uid || '').trim();
	if (!targetUid) return;

	const idx = allSwitches.findIndex((sw) => sw && sw.uid === targetUid);
	if (idx >= 0) {
		allSwitches[idx] = {
			...allSwitches[idx],
			state: Boolean(state),
			lastToggled: timestamp ? Number(timestamp) : allSwitches[idx].lastToggled
		};
	}

	// Patch visible card if present
	const card = switchesGrid ? switchesGrid.querySelector(`.switch-card[data-uid="${targetUid}"]`) : null;
	if (card && idx >= 0) {
		updateSwitchCardElement(card, allSwitches[idx]);
		updateStats();
	}

	if (currentSwitchId && currentSwitchId === targetUid) {
		currentSwitchDetail = {
			...(currentSwitchDetail || {}),
			uid: targetUid,
			state: Boolean(state),
			lastToggled: timestamp ? Number(timestamp) : (currentSwitchDetail?.lastToggled || null)
		};
		updateHeroStatusButton(currentSwitchDetail);
		if (toggleDialog && !toggleDialog.classList.contains('hidden')) {
			if (toggleDialogSwitch) {
				toggleDialogSwitch.checked = Boolean(state);
			}
			updateToggleDialogStateLabel(Boolean(state));
			renderToggleDialogActivity(currentSwitchDetail || {});
		}
		scheduleDetailRefresh(targetUid);
	}
}

function scheduleDetailRefresh(uid) {
	if (!uid || uid !== currentSwitchId) return;
	if (detailRefreshTimer) return;
	detailRefreshTimer = setTimeout(() => {
		detailRefreshTimer = null;
		refreshSwitchDetail(uid);
	}, 800);
}

async function refreshSwitchDetail(uid, allowRedirect = true) {
	if (!uid || uid !== currentSwitchId) return;
	try {
		const response = await fetch(`${API_BASE_URL}/switch/${uid}`);
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}: ${response.statusText}`);
		}
		const data = await response.json();
		if (!data.success || !data.data) {
			throw new Error(data.error || 'Failed to load switch detail');
		}

		const detail = data.data;
		if (detail && detail.redirect) {
			if (allowRedirect && detail.redirectTo) {
				await openSwitchDetails(detail.redirectTo, true, {
					allowRedirect: false,
					redirectFrom: uid,
					redirectReason: detail.redirectReason
				});
				return;
			}
			showRedirectNotice(uid, detail.redirectTo, detail.redirectReason);
			return;
		}

		currentSwitchDetail = detail;
		hideRedirectNotice();
		updateHeroStatusButton(detail);

		// Update activity + counts without resetting the manage panel inputs.
		updateDetailActivity(detail);
		syncSwitchDetailToIndex(detail);
	} catch (error) {
		console.warn('Failed to refresh switch detail:', error);
	}
}

async function ensureCurrentSwitchDetail(uid) {
	if (!uid || uid !== currentSwitchId) return currentSwitchDetail;
	if (currentSwitchDetail && currentSwitchDetail.uid === uid && typeof currentSwitchDetail.state === 'boolean') {
		return currentSwitchDetail;
	}
	await refreshSwitchDetail(uid);
	return currentSwitchDetail;
}

async function toggleSwitchWithAccessKey(uid, apiKey) {
	if (!uid || !apiKey) {
		throw new Error('Access key required');
	}
	const response = await fetch(`${API_BASE_URL}/v2/switch/${uid}/toggle`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			'X-Api-Key': apiKey
		},
		body: JSON.stringify({})
	});
	const data = await response.json();
	if (!response.ok || !data.success) {
		throw new Error(data.error || 'Failed to toggle switch');
	}
	if (data.data) {
		handleStateUpdate(uid, data.data.state, data.data.timestamp);
		if (currentSwitchDetail && currentSwitchDetail.uid === uid && typeof data.data.toggleCount === 'number') {
			currentSwitchDetail.toggleCount = data.data.toggleCount;
			if (detailToggles) detailToggles.textContent = `${data.data.toggleCount} toggles`;
		}
	}
	return data.data;
}

function updateDetailActivity(detail) {
	if (!detail) return;

	if (detailUsers) detailUsers.textContent = `${detail.userCount || 0} user${(detail.userCount || 0) === 1 ? '' : 's'}`;
	if (detailToggles) detailToggles.textContent = `${detail.toggleCount || 0} toggles`;
	if (detailLastChange) detailLastChange.textContent = detail.lastToggled ? formatTimeAgo(new Date(detail.lastToggled)) : 'Never';

	// If the icon/banner/description changes, update visible pieces too
	if (detailTitle) detailTitle.textContent = getDisplaySwitchName(detail.description) || 'Untitled switch';
	if (detailIcon) {
		const icon = String(detail.iconUrl || '').trim();
		if (icon) {
			detailIcon.src = icon;
			detailIcon.classList.remove('hidden');
		} else {
			detailIcon.removeAttribute('src');
			detailIcon.classList.add('hidden');
		}
	}

	renderEvents(detail.events || []);
	if (toggleDialog && !toggleDialog.classList.contains('hidden')) {
		renderToggleDialogActivity(detail);
	}
	updateCommentStateVisibility();
}

function syncSwitchDetailToIndex(detail) {
	if (!detail || !detail.uid) return;
	const idx = allSwitches.findIndex((sw) => sw && sw.uid === detail.uid);
	if (idx >= 0) {
		allSwitches[idx] = {
			...allSwitches[idx],
			state: detail.state,
			lastToggled: detail.lastToggled,
			toggleCount: detail.toggleCount,
			userCount: detail.userCount,
			iconUrl: detail.iconUrl || allSwitches[idx].iconUrl,
			bannerUrl: detail.bannerUrl || allSwitches[idx].bannerUrl,
			description: detail.description || allSwitches[idx].description,
			location: detail.location || allSwitches[idx].location,
			category: detail.category || allSwitches[idx].category
		};
		updateRenderedSwitchCards();
		updateStats();
	}
}

function connectRealtime(initialUid) {
	if (typeof window.WebSocket !== 'function') return;

	const uid = String(initialUid || '').trim();
	if (!uid) return;

	const base = resolveWebSocketBaseUrl();
	if (!base) return;

	// If already connected, keep it.
	if (wsClient && (wsClient.readyState === 0 || wsClient.readyState === 1)) {
		return;
	}

	const wsUrl = `${base}?uid=${encodeURIComponent(uid)}`;
	try {
		wsClient = new window.WebSocket(wsUrl);
	} catch (err) {
		console.warn('Failed to create WebSocket:', err);
		wsClient = null;
		return;
	}

	wsClient.onopen = () => {
		wsReconnectAttempts = 0;
		clearWsReconnectTimer();
		// We'll subscribe after open via ensureRealtimeSubscriptions()
		ensureRealtimeSubscriptions();
	};

	wsClient.onmessage = (event) => {
		try {
			const raw = event && event.data ? String(event.data) : '';
			if (!raw) return;
			const msg = JSON.parse(raw);
			if (msg && msg.type === 'state_update') {
				handleStateUpdate(msg.uid, msg.state, msg.timestamp);
			}
		} catch (err) {
			console.warn('Bad WS message:', err);
		}
	};

	wsClient.onclose = () => {
		wsClient = null;
		scheduleWsReconnect(uid);
	};

	wsClient.onerror = () => {
		// Let onclose handle reconnect
	};
}

function ensureRealtimeSubscriptions() {
	// Subscribe to the switches that are currently visible (plus current detail).
	const desired = new Set(getRenderedSwitchUids());
	if (currentSwitchId) desired.add(currentSwitchId);

	const desiredList = Array.from(desired).filter(Boolean);
	if (desiredList.length === 0) {
		disconnectRealtime();
		return;
	}

	// Ensure connection (server requires a uid query param)
	connectRealtime(desiredList[0]);

	// If WS not ready yet, we'll subscribe on open.
	if (!wsClient || wsClient.readyState !== 1) return;

	// Subscribe new
	desiredList.forEach((uid) => {
		if (wsSubscribedUids.has(uid)) return;
		if (wsSendJson({ type: 'subscribe', uid })) {
			wsSubscribedUids.add(uid);
		}
	});

	// Unsubscribe old
	Array.from(wsSubscribedUids).forEach((uid) => {
		if (desired.has(uid)) return;
		wsSendJson({ type: 'unsubscribe', uid });
		wsSubscribedUids.delete(uid);
	});
}

async function loadCategories() {
	try {
		const response = await fetch(`${API_BASE_URL}/categories`);
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}: ${response.statusText}`);
		}
		const data = await response.json();
		if (!data.success) {
			throw new Error(data.error || 'Failed to load categories');
		}
		categories = data.data || {};
		renderCategories();
	} catch (error) {
		console.warn('Categories unavailable:', error);
		categoryList.innerHTML = '<p class="text-muted">Categories unavailable.</p>';
	}
}

function renderCategories() {
	if (!categories || Object.keys(categories).length === 0) {
		categoryList.innerHTML = '<p class="text-muted">No categories yet.</p>';
		return;
	}

	categoryList.innerHTML = Object.entries(categories)
		.sort((a, b) => b[1] - a[1])
		.map(([name, count]) => `
			<button class="category-chip" onclick="filterByCategory('${encodeURIComponent(name)}')">
				<span>${escapeHtml(name)}</span>
				<span class="chip-count">${count}</span>
			</button>
		`).join('');
}

function showLoading() {
	hideMessages();
	loadingMessage.style.display = 'block';
}

function showError() {
	hideMessages();
	errorMessage.style.display = 'block';
}

function showEmptyMessage() {
	hideMessages();
	emptySwitches.style.display = 'block';
}

function hideMessages() {
	loadingMessage.style.display = 'none';
	errorMessage.style.display = 'none';
	emptySwitches.style.display = 'none';
}

function updateStats() {
	const total = allSwitches.length;
	const active = allSwitches.filter(sw => sw.state).length;
	totalSwitches.textContent = total;
	activeSwitches.textContent = active;
}

function applyFilters(options = {}) {
	const shouldRender = options.render !== false;
	filteredSwitches = computeFilteredSwitches();
	if (shouldRender) {
		renderSwitches();
	}
}

function renderSwitches() {
	if (filteredSwitches.length === 0 && allSwitches.length > 0) {
		switchesGrid.innerHTML = `
			<div class="empty-message" style="grid-column: 1 / -1;">
				<h3>🔍 No Matching Switches</h3>
				<p>No switches match your search criteria. Try adjusting your filters.</p>
			</div>
		`;
		return;
	}

	switchesGrid.innerHTML = filteredSwitches.map(createSwitchCard).join('');
	wireSwitchCardClicks();
}

function setQuickViewBanner(bannerUrl) {
	if (!quickView) return;
	const url = String(bannerUrl || '').trim();
	if (!url) {
		quickView.style.removeProperty('--quickview-banner-image');
		return;
	}
	quickView.style.setProperty('--quickview-banner-image', `url("${cssEscapeUrl(url)}")`);
}

function showQuickView() {
	if (!quickView) return;
	quickView.classList.remove('hidden');
	document.body.classList.add('modal-open');
	quickView.setAttribute('aria-hidden', 'false');
}

function closeQuickView() {
	if (!quickView) return;
	quickView.classList.add('hidden');
	document.body.classList.remove('modal-open');
	quickView.setAttribute('aria-hidden', 'true');

	setQuickViewBanner('');
	quickViewUid = null;
	quickViewDetail = null;

	if (quickViewIcon) {
		quickViewIcon.removeAttribute('src');
		quickViewIcon.classList.add('hidden');
	}
	if (quickViewTitle) quickViewTitle.textContent = 'Switch details';
	if (quickViewSubtitle) quickViewSubtitle.textContent = '';
	if (quickViewMeta) quickViewMeta.innerHTML = '';
}

function showHaDialog() {
	if (!haDialog) return;
	haDialog.classList.remove('hidden');
	document.body.classList.add('modal-open');
	haDialog.setAttribute('aria-hidden', 'false');
}

function closeHaDialog() {
	if (!haDialog) return;
	haDialog.classList.add('hidden');
	document.body.classList.remove('modal-open');
	haDialog.setAttribute('aria-hidden', 'true');
	if (haDialogCode) haDialogCode.value = '';
}

function openHaDialogForUid(uid) {
	const trimmed = String(uid || '').trim();
	if (!trimmed) return;
	if (haDialogCode) haDialogCode.value = trimmed;
	if (haDialogOpenLink) haDialogOpenLink.href = HOME_ASSISTANT_CONFIG_FLOW_URL;
	if (haDialogBadgeLink) haDialogBadgeLink.href = HOME_ASSISTANT_CONFIG_FLOW_URL;
	showHaDialog();
}

function showHacsDialog() {
	if (!hacsDialog) return;
	hacsDialog.classList.remove('hidden');
	document.body.classList.add('modal-open');
	hacsDialog.setAttribute('aria-hidden', 'false');
}

function closeHacsDialog() {
	if (!hacsDialog) return;
	hacsDialog.classList.add('hidden');
	document.body.classList.remove('modal-open');
	hacsDialog.setAttribute('aria-hidden', 'true');
}

function openHacsDialog() {
	if (hacsDialogRepo) hacsDialogRepo.value = HACS_REPO_URL;
	if (hacsDialogOpenLink) hacsDialogOpenLink.href = HACS_ADD_URL;
	if (hacsDialogBadgeLink) hacsDialogBadgeLink.href = HACS_ADD_URL;
	showHacsDialog();
}

function showAuthDialog() {
	if (!authDialog) return;
	authDialog.classList.remove('hidden');
	document.body.classList.add('modal-open');
	authDialog.setAttribute('aria-hidden', 'false');
}

function closeAuthDialog() {
	if (!authDialog) return;
	authDialog.classList.add('hidden');
	document.body.classList.remove('modal-open');
	authDialog.setAttribute('aria-hidden', 'true');
	if (authDialogStatus) {
		authDialogStatus.textContent = '';
		authDialogStatus.className = 'comment-status';
	}
	if (authDialogKeyInput) authDialogKeyInput.value = '';
	pendingAuthToggle = false;
}

function openAuthDialog(shouldToggleAfter = false, message = '') {
	if (!currentSwitchId) return;
	pendingAuthToggle = Boolean(shouldToggleAfter);
	if (authDialogStatus) {
		authDialogStatus.textContent = message || '';
		authDialogStatus.className = message ? 'comment-status error' : 'comment-status';
	}
	showAuthDialog();
}

function applyAuthDialog() {
	if (!currentSwitchId) return;
	const key = authDialogKeyInput ? String(authDialogKeyInput.value || '').trim() : '';
	if (!key) {
		if (authDialogStatus) {
			authDialogStatus.textContent = 'Access key required.';
			authDialogStatus.className = 'comment-status error';
		}
		return;
	}
	authDialogStatus.textContent = 'Checking key...';
	authDialogStatus.className = 'comment-status';
	(async () => {
		const result = await detectManagementPermission(currentSwitchId, key);
		if (!result.valid) {
			authDialogStatus.textContent = result.error || 'Invalid access key.';
			authDialogStatus.className = 'comment-status error';
			return;
		}
		setStoredManagementKey(currentSwitchId, '');
		setStoredApiKey(currentSwitchId, '');
		if (result.isManagement) {
			setStoredManagementKey(currentSwitchId, key);
		} else {
			setStoredApiKey(currentSwitchId, key);
		}
		togglePermissionDeniedByUid[currentSwitchId] = false;
		updateAuthPanel(currentSwitchDetail || { uid: currentSwitchId });
		updateAuthGates(currentSwitchDetail || { uid: currentSwitchId });
		updateHeroStatusButton(currentSwitchDetail || {});
		updateCommentStateVisibility();
		updateManagePanel(currentSwitchDetail || { uid: currentSwitchId });
		setAuthStatus('Authenticated.', false);
		const shouldToggle = pendingAuthToggle;
		closeAuthDialog();

		if (shouldToggle) {
			openToggleDialogForDetail(currentSwitchDetail || { uid: currentSwitchId });
		}
	})();
}

function showToggleDialog() {
	if (!toggleDialog) return;
	toggleDialog.classList.remove('hidden');
	document.body.classList.add('modal-open');
	toggleDialog.setAttribute('aria-hidden', 'false');
}

function closeToggleDialog() {
	if (!toggleDialog) return;
	toggleDialog.classList.add('hidden');
	document.body.classList.remove('modal-open');
	toggleDialog.setAttribute('aria-hidden', 'true');
	if (toggleDialogStatus) {
		toggleDialogStatus.textContent = '';
		toggleDialogStatus.className = 'comment-status';
	}
}

function updateToggleDialogStateLabel(state) {
	if (!toggleDialogState) return;
	if (typeof state !== 'boolean') {
		toggleDialogState.textContent = 'Status: Unknown';
		return;
	}
	toggleDialogState.textContent = `Status: ${state ? 'ON' : 'OFF'}`;
}

function openToggleDialogForDetail(detail) {
	if (!toggleDialog) return;
	const stateKnown = typeof detail?.state === 'boolean';
	if (toggleDialogSwitch) {
		toggleDialogSwitch.checked = stateKnown ? detail.state : false;
	}
	updateToggleDialogStateLabel(stateKnown ? detail.state : null);
	renderToggleDialogActivity(detail);
	if (toggleDialogStatus) {
		if (!hasAccessKey(detail?.uid)) {
			toggleDialogStatus.textContent = 'Authenticate to toggle.';
			toggleDialogStatus.className = 'comment-status error';
		} else {
			toggleDialogStatus.textContent = '';
			toggleDialogStatus.className = 'comment-status';
		}
	}
	showToggleDialog();
}

async function applyToggleDialog() {
	if (!currentSwitchId || heroToggleInFlight) return;
	if (!toggleDialogSwitch) return;

	const uid = currentSwitchId;
	const desiredState = Boolean(toggleDialogSwitch.checked);
	const detail = await ensureCurrentSwitchDetail(uid);
	const currentState = (detail && typeof detail.state === 'boolean') ? detail.state : null;
	if (currentState == null) {
		if (toggleDialogStatus) {
			toggleDialogStatus.textContent = 'Current state unavailable.';
			toggleDialogStatus.className = 'comment-status error';
		}
		return;
	}
	const previousState = currentState;
	const apiKey = getActiveApiKey(uid);
	const managementKey = getActiveManagementKey(uid);
	const primaryKey = apiKey || managementKey;
	const fallbackKey = (primaryKey === apiKey) ? managementKey : apiKey;
	if (!primaryKey) {
		if (toggleDialogStatus) {
			toggleDialogStatus.textContent = 'Access key required.';
			toggleDialogStatus.className = 'comment-status error';
		}
		return;
	}

	if (currentState === desiredState) {
		if (toggleDialogStatus) {
			toggleDialogStatus.textContent = 'Already in the requested state.';
			toggleDialogStatus.className = 'comment-status';
		}
		return;
	}

	heroToggleInFlight = true;
	if (toggleDialogStatus) {
		toggleDialogStatus.textContent = 'Toggling...';
		toggleDialogStatus.className = 'comment-status';
	}
	try {
		await toggleSwitchWithAccessKey(uid, primaryKey);
		togglePermissionDeniedByUid[uid] = false;
		updateToggleDialogStateLabel(desiredState);
		updateAuthPanel(detail);
		updateAuthGates(detail);
		updateHeroStatusButton(currentSwitchDetail || {});
		updateCommentStateVisibility();
		if (toggleDialogStatus) {
			toggleDialogStatus.textContent = 'Updated.';
			toggleDialogStatus.className = 'comment-status success';
		}
	} catch (error) {
		if (toggleDialogSwitch) {
			toggleDialogSwitch.checked = previousState;
		}
		updateToggleDialogStateLabel(previousState);
		let message = error && error.message ? error.message : 'Failed to toggle.';
		if (/insufficient permissions/i.test(message) && fallbackKey) {
			try {
				await toggleSwitchWithAccessKey(uid, fallbackKey);
				togglePermissionDeniedByUid[uid] = false;
				if (toggleDialogSwitch) {
					toggleDialogSwitch.checked = desiredState;
				}
				updateToggleDialogStateLabel(desiredState);
				updateAuthPanel(detail);
				updateAuthGates(detail);
				updateHeroStatusButton(currentSwitchDetail || {});
				updateCommentStateVisibility();
				if (toggleDialogStatus) {
					toggleDialogStatus.textContent = 'Updated.';
					toggleDialogStatus.className = 'comment-status success';
				}
				return;
			} catch (fallbackError) {
				message = fallbackError && fallbackError.message ? fallbackError.message : message;
			}
		}
		if (/insufficient permissions/i.test(message)) {
			togglePermissionDeniedByUid[uid] = true;
			message = 'Key missing toggle permission. Generate a new management/API key with toggle access.';
		}
		if (toggleDialogStatus) {
			toggleDialogStatus.textContent = message;
			toggleDialogStatus.className = 'comment-status error';
		}
	} finally {
		heroToggleInFlight = false;
	}
}

async function copyAndOpenHomeAssistant(uid, button, defaultLabel) {
	const trimmed = String(uid || '').trim();
	if (!trimmed) return;
	if (button) {
		copyText(trimmed, button, defaultLabel || button.textContent || 'Copy and Open');
	} else {
		try {
			await navigator.clipboard.writeText(trimmed);
		} catch {
			// ignore
		}
	}
	try {
		window.open(HOME_ASSISTANT_CONFIG_FLOW_URL, '_blank', 'noopener');
	} catch {
		// ignore
	}
}

function renderQuickView(detail) {
	if (!detail) return;
	if (quickViewTitle) quickViewTitle.textContent = getDisplaySwitchName(detail.description) || 'Untitled switch';
	if (quickViewSubtitle) {
		quickViewSubtitle.textContent = detail.location ? `📍 ${detail.location}` : '';
	}
	if (quickViewIcon) {
		const icon = String(detail.iconUrl || '').trim();
		if (icon) {
			quickViewIcon.src = icon;
			quickViewIcon.classList.remove('hidden');
		} else {
			quickViewIcon.removeAttribute('src');
			quickViewIcon.classList.add('hidden');
		}
	}
	setQuickViewBanner(detail.bannerUrl);

	if (quickViewMeta) {
		const category = escapeHtml(detail.category || 'Other');
		const users = typeof detail.userCount === 'number' ? detail.userCount : (detail.userCount || 0);
		const toggles = typeof detail.toggleCount === 'number' ? detail.toggleCount : (detail.toggleCount || 0);
		const last = detail.lastToggled ? formatTimeAgo(new Date(detail.lastToggled)) : 'Never';

		quickViewMeta.innerHTML = `
			<div class="meta-item">
				<span class="meta-label">Category</span>
				<span class="meta-value">${category}</span>
			</div>
			<div class="meta-item">
				<span class="meta-label">Users</span>
				<span class="meta-value">${users} user${users === 1 ? '' : 's'}</span>
			</div>
			<div class="meta-item">
				<span class="meta-label">Toggle count</span>
				<span class="meta-value">${toggles} toggles</span>
			</div>
			<div class="meta-item">
				<span class="meta-label">Last change</span>
				<span class="meta-value">${escapeHtml(last)}</span>
			</div>
			<div class="meta-item">
				<span class="meta-label">UID</span>
				<span class="meta-value mono">${escapeHtml(detail.uid)}</span>
			</div>
		`;
	}
}

async function openQuickView(uid, previewBannerUrl = '') {
	if (!uid) return;
	if (!quickView) {
		await openSwitchDetails(uid, false);
		return;
	}

	quickViewUid = uid;
	quickViewDetail = null;
	if (quickViewTitle) quickViewTitle.textContent = 'Loading…';
	if (quickViewSubtitle) quickViewSubtitle.textContent = '';
	if (quickViewMeta) quickViewMeta.innerHTML = '';
	if (quickViewIcon) {
		quickViewIcon.removeAttribute('src');
		quickViewIcon.classList.add('hidden');
	}
	setQuickViewBanner(previewBannerUrl);
	showQuickView();

	try {
		const response = await fetch(`${API_BASE_URL}/switch/${uid}`);
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}: ${response.statusText}`);
		}
		const data = await response.json();
		if (!data.success) {
			throw new Error(data.error || 'Failed to load switch detail');
		}

		quickViewDetail = data.data;
		renderQuickView(data.data);
	} catch (error) {
		console.error('Error loading quick view:', error);
		if (quickViewTitle) quickViewTitle.textContent = 'Unable to load switch';
		if (quickViewSubtitle) quickViewSubtitle.textContent = 'Please try again.';
	}
}

function wireSwitchCardClicks() {
	if (!switchesGrid) return;
	const cards = Array.from(switchesGrid.querySelectorAll('.switch-card[data-uid]'));
	cards.forEach((card) => {
		if (!card || card.dataset.wired === '1') return;
		card.dataset.wired = '1';
		card.addEventListener('click', (event) => {
			const interactive = event.target && event.target.closest
				? event.target.closest('button, a, input, select, textarea, label')
				: null;
			if (interactive) return;

			const uid = String(card.dataset.uid || '').trim();
			if (!uid) return;
			const bannerUrl = String(card.dataset.bannerUrl || '').trim();
			openQuickView(uid, bannerUrl);
		});
	});
}

function createSwitchCard(switchData) {
	const {
		uid,
		description,
		location,
		category,
		state,
		lastToggled,
		userCount,
		toggleCount,
		link,
		iconUrl,
		bannerUrl
	} = switchData;

	const stateClass = state ? 'state-on' : 'state-off';
	const stateText = state ? 'on' : 'off';
	const stateLabel = state ? 'ON' : 'OFF';
	const lastToggledText = lastToggled ? formatTimeAgo(new Date(lastToggled)) : 'Never';
	const safeCategory = escapeHtml(category || 'Other');
	const usersLabel = typeof userCount === 'number' ? `${userCount} user${userCount === 1 ? '' : 's'}` : '0 users';
	const togglesLabel = typeof toggleCount === 'number' ? `${toggleCount} toggles` : '0 toggles';
	const webLink = link ? `<a class="inline-link" href="${escapeAttr(link)}" target="_blank" rel="noopener">🌐 Link</a>` : '';
	const iconSrc = String(iconUrl || '').trim();
	const iconHtml = `<img class="switch-icon ${iconSrc ? '' : 'hidden'}" data-field="icon" ${iconSrc ? `src="${escapeAttr(iconSrc)}"` : ''} alt="" loading="lazy" referrerpolicy="no-referrer">`;

	return `
		<div class="switch-card ${stateClass}" data-uid="${escapeAttr(uid)}" data-banner-url="${escapeAttr(bannerUrl || '')}">
			<div class="switch-header">
				<div class="switch-title">
					${iconHtml}
					<div class="switch-description" data-field="description">${escapeHtml(getDisplaySwitchName(description) || 'Untitled Switch')}</div>
				</div>
				<div class="switch-state ${stateText}" data-field="stateBadge">${stateLabel}</div>
			</div>

			<div class="switch-details">
				${location ? `
					<div class="switch-detail">
						<span class="switch-detail-label">📍 Location:</span>
						<span class="switch-detail-value">${escapeHtml(location)}</span>
					</div>
				` : ''}

				<div class="switch-detail">
					<span class="switch-detail-label">🏷️ Category:</span>
					<button class="chip-link" onclick="filterByCategory('${encodeURIComponent(category || 'Other')}')">${safeCategory}</button>
				</div>

				<div class="switch-detail">
					<span class="switch-detail-label">👥 Users:</span>
					<span class="switch-detail-value" data-field="users">${usersLabel}</span>
				</div>

				<div class="switch-detail">
					<span class="switch-detail-label">🔢 Toggles:</span>
					<span class="switch-detail-value" data-field="toggles">${togglesLabel}</span>
				</div>

				<div class="switch-detail">
					<span class="switch-detail-label">🕒 Last Changed:</span>
					<span class="switch-detail-value" data-field="lastChanged" data-ts="${lastToggled ? String(lastToggled) : ''}">${lastToggledText}</span>
				</div>

				<div class="switch-detail">
					<span class="switch-detail-label">🆔 UID:</span>
					<span class="switch-detail-value mono small">${uid.substring(0, 8)}...</span>
				</div>

				${webLink ? `
				<div class="switch-detail">
					<span class="switch-detail-label">🔗 Website:</span>
					<span class="switch-detail-value">${webLink}</span>
				</div>` : ''}
			</div>

			<div class="switch-actions">
				<button class="copy-uid-btn" onclick="copyUID('${uid}', this)">
					📋 Copy UID
				</button>
				<button class="view-details-btn" onclick="openSwitchDetails('${uid}')">
					👁️ Details
				</button>
			</div>
		</div>
	`;
}

function copyUID(uid, button) {
	copyText(uid, button, '📋 Copy UID');
}

function copyText(value, button, defaultLabel) {
	navigator.clipboard.writeText(value).then(() => {
		const originalText = button.textContent;
		button.textContent = '✅ Copied!';
		button.classList.add('copied');

		setTimeout(() => {
			button.textContent = originalText || defaultLabel;
			button.classList.remove('copied');
		}, 2000);
	}).catch((err) => {
		console.error('Failed to copy text:', err);

		const textArea = document.createElement('textarea');
		textArea.value = value;
		document.body.appendChild(textArea);
		textArea.select();

		try {
			document.execCommand('copy');
			button.textContent = '✅ Copied!';
			button.classList.add('copied');

			setTimeout(() => {
				button.textContent = defaultLabel;
				button.classList.remove('copied');
			}, 2000);
		} catch (error) {
			alert('Failed to copy. Please copy manually.');
		}

		document.body.removeChild(textArea);
	});
}

async function openSwitchDetails(uid, fromPopState = false, options = {}) {
	try {
		const allowRedirect = options.allowRedirect !== false;
		const redirectFrom = options.redirectFrom || '';
		const redirectReason = options.redirectReason || '';

		if (document?.body) {
			document.body.classList.add('view-switch');
		}

		// Apply banner ASAP (from list data) to make expansion feel instant
		const preview = allSwitches.find(sw => sw.uid === uid);
		if (preview && preview.bannerUrl) {
			setHeroBanner(preview.bannerUrl);
		}

		currentSwitchId = uid;
		const response = await fetch(`${API_BASE_URL}/switch/${uid}`);
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}: ${response.statusText}`);
		}
		const data = await response.json();
		if (!data.success) {
			throw new Error(data.error || 'Failed to load switch detail');
		}

		const detail = data.data || {};
		if (detail.redirect) {
			if (allowRedirect && detail.redirectTo) {
				await openSwitchDetails(detail.redirectTo, fromPopState, {
					allowRedirect: false,
					redirectFrom: uid,
					redirectReason: detail.redirectReason
				});
				return;
			}
			detailSection.classList.remove('hidden');
			showRedirectNotice(uid, detail.redirectTo, detail.redirectReason);
			if (!fromPopState) {
				pushSwitchQuery(uid);
			}
			return;
		}

		if (redirectFrom) {
			detail.redirectFrom = redirectFrom;
			detail.redirectReason = redirectReason;
		}

		detailSection.classList.remove('hidden');
		renderSwitchDetail(detail);
		ensureRealtimeSubscriptions();
		if (!fromPopState) {
			pushSwitchQuery(uid);
		}
	} catch (error) {
		console.error('Error loading switch detail:', error);
		if (options && options.redirectFrom) {
			detailSection.classList.remove('hidden');
			showRedirectNotice(options.redirectFrom, uid, options.redirectReason);
			return;
		}
		alert('Unable to load switch details. Please try again.');
		closeDetail();
	}
}

function renderSwitchDetail(detail) {
	currentSwitchDetail = detail;
	updateAdminPanelVisibility();
	if (detail && detail.redirectFrom) {
		showRedirectNotice(detail.redirectFrom, detail.uid, detail.redirectReason);
	} else {
		hideRedirectNotice();
	}
	detailTitle.textContent = getDisplaySwitchName(detail.description) || 'Untitled switch';
	if (detailIcon) {
		const icon = String(detail.iconUrl || '').trim();
		if (icon) {
			detailIcon.src = icon;
			detailIcon.classList.remove('hidden');
		} else {
			detailIcon.removeAttribute('src');
			detailIcon.classList.add('hidden');
		}
	}
	setHeroBanner(detail.bannerUrl);
	setHeroForSwitch(detail);
	detailLocation.textContent = detail.location ? `📍 ${detail.location}` : '📍 Not specified';
	detailCategory.textContent = detail.category || 'Other';
	detailUsers.textContent = `${detail.userCount || 0} user${(detail.userCount || 0) === 1 ? '' : 's'}`;
	detailToggles.textContent = `${detail.toggleCount || 0} toggles`;
	detailLastChange.textContent = detail.lastToggled ? formatTimeAgo(new Date(detail.lastToggled)) : 'Never';
	detailUid.textContent = detail.uid;

	updateAuthPanel(detail);
	updateAuthGates(detail);
	updateManagePanel(detail);

	if (detail.link) {
		switchWebLink.href = detail.link;
		switchWebLink.classList.remove('hidden');
	} else {
		switchWebLink.href = '#';
		switchWebLink.classList.add('hidden');
	}

	if (detail.ownerProfileUrl) {
		ownerWebLink.href = detail.ownerProfileUrl;
		ownerWebLink.classList.remove('hidden');
	} else {
		ownerWebLink.href = '#';
		ownerWebLink.classList.add('hidden');
	}

	renderEvents(detail.events || []);
	if (toggleDialog && !toggleDialog.classList.contains('hidden')) {
		renderToggleDialogActivity(detail);
	}
}

function revokeObjectUrl(value) {
	if (!value) return;
	try {
		if (typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
			URL.revokeObjectURL(value);
		}
	} catch {
		// ignore
	}
}

function setMediaPreviewImage(imgEl, placeholderEl, url) {
	if (!imgEl) return;
	const value = String(url || '').trim();
	if (value) {
		imgEl.src = value;
		imgEl.classList.remove('hidden');
		if (placeholderEl) placeholderEl.classList.add('hidden');
		return;
	}
	imgEl.removeAttribute('src');
	imgEl.classList.add('hidden');
	if (placeholderEl) placeholderEl.classList.remove('hidden');
}

function setManageIconEditVisible(visible) {
	if (!manageIconEdit) return;
	if (visible) manageIconEdit.classList.remove('hidden');
	else manageIconEdit.classList.add('hidden');
}

function setManageBannerEditVisible(visible) {
	if (!manageBannerEdit) return;
	if (visible) manageBannerEdit.classList.remove('hidden');
	else manageBannerEdit.classList.add('hidden');
}

function resetManageMediaPickers(detail) {
	manageIconAction = 'keep';
	manageBannerAction = 'keep';

	manageCurrentIconUrl = detail && detail.iconUrl ? String(detail.iconUrl) : '';
	manageCurrentBannerUrl = detail && detail.bannerUrl ? String(detail.bannerUrl) : '';

	revokeObjectUrl(manageIconObjectUrl);
	revokeObjectUrl(manageBannerObjectUrl);
	manageIconObjectUrl = null;
	manageBannerObjectUrl = null;

	if (manageIconUrlInput) manageIconUrlInput.value = '';
	if (manageBannerUrlInput) manageBannerUrlInput.value = '';
	if (manageIconFileInput) manageIconFileInput.value = '';
	if (manageBannerFileInput) manageBannerFileInput.value = '';

	setManageIconEditVisible(false);
	setManageBannerEditVisible(false);

	setMediaPreviewImage(manageIconPreview, manageIconPlaceholder, manageCurrentIconUrl);
	setMediaPreviewImage(manageBannerPreview, manageBannerPlaceholder, manageCurrentBannerUrl);
}

function updateAuthPanel(detail) {
	if (!authPanel || !detail) return;
	const uid = detail.uid;
	const hasManagement = Boolean(getActiveManagementKey(uid));
	const hasApi = Boolean(getActiveApiKey(uid));
	const authed = hasManagement || hasApi;

	if (authLoggedOut) authLoggedOut.classList.toggle('hidden', authed);
	if (authLoggedIn) authLoggedIn.classList.toggle('hidden', !authed);

	if (authStatusText) {
		if (!authed) {
			authStatusText.textContent = '';
		} else if (hasManagement && hasApi) {
			authStatusText.textContent = 'Authenticated with management + API keys (session only).';
		} else if (hasManagement) {
			authStatusText.textContent = 'Authenticated with management key (session only).';
		} else {
			authStatusText.textContent = 'Authenticated with API key (session only).';
		}
	}

	if (authStatus) {
		authStatus.textContent = '';
		authStatus.className = 'comment-status hidden';
	}

	updateNavSwitchStatus(detail);
}

function setAuthStatus(message, isError = false) {
	if (!authStatus) return;
	if (!message || !isError) {
		authStatus.textContent = '';
		authStatus.className = 'comment-status hidden';
		return;
	}
	authStatus.textContent = message;
	authStatus.className = 'comment-status error';
}

function updateAuthGates(detail) {
	if (!detail) return;
	const hasAny = hasAccessKey(detail.uid);
	const hasManagement = Boolean(getActiveManagementKey(detail.uid));
	if (commentSection) commentSection.classList.toggle('hidden', !hasAny);
	if (commentForm) commentForm.classList.toggle('hidden', !hasAny);
	if (managePanel) managePanel.classList.toggle('hidden', !hasManagement);
}

function updateManagePanel(detail) {
	if (!managePanel) return;
	if (!detail || !detail.uid) return;
	const hasManagement = Boolean(getActiveManagementKey(detail.uid));
	managePanel.classList.toggle('hidden', !hasManagement);
	if (!hasManagement) {
		return;
	}

	if (manageLinkInput) manageLinkInput.value = detail.link || '';
	resetManageMediaPickers(detail);

	// If a management key was supplied via #accessKey=..., scroll to this panel once
	const wantScroll = Boolean(
		managementAutoscrollUid
		&& String(managementAutoscrollUid).toLowerCase() === String(detail.uid || '').toLowerCase()
	);
	if (wantScroll) {
		managementAutoscrollUid = null;
		if (manageStatus) {
			manageStatus.textContent = 'Management key loaded — edit fields below and click “Save appearance”.';
			manageStatus.className = 'comment-status success';
		}
		// Give the browser a chance to lay out the newly-shown detail section before scrolling.
		const doScroll = () => {
			try {
				managePanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
			} catch {
				// ignore
			}
		};
		if (typeof window.requestAnimationFrame === 'function') {
			window.requestAnimationFrame(() => window.requestAnimationFrame(doScroll));
		} else {
			setTimeout(doScroll, 50);
		}
	}

	updateHeroStatusButton(detail);
}

async function submitManageAppearance() {
	if (!currentSwitchId) return;
	if (!manageStatus) return;

	const apiKey = getActiveManagementKey(currentSwitchId) || getStoredManagementKey(currentSwitchId);
	if (!apiKey) {
		manageStatus.textContent = 'Access key required.';
		manageStatus.className = 'comment-status error';
		return;
	}

	const updates = {};
	const detail = currentSwitchDetail || {};

	const link = String(manageLinkInput?.value || '').trim();
	if (link !== String(detail.link || '')) {
		updates.link = link;
	}

	const iconFile = manageIconFileInput?.files?.[0] || null;
	const iconUrl = String(manageIconUrlInput?.value || '').trim();
	let iconMode = manageIconAction;
	if (iconMode === 'keep' && (iconFile || iconUrl)) {
		iconMode = 'replace';
	}
	if (iconMode === 'remove') {
		updates.iconUrl = '';
	} else if (iconMode === 'replace') {
		if (!iconFile && !iconUrl) {
			manageStatus.textContent = 'Icon: please choose a file or enter a URL (or click Cancel).';
			manageStatus.className = 'comment-status error';
			return;
		}
		if (!iconFile) {
			updates.iconUrl = iconUrl;
		}
	}

	const bannerFile = manageBannerFileInput?.files?.[0] || null;
	const bannerUrl = String(manageBannerUrlInput?.value || '').trim();
	let bannerMode = manageBannerAction;
	if (bannerMode === 'keep' && (bannerFile || bannerUrl)) {
		bannerMode = 'replace';
	}
	if (bannerMode === 'remove') {
		updates.bannerUrl = '';
	} else if (bannerMode === 'replace') {
		if (!bannerFile && !bannerUrl) {
			manageStatus.textContent = 'Banner: please choose a file or enter a URL (or click Cancel).';
			manageStatus.className = 'comment-status error';
			return;
		}
		if (!bannerFile) {
			updates.bannerUrl = bannerUrl;
		}
	}

	const hasFileUpload = Boolean(iconFile || bannerFile);
	if (Object.keys(updates).length === 0 && !hasFileUpload) {
		manageStatus.textContent = 'No changes to save.';
		manageStatus.className = 'comment-status';
		return;
	}

	manageStatus.textContent = 'Saving...';
	manageStatus.className = 'comment-status';

	try {
		const form = new FormData();
		Object.entries(updates).forEach(([k, v]) => {
			form.append(k, v == null ? '' : String(v));
		});
		if (iconFile) {
			form.append('iconFile', iconFile, iconFile.name || 'icon');
		}
		if (bannerFile) {
			form.append('bannerFile', bannerFile, bannerFile.name || 'banner');
		}

		const response = await fetch(`${API_BASE_URL}/v2/switch/${currentSwitchId}/metadata`, {
			method: 'POST',
			headers: {
				'X-Api-Key': apiKey
			},
			body: form
		});

		const data = await response.json();
		if (!response.ok || !data.success) {
			throw new Error((data && data.error) ? data.error : `HTTP ${response.status}`);
		}

		setStoredManagementKey(currentSwitchId, apiKey);

		if (data.data) {
			renderSwitchDetail(data.data);
		}

		resetManageMediaPickers(currentSwitchDetail || {});
		updateAuthPanel(currentSwitchDetail || detail);
		updateAuthGates(currentSwitchDetail || detail);

		manageStatus.textContent = 'Saved.';
		manageStatus.className = 'comment-status success';
	} catch (error) {
		console.error('Error saving appearance:', error);
		manageStatus.textContent = `Failed: ${error.message || 'Unknown error'}`;
		manageStatus.className = 'comment-status error';
	}
}

function renderEvents(events) {
	if (!events.length) {
		detailEvents.innerHTML = '<li class="timeline-empty">No history yet.</li>';
		return;
	}

	detailEvents.innerHTML = events.map((event) => {
		const timeText = event.timestamp ? new Date(event.timestamp).toLocaleString() : 'Unknown time';
		if (event.type === 'comment') {
			return `
				<li class="timeline-item">
					<div class="timeline-dot comment"></div>
					<div class="timeline-content">
						<div class="timeline-head">
							<span class="timeline-type">Comment</span>
							<span class="timeline-time">${timeText}</span>
						</div>
						<p class="timeline-actor">${escapeHtml(event.actor || 'user')}</p>
						<p>${escapeHtml(event.comment || '')}</p>
					</div>
				</li>
			`;
		}

		const stateLabel = event.state ? 'turned ON' : 'turned OFF';
		const actor = event.actor || 'user';
		const via = event.viaApiKey ? 'via API key' : 'via personal key';

		return `
			<li class="timeline-item">
				<div class="timeline-dot state ${event.state ? 'on' : 'off'}"></div>
				<div class="timeline-content">
					<div class="timeline-head">
						<span class="timeline-type">State</span>
						<span class="timeline-time">${timeText}</span>
					</div>
					<p class="timeline-actor">${escapeHtml(actor)} ${stateLabel} (${via})</p>
				</div>
			</li>
		`;
	}).join('');
}

function renderToggleDialogActivity(detail) {
	if (!toggleDialogEvents) return;
	const events = (detail && Array.isArray(detail.events)) ? detail.events : [];
	if (!events.length) {
		toggleDialogEvents.innerHTML = '<li class="timeline-empty">No recent activity.</li>';
		return;
	}
	const latest = events.slice(0, 4);
	toggleDialogEvents.innerHTML = latest.map((event) => {
		const timeText = event.timestamp ? formatTimeAgo(new Date(event.timestamp)) : 'Unknown time';
		if (event.type === 'comment') {
			return `
				<li class="timeline-item">
					<div class="timeline-dot comment"></div>
					<div class="timeline-content">
						<div class="timeline-head">
							<span class="timeline-type">Comment</span>
							<span class="timeline-time">${timeText}</span>
						</div>
						<p class="timeline-actor">${escapeHtml(event.actor || 'user')}</p>
						<p>${escapeHtml(event.comment || '')}</p>
					</div>
				</li>
			`;
		}

		const stateLabel = event.state ? 'turned ON' : 'turned OFF';
		const actor = event.actor || 'user';
		const via = event.viaApiKey ? 'via API key' : 'via personal key';
		return `
			<li class="timeline-item">
				<div class="timeline-dot state ${event.state ? 'on' : 'off'}"></div>
				<div class="timeline-content">
					<div class="timeline-head">
						<span class="timeline-type">State</span>
						<span class="timeline-time">${timeText}</span>
					</div>
					<p class="timeline-actor">${escapeHtml(actor)} ${stateLabel} (${via})</p>
				</div>
			</li>
		`;
	}).join('');
}

async function submitComment() {
	if (!currentSwitchId) {
		return;
	}
	if (!hasAccessKey(currentSwitchId)) {
		openAuthDialog(false);
		return;
	}
	const comment = (commentTextInput.value || '').trim();
	if (!comment) {
		commentStatus.textContent = 'Comment is required.';
		return;
	}

	const commentKey = resolveCommentAccessKey(currentSwitchId);
	if (!commentKey) {
		commentStatus.textContent = 'Access key is required.';
		return;
	}

	const requestedState = commentStateSelect ? String(commentStateSelect.value || '').trim() : '';
	const wantsStateChange = requestedState === 'on' || requestedState === 'off';
	if (wantsStateChange) {
		const commentKeyValue = getActiveApiKey(currentSwitchId);
		const managementKey = getActiveManagementKey(currentSwitchId);
		const toggleKey = managementKey || commentKeyValue;
		if (!toggleKey) {
			commentStatus.textContent = 'Access key required to toggle.';
			return;
		}
		commentStatus.textContent = 'Applying state change...';
		commentStatus.className = 'comment-status';

		const detail = await ensureCurrentSwitchDetail(currentSwitchId);
		const currentState = (detail && typeof detail.state === 'boolean') ? detail.state : null;
		if (currentState == null) {
			commentStatus.textContent = 'Current state unavailable.';
			commentStatus.className = 'comment-status error';
			return;
		}
		const targetState = requestedState === 'on';
		if (currentState !== targetState) {
			try {
				await toggleSwitchWithAccessKey(currentSwitchId, toggleKey);
			} catch (error) {
				if (managementKey && commentKeyValue && managementKey !== commentKeyValue) {
					try {
						await toggleSwitchWithAccessKey(currentSwitchId, commentKeyValue);
					} catch (fallbackError) {
						console.error('Failed to toggle during comment:', fallbackError);
						let message = fallbackError.message || 'Failed to toggle.';
						if (/insufficient permissions/i.test(message)) {
							message = 'Key missing toggle permission. Use a key with toggle access.';
						}
						commentStatus.textContent = message;
						commentStatus.className = 'comment-status error';
						return;
					}
				} else {
					console.error('Failed to toggle during comment:', error);
					let message = error.message || 'Failed to toggle.';
					if (/insufficient permissions/i.test(message)) {
						message = 'Key missing toggle permission. Use a key with toggle access.';
					}
					commentStatus.textContent = message;
					commentStatus.className = 'comment-status error';
					return;
				}
			}
		}
	}

	commentStatus.textContent = 'Posting...';
	commentStatus.className = 'comment-status';
	try {
		const response = await fetch(`${API_BASE_URL}/v2/switch/${currentSwitchId}/comment`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'X-Api-Key': commentKey
			},
			body: JSON.stringify({ comment })
		});

		const data = await response.json();
		if (!response.ok || !data.success) {
			throw new Error(data.error || 'Failed to post comment');
		}

		commentStatus.textContent = 'Posted.';
		commentStatus.className = 'comment-status success';
		commentTextInput.value = '';
		if (commentStateSelect) commentStateSelect.value = '';
		await openSwitchDetails(currentSwitchId, true);
	} catch (error) {
		console.error('Failed to post comment:', error);
		let message = 'Failed to post comment.';
		if (error && error.message) {
			message = error.message;
		}
		if (/insufficient permissions/i.test(message)) {
			message = 'Key missing comment permission. Use a key with comment access.';
		}
		commentStatus.textContent = message;
		commentStatus.className = 'comment-status error';
	}
}

function closeDetail() {
	detailSection.classList.add('hidden');
	currentSwitchId = null;
	currentSwitchDetail = null;
	hideRedirectNotice();
	clearHeroBanner();
	restoreHeroText();
	if (document?.body) {
		document.body.classList.remove('view-switch');
	}
	updateNavSwitchStatus(null);
	clearSwitchQuery();
	ensureRealtimeSubscriptions();
	updateCommentStateVisibility();
	closeToggleDialog();
	closeAuthDialog();
}

function filterByCategory(encodedCategory) {
	const category = decodeURIComponent(encodedCategory);
	categoryFilter.value = category;
	applyFilters();
}

function restoreSwitchFromQuery() {
	const pathSwitchId = extractSwitchUidFromPathname(window.location.pathname);
	const params = new URLSearchParams(window.location.search);
	const querySwitchId = params.get('switch');
	const switchId = pathSwitchId || (querySwitchId && isValidSwitchUid(querySwitchId) ? querySwitchId : null);
	if (switchId) {
		openSwitchDetails(switchId, true);
	} else {
		closeDetail();
	}
}

function buildSwitchPath(uid) {
	const params = new URLSearchParams(window.location.search);
	params.delete('switch');
	const suffix = params.toString();
	return `/switch/${encodeURIComponent(uid)}${suffix ? `?${suffix}` : ''}`;
}

function buildHomePath() {
	const params = new URLSearchParams(window.location.search);
	params.delete('switch');
	const suffix = params.toString();
	return `/${suffix ? `?${suffix}` : ''}`;
}

function pushSwitchQuery(uid) {
	const newUrl = buildSwitchPath(uid);
	window.history.pushState({}, '', newUrl);
}

function clearSwitchQuery() {
	const currentPathSwitchId = extractSwitchUidFromPathname(window.location.pathname);
	const params = new URLSearchParams(window.location.search);
	const hasQuerySwitch = params.has('switch');
	if (!currentPathSwitchId && !hasQuerySwitch) {
		return;
	}
	const newUrl = buildHomePath();
	window.history.pushState({}, '', newUrl);
}

function scrollToSwitches() {
	const section = document.querySelector('.switches');
	if (section) {
		section.scrollIntoView({ behavior: 'smooth' });
	}
}

function formatTimeAgo(date) {
	const now = new Date();
	const diffMs = now - date;
	const diffMins = Math.floor(diffMs / 60000);
	const diffHours = Math.floor(diffMins / 60);
	const diffDays = Math.floor(diffHours / 24);

	if (diffMins < 1) return 'Just now';
	if (diffMins < 60) return `${diffMins}m ago`;
	if (diffHours < 24) return `${diffHours}h ago`;
	if (diffDays < 7) return `${diffDays}d ago`;

	return date.toLocaleDateString();
}

function getDisplaySwitchName(rawName) {
	const text = String(rawName || '').trim();
	if (!text) return '';
	let cleaned = text.replace(/^VomeSync(?:\s+Switch)?\s*[-:–—]\s*/i, '').trim();
	if (cleaned && cleaned !== text) {
		return cleaned;
	}
	if (/^VomeSync\s+/i.test(text)) {
		cleaned = text.replace(/^VomeSync\s+/i, '').trim();
		if (cleaned) return cleaned;
	}
	return text;
}

function escapeHtml(text) {
	const div = document.createElement('div');
	div.textContent = text;
	return div.innerHTML;
}

function escapeAttr(text) {
	const div = document.createElement('div');
	div.textContent = text;
	return div.innerHTML;
}

// Expose functions for inline handlers
window.openSwitchDetails = openSwitchDetails;
window.filterByCategory = filterByCategory;
window.copyUID = copyUID;
