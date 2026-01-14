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

let allSwitches = [];
let filteredSwitches = [];
let categories = {};
let currentSwitchId = null;
let currentSwitchDetail = null;

// DOM elements
const heroSection = document.querySelector('.hero');
const heroTitleEl = document.getElementById('heroTitle');
const heroSubtitleEl = document.getElementById('heroSubtitle');
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
const commentForm = document.getElementById('commentForm');
const commentKeyInput = document.getElementById('commentKey');
const commentTextInput = document.getElementById('commentText');
const commentStatus = document.getElementById('commentStatus');

// Owner tools (appearance)
const managePanel = document.getElementById('managePanel');
const manageForm = document.getElementById('manageForm');
const manageKeyInput = document.getElementById('manageKey');
const manageLinkInput = document.getElementById('manageLink');
const manageIconFileInput = document.getElementById('manageIconFile');
const manageIconUrlInput = document.getElementById('manageIconUrl');
const manageBannerFileInput = document.getElementById('manageBannerFile');
const manageBannerUrlInput = document.getElementById('manageBannerUrl');
const manageStatus = document.getElementById('manageStatus');
const manageForgetBtn = document.getElementById('manageForgetBtn');

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

// Quick view modal
const quickView = document.getElementById('quickView');
const quickViewBackdrop = document.getElementById('quickViewBackdrop');
const quickViewCloseBtn = document.getElementById('quickViewClose');
const quickViewIcon = document.getElementById('quickViewIcon');
const quickViewTitle = document.getElementById('quickViewTitle');
const quickViewSubtitle = document.getElementById('quickViewSubtitle');
const quickViewMeta = document.getElementById('quickViewMeta');
const quickViewCopyUidBtn = document.getElementById('quickViewCopyUid');
const quickViewOpenDetailsBtn = document.getElementById('quickViewOpenDetails');

const DEFAULT_HERO_TITLE_HTML = heroTitleEl ? heroTitleEl.innerHTML : '';
const DEFAULT_HERO_SUBTITLE_TEXT = heroSubtitleEl ? heroSubtitleEl.textContent : '';

let manageIconAction = 'keep';
let manageBannerAction = 'keep';
let manageCurrentIconUrl = '';
let manageCurrentBannerUrl = '';
let manageIconObjectUrl = null;
let manageBannerObjectUrl = null;

let quickViewUid = null;
let quickViewDetail = null;

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

function restoreHeroText() {
	if (!heroTitleEl || !heroSubtitleEl) return;
	heroTitleEl.innerHTML = DEFAULT_HERO_TITLE_HTML;
	heroSubtitleEl.textContent = DEFAULT_HERO_SUBTITLE_TEXT;
}

function setHeroForSwitch(detail) {
	if (!detail) return;
	if (!heroTitleEl || !heroSubtitleEl) return;

	const title = String(detail.description || '').trim() || 'Switch';
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
}

function init() {
	applyEnvBadge();
	applyDynamicLinks();
	setupEventListeners();
	importManagementKeyFromHash();
	loadAllData();
	restoreSwitchFromQuery();
}

const MANAGEMENT_KEY_STORAGE_PREFIX = 'vomesync_manage_key:';
let managementAutoscrollUid = null;

function getStoredManagementKey(uid) {
	if (!uid) return '';
	try {
		return sessionStorage.getItem(`${MANAGEMENT_KEY_STORAGE_PREFIX}${uid}`) || '';
	} catch {
		return '';
	}
}

function setStoredManagementKey(uid, apiKey) {
	if (!uid) return;
	try {
		if (!apiKey) {
			sessionStorage.removeItem(`${MANAGEMENT_KEY_STORAGE_PREFIX}${uid}`);
		} else {
			sessionStorage.setItem(`${MANAGEMENT_KEY_STORAGE_PREFIX}${uid}`, apiKey);
		}
	} catch {
		// ignore (private mode, etc)
	}
}

function importManagementKeyFromHash() {
	const hash = String(window.location.hash || '');
	if (!hash || hash.length < 2) return;
	const params = new URLSearchParams(hash.slice(1));
	const apiKey = String(params.get('accessKey') || '').trim();
	if (!apiKey) return;

	const uid = extractSwitchUidFromPathname(window.location.pathname);
	if (!uid) return;

	setStoredManagementKey(uid, apiKey);
	managementAutoscrollUid = uid;

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
	const loginLink = document.getElementById('loginLink');
	if (loginLink) {
		loginLink.href = `${window.location.origin}/login`;
	}
	
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

	if (manageForgetBtn) {
		manageForgetBtn.addEventListener('click', () => {
			if (currentSwitchId) {
				setStoredManagementKey(currentSwitchId, '');
			}
			if (manageKeyInput) {
				manageKeyInput.value = '';
			}
			if (manageStatus) {
				manageStatus.textContent = 'Key cleared (this browser session).';
				manageStatus.className = 'comment-status';
			}
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
	window.addEventListener('keydown', (e) => {
		if (e.key === 'Escape') {
			closeQuickView();
		}
	});

	window.addEventListener('popstate', () => {
		restoreSwitchFromQuery();
	});

	setInterval(loadAllData, 30000);
}

async function loadAllData() {
	showLoading();
	try {
		await Promise.all([loadSwitches(), loadCategories()]);
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
	const response = await fetch(`${API_BASE_URL}/public-switches`);
	if (!response.ok) {
		throw new Error(`HTTP ${response.status}: ${response.statusText}`);
	}

	const data = await response.json();
	if (!data.success) {
		throw new Error(data.error || 'Failed to load switches');
	}

	allSwitches = data.data.switches || [];
	filteredSwitches = [...allSwitches];
	updateStats();
	renderSwitches();
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
	const now = new Date();

	totalSwitches.textContent = total;
	activeSwitches.textContent = active;
	lastUpdate.textContent = now.toLocaleTimeString();
}

function applyFilters() {
	const searchQuery = searchBox.value.toLowerCase().trim();
	const category = categoryFilter.value;
	const minUsers = userCountFilter.value ? parseInt(userCountFilter.value, 10) : null;

	filteredSwitches = allSwitches.filter((switchData) => {
		const description = (switchData.description || '').toLowerCase();
		const location = (switchData.location || '').toLowerCase();
		const categoryValue = (switchData.category || '').toLowerCase();

		const matchesSearch = !searchQuery || description.includes(searchQuery) || location.includes(searchQuery) || categoryValue.includes(searchQuery);
		const matchesCategory = !category || switchData.category === category;
		const matchesUserCount = !minUsers || (switchData.userCount || 0) >= minUsers;

		return matchesSearch && matchesCategory && matchesUserCount;
	});

	renderSwitches();
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

function renderQuickView(detail) {
	if (!detail) return;
	if (quickViewTitle) quickViewTitle.textContent = detail.description || 'Untitled switch';
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
	const iconHtml = iconUrl ? `<img class="switch-icon" src="${escapeAttr(iconUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '';

	return `
		<div class="switch-card ${stateClass}" data-uid="${escapeAttr(uid)}" data-banner-url="${escapeAttr(bannerUrl || '')}">
			<div class="switch-header">
				<div class="switch-title">
					${iconHtml}
					<div class="switch-description">${escapeHtml(description || 'Untitled Switch')}</div>
				</div>
				<div class="switch-state ${stateText}">${stateLabel}</div>
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
					<span class="switch-detail-value">${usersLabel}</span>
				</div>

				<div class="switch-detail">
					<span class="switch-detail-label">🔢 Toggles:</span>
					<span class="switch-detail-value">${togglesLabel}</span>
				</div>

				<div class="switch-detail">
					<span class="switch-detail-label">🕒 Last Changed:</span>
					<span class="switch-detail-value">${lastToggledText}</span>
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

async function openSwitchDetails(uid, fromPopState = false) {
	try {
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

		detailSection.classList.remove('hidden');
		renderSwitchDetail(data.data);
		if (!fromPopState) {
			pushSwitchQuery(uid);
		}
	} catch (error) {
		console.error('Error loading switch detail:', error);
		alert('Unable to load switch details. Please try again.');
		closeDetail();
	}
}

function renderSwitchDetail(detail) {
	currentSwitchDetail = detail;
	detailTitle.textContent = detail.description || 'Untitled switch';
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

function updateManagePanel(detail) {
	if (!managePanel) return;
	// Directory is v2-only; owner tools are optional (access key required).
	managePanel.classList.remove('hidden');

	if (manageLinkInput) manageLinkInput.value = detail.link || '';
	resetManageMediaPickers(detail);

	const storedKey = getStoredManagementKey(detail.uid);
	if (storedKey && manageKeyInput && !manageKeyInput.value) {
		manageKeyInput.value = storedKey;
	}

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
}

async function submitManageAppearance() {
	if (!currentSwitchId) return;
	if (!manageStatus) return;

	const apiKey = String(manageKeyInput?.value || '').trim() || getStoredManagementKey(currentSwitchId);
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

		// Single-use keys (from HA "Manage on website") are revoked by the server after first save.
		setStoredManagementKey(currentSwitchId, '');
		if (manageKeyInput) {
			manageKeyInput.value = '';
		}
	resetManageMediaPickers(currentSwitchDetail || {});

		manageStatus.textContent = 'Saved. (This key is now invalid — generate a new link to edit again.)';
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

async function submitComment() {
	if (!currentSwitchId) {
		return;
	}
	const key = (commentKeyInput.value || '').trim();
	const comment = (commentTextInput.value || '').trim();
	if (!key || !comment) {
		commentStatus.textContent = 'Key and comment are required.';
		return;
	}

	commentStatus.textContent = 'Posting...';
	try {
		const response = await fetch(`${API_BASE_URL}/v2/switch/${currentSwitchId}/comment`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'X-Api-Key': key
			},
			body: JSON.stringify({ comment })
		});

		const data = await response.json();
		if (!response.ok || !data.success) {
			throw new Error(data.error || 'Failed to post comment');
		}

		commentStatus.textContent = 'Posted.';
		commentTextInput.value = '';
		await openSwitchDetails(currentSwitchId, true);
	} catch (error) {
		console.error('Failed to post comment:', error);
		commentStatus.textContent = 'Failed to post comment.';
	}
}

function closeDetail() {
	detailSection.classList.add('hidden');
	currentSwitchId = null;
	currentSwitchDetail = null;
	clearHeroBanner();
	restoreHeroText();
	if (document?.body) {
		document.body.classList.remove('view-switch');
	}
	clearSwitchQuery();
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
