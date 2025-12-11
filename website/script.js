// VomeSync Website JavaScript (static SPA)

// Auto-detect API URL based on environment
const API_BASE_URL = window.location.port === '8112' 
	? 'http://localhost:3091/api'  // Dev environment
	: 'https://sync.vome.io/api';   // Production environment

let allSwitches = [];
let filteredSwitches = [];
let categories = {};
let currentSwitchId = null;

// DOM elements
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

document.addEventListener('DOMContentLoaded', () => {
	init();
});

function init() {
	setupEventListeners();
	loadAllData();
	restoreSwitchFromQuery();
}

function setupEventListeners() {
	searchBox.addEventListener('input', () => applyFilters());
	categoryFilter.addEventListener('change', () => applyFilters());
	userCountFilter.addEventListener('change', () => applyFilters());
	refreshBtn.addEventListener('click', loadAllData);

	backToList.addEventListener('click', () => {
		closeDetail();
		scrollToSwitches();
	});

	copySwitchLinkBtn.addEventListener('click', () => {
		if (!currentSwitchId) return;
		const link = `${window.location.origin}${window.location.pathname}?switch=${currentSwitchId}`;
		copyText(link, copySwitchLinkBtn, '🔗 Copy link');
	});

	commentForm.addEventListener('submit', async (e) => {
		e.preventDefault();
		await submitComment();
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
		link
	} = switchData;

	const stateClass = state ? 'state-on' : 'state-off';
	const stateText = state ? 'on' : 'off';
	const stateLabel = state ? 'ON' : 'OFF';
	const lastToggledText = lastToggled ? formatTimeAgo(new Date(lastToggled)) : 'Never';
	const safeCategory = escapeHtml(category || 'Other');
	const usersLabel = typeof userCount === 'number' ? `${userCount} user${userCount === 1 ? '' : 's'}` : '0 users';
	const togglesLabel = typeof toggleCount === 'number' ? `${toggleCount} toggles` : '0 toggles';
	const webLink = link ? `<a class="inline-link" href="${escapeAttr(link)}" target="_blank" rel="noopener">🌐 Link</a>` : '';

	return `
		<div class="switch-card ${stateClass}">
			<div class="switch-header">
				<div class="switch-description">${escapeHtml(description || 'Untitled Switch')}</div>
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
	detailTitle.textContent = detail.description || 'Untitled switch';
	detailLocation.textContent = detail.location ? `📍 ${detail.location}` : '📍 Not specified';
	detailCategory.textContent = detail.category || 'Other';
	detailUsers.textContent = `${detail.userCount || 0} user${(detail.userCount || 0) === 1 ? '' : 's'}`;
	detailToggles.textContent = `${detail.toggleCount || 0} toggles`;
	detailLastChange.textContent = detail.lastToggled ? formatTimeAgo(new Date(detail.lastToggled)) : 'Never';
	detailUid.textContent = detail.uid;

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
		const response = await fetch(`${API_BASE_URL}/switch/${currentSwitchId}/comment`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'X-Personal-Key': key
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
	clearSwitchQuery();
}

function filterByCategory(encodedCategory) {
	const category = decodeURIComponent(encodedCategory);
	categoryFilter.value = category;
	applyFilters();
}

function restoreSwitchFromQuery() {
	const params = new URLSearchParams(window.location.search);
	const switchId = params.get('switch');
	if (switchId) {
		openSwitchDetails(switchId, true);
	} else {
		closeDetail();
	}
}

function pushSwitchQuery(uid) {
	const params = new URLSearchParams(window.location.search);
	params.set('switch', uid);
	const newUrl = `${window.location.pathname}?${params.toString()}`;
	window.history.pushState({}, '', newUrl);
}

function clearSwitchQuery() {
	const params = new URLSearchParams(window.location.search);
	if (params.has('switch')) {
		params.delete('switch');
		const newUrl = params.toString() ? `${window.location.pathname}?${params.toString()}` : window.location.pathname;
		window.history.pushState({}, '', newUrl);
	}
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
