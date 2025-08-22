// VomeSync Website JavaScript

const API_BASE_URL = 'https://sync.vome.io/api';
let allSwitches = [];
let filteredSwitches = [];

// DOM elements
const loadingMessage = document.getElementById('loadingMessage');
const errorMessage = document.getElementById('errorMessage');
const emptySwitches = document.getElementById('emptySwitches');
const switchesGrid = document.getElementById('switchesGrid');
const searchBox = document.getElementById('searchBox');
const categoryFilter = document.getElementById('categoryFilter');
const refreshBtn = document.getElementById('refreshBtn');
const totalSwitches = document.getElementById('totalSwitches');
const activeSwitches = document.getElementById('activeSwitches');
const lastUpdate = document.getElementById('lastUpdate');

// Initialize the application
document.addEventListener('DOMContentLoaded', function() {
	loadSwitches();
	setupEventListeners();
});

function setupEventListeners() {
	// Search and filter
	searchBox.addEventListener('input', handleSearch);
	categoryFilter.addEventListener('change', handleFilter);
	refreshBtn.addEventListener('click', loadSwitches);
	
	// Auto-refresh every 30 seconds
	setInterval(loadSwitches, 30000);
}

async function loadSwitches() {
	showLoading();
	
	try {
		const response = await fetch(`${API_BASE_URL}/public-switches`);
		
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}: ${response.statusText}`);
		}
		
		const data = await response.json();
		
		if (data.success) {
			allSwitches = data.data.switches || [];
			filteredSwitches = [...allSwitches];
			
			updateStats();
			renderSwitches();
			hideMessages();
			
			if (allSwitches.length === 0) {
				showEmptyMessage();
			}
		} else {
			throw new Error(data.error || 'Failed to load switches');
		}
		
	} catch (error) {
		console.error('Error loading switches:', error);
		showError();
	}
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

function handleSearch() {
	const query = searchBox.value.toLowerCase().trim();
	applyFilters(query, categoryFilter.value);
}

function handleFilter() {
	const query = searchBox.value.toLowerCase().trim();
	applyFilters(query, categoryFilter.value);
}

function applyFilters(searchQuery, category) {
	filteredSwitches = allSwitches.filter(switchData => {
		// Search filter
		const matchesSearch = !searchQuery || 
			switchData.description.toLowerCase().includes(searchQuery) ||
			switchData.location.toLowerCase().includes(searchQuery) ||
			switchData.category.toLowerCase().includes(searchQuery);
		
		// Category filter
		const matchesCategory = !category || switchData.category === category;
		
		return matchesSearch && matchesCategory;
	});
	
	renderSwitches();
}

function renderSwitches() {
	if (filteredSwitches.length === 0 && allSwitches.length > 0) {
		// No results from filtering
		switchesGrid.innerHTML = `
			<div class="empty-message" style="grid-column: 1 / -1;">
				<h3>🔍 No Matching Switches</h3>
				<p>No switches match your search criteria. Try adjusting your filters.</p>
			</div>
		`;
		return;
	}
	
	switchesGrid.innerHTML = filteredSwitches.map(switchData => 
		createSwitchCard(switchData)
	).join('');
}

function createSwitchCard(switchData) {
	const {
		uid,
		description,
		location,
		category,
		state,
		lastToggled
	} = switchData;
	
	const stateClass = state ? 'state-on' : 'state-off';
	const stateText = state ? 'on' : 'off';
	const stateLabel = state ? 'ON' : 'OFF';
	
	const lastToggledText = lastToggled ? 
		formatTimeAgo(new Date(lastToggled)) : 
		'Never';
	
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
					<span class="switch-detail-value">${escapeHtml(category)}</span>
				</div>
				
				<div class="switch-detail">
					<span class="switch-detail-label">🕒 Last Changed:</span>
					<span class="switch-detail-value">${lastToggledText}</span>
				</div>
				
				<div class="switch-detail">
					<span class="switch-detail-label">🆔 UID:</span>
					<span class="switch-detail-value" style="font-family: monospace; font-size: 0.8rem;">${uid.substring(0, 8)}...</span>
				</div>
			</div>
			
			<div class="switch-actions">
				<button class="copy-uid-btn" onclick="copyUID('${uid}', this)">
					📋 Copy UID
				</button>
				<button class="view-details-btn" onclick="showSwitchDetails('${uid}')">
					👁️ Details
				</button>
			</div>
		</div>
	`;
}

function copyUID(uid, button) {
	navigator.clipboard.writeText(uid).then(() => {
		const originalText = button.textContent;
		button.textContent = '✅ Copied!';
		button.classList.add('copied');
		
		setTimeout(() => {
			button.textContent = originalText;
			button.classList.remove('copied');
		}, 2000);
	}).catch(err => {
		console.error('Failed to copy UID:', err);
		
		// Fallback for older browsers
		const textArea = document.createElement('textarea');
		textArea.value = uid;
		document.body.appendChild(textArea);
		textArea.select();
		
		try {
			document.execCommand('copy');
			button.textContent = '✅ Copied!';
			button.classList.add('copied');
			
			setTimeout(() => {
				button.textContent = '📋 Copy UID';
				button.classList.remove('copied');
			}, 2000);
		} catch (err) {
			alert('Failed to copy UID. Please copy manually: ' + uid);
		}
		
		document.body.removeChild(textArea);
	});
}

function showSwitchDetails(uid) {
	const switchData = allSwitches.find(sw => sw.uid === uid);
	if (!switchData) return;
	
	const modal = document.createElement('div');
	modal.className = 'modal-overlay';
	modal.innerHTML = `
		<div class="modal-content">
			<div class="modal-header">
				<h2>Switch Details</h2>
				<button class="modal-close" onclick="closeModal()">&times;</button>
			</div>
			<div class="modal-body">
				<div class="detail-grid">
					<div class="detail-item">
						<strong>Description:</strong>
						<span>${escapeHtml(switchData.description || 'No description')}</span>
					</div>
					<div class="detail-item">
						<strong>Current State:</strong>
						<span class="state-badge ${switchData.state ? 'on' : 'off'}">${switchData.state ? 'ON' : 'OFF'}</span>
					</div>
					<div class="detail-item">
						<strong>Location:</strong>
						<span>${escapeHtml(switchData.location || 'Not specified')}</span>
					</div>
					<div class="detail-item">
						<strong>Category:</strong>
						<span>${escapeHtml(switchData.category)}</span>
					</div>
					<div class="detail-item">
						<strong>Last Changed:</strong>
						<span>${switchData.lastToggled ? new Date(switchData.lastToggled).toLocaleString() : 'Never'}</span>
					</div>
					<div class="detail-item">
						<strong>Unique ID:</strong>
						<span style="font-family: monospace; word-break: break-all;">${switchData.uid}</span>
					</div>
				</div>
				<div class="modal-actions">
					<button class="copy-uid-btn" onclick="copyUID('${switchData.uid}', this)">
						📋 Copy Full UID
					</button>
				</div>
			</div>
		</div>
	`;
	
	document.body.appendChild(modal);
	
	// Add modal styles if not already present
	if (!document.querySelector('#modal-styles')) {
		const styles = document.createElement('style');
		styles.id = 'modal-styles';
		styles.textContent = `
			.modal-overlay {
				position: fixed;
				top: 0;
				left: 0;
				right: 0;
				bottom: 0;
				background: rgba(0,0,0,0.7);
				display: flex;
				align-items: center;
				justify-content: center;
				z-index: 1000;
			}
			.modal-content {
				background: white;
				border-radius: 8px;
				max-width: 500px;
				width: 90%;
				max-height: 80vh;
				overflow-y: auto;
			}
			.modal-header {
				display: flex;
				justify-content: space-between;
				align-items: center;
				padding: 1.5rem;
				border-bottom: 1px solid #e0e0e0;
			}
			.modal-close {
				background: none;
				border: none;
				font-size: 1.5rem;
				cursor: pointer;
				color: #666;
			}
			.modal-body {
				padding: 1.5rem;
			}
			.detail-grid {
				display: grid;
				gap: 1rem;
				margin-bottom: 2rem;
			}
			.detail-item {
				display: grid;
				grid-template-columns: 140px 1fr;
				gap: 1rem;
				align-items: center;
			}
			.state-badge {
				padding: 0.25rem 0.75rem;
				border-radius: 20px;
				font-size: 0.85rem;
				font-weight: 500;
				text-transform: uppercase;
			}
			.state-badge.on {
				background: #e8f5e8;
				color: #4CAF50;
			}
			.state-badge.off {
				background: #f5f5f5;
				color: #666;
			}
			.modal-actions {
				text-align: center;
			}
		`;
		document.head.appendChild(styles);
	}
}

function closeModal() {
	const modal = document.querySelector('.modal-overlay');
	if (modal) {
		document.body.removeChild(modal);
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

// Close modal when clicking outside
document.addEventListener('click', function(e) {
	if (e.target.classList.contains('modal-overlay')) {
		closeModal();
	}
});

// Close modal with Escape key
document.addEventListener('keydown', function(e) {
	if (e.key === 'Escape') {
		closeModal();
	}
});
