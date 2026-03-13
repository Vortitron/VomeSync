/**
 * VomeSync Network Background Animation
 * Creates an animated mesh network of switches and lights
 */

class NetworkBackground {
	constructor(container) {
		this.container = container;
		this.svg = null;
		this.nodes = [];
		this.connections = [];
		this.animationFrame = null;
		
		this.init();
	}
	
	init() {
		// Create SVG
		this.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
		this.svg.classList.add('network-bg');
		this.svg.style.position = 'absolute';
		this.svg.style.top = '0';
		this.svg.style.left = '0';
		this.svg.style.width = '100%';
		this.svg.style.height = '100%';
		// Note: pointer-events set via CSS, switches have pointerEvents: 'all'
		this.container.insertBefore(this.svg, this.container.firstChild);
		
		// Create defs for gradients
		const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
		
		// Gradient for active connections
		const gradient = document.createElementNS('http://www.w3.org/2000/svg', 'linearGradient');
		gradient.setAttribute('id', 'lineGradient');
		gradient.setAttribute('gradientUnits', 'userSpaceOnUse');
		const stop1 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
		stop1.setAttribute('offset', '0%');
		stop1.setAttribute('style', 'stop-color:#FF9800;stop-opacity:0.4');
		const stop2 = document.createElementNS('http://www.w3.org/2000/svg', 'stop');
		stop2.setAttribute('offset', '100%');
		stop2.setAttribute('style', 'stop-color:#FFB74D;stop-opacity:0.4');
		gradient.appendChild(stop1);
		gradient.appendChild(stop2);
		defs.appendChild(gradient);
		
		this.svg.appendChild(defs);
		
		// Generate network
		this.generateNetwork();
		
		// Start animation loop
		this.animate();
		
		// Handle resize
		window.addEventListener('resize', () => this.handleResize());
	}
	
	generateNetwork() {
		const width = this.container.clientWidth || window.innerWidth;
		const height = this.container.clientHeight || 500;
		
		// Set SVG viewBox to match container
		this.svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
		this.svg.setAttribute('preserveAspectRatio', 'xMidYMid slice');
		
		// Create switches (4-6)
		const numSwitches = 4 + Math.floor(Math.random() * 3);
		const switches = [];
		
		for (let i = 0; i < numSwitches; i++) {
			const x = (width / (numSwitches + 1)) * (i + 1);
			const y = height * (0.25 + Math.random() * 0.5);
			const switchNode = this.createSwitch(x, y);
			switches.push(switchNode);
			this.nodes.push(switchNode);
		}
		
		// Create lights (6-10)
		const numLights = 6 + Math.floor(Math.random() * 5);
		const lights = [];
		
		for (let i = 0; i < numLights; i++) {
			const x = (width / (numLights + 1)) * (i + 1) + (Math.random() - 0.5) * 80;
			const y = height * (0.25 + Math.random() * 0.5);
			const lightNode = this.createLight(x, y);
			lights.push(lightNode);
			this.nodes.push(lightNode);
		}
		
		// Create connections - ensure every light is connected to at least one switch
		const connectedLights = new Set();
		
		// First pass: each switch connects to 1-3 random lights
		switches.forEach(switchNode => {
			const numConnections = 1 + Math.floor(Math.random() * 3);
			const lightsToConnect = this.getRandomItems(lights, numConnections);
			
			lightsToConnect.forEach(lightNode => {
				this.createConnection(switchNode, lightNode);
				connectedLights.add(lightNode);
			});
		});
		
		// Second pass: connect any unconnected lights to a random switch
		lights.forEach(lightNode => {
			if (!connectedLights.has(lightNode)) {
				const randomSwitch = switches[Math.floor(Math.random() * switches.length)];
				this.createConnection(randomSwitch, lightNode);
			}
		});
		
		// Draw everything
		this.render();
	}
	
	createSwitch(x, y) {
		return {
			type: 'switch',
			x,
			y,
			baseX: x,
			baseY: y,
			vx: (Math.random() - 0.5) * 0.3,
			vy: (Math.random() - 0.5) * 0.3,
			state: Math.random() > 0.5, // on/off
			toggleTimer: Math.random() * 5000 + 3000, // Random toggle interval
			lastToggle: Date.now(),
			element: null
		};
	}
	
	createLight(x, y) {
		return {
			type: 'light',
			x,
			y,
			baseX: x,
			baseY: y,
			vx: (Math.random() - 0.5) * 0.2,
			vy: (Math.random() - 0.5) * 0.2,
			state: false,
			element: null
		};
	}
	
	createConnection(switchNode, lightNode) {
		this.connections.push({
			from: switchNode,
			to: lightNode,
			element: null
		});
		
		// Link light to switch
		if (!switchNode.connectedLights) {
			switchNode.connectedLights = [];
		}
		switchNode.connectedLights.push(lightNode);
	}
	
	render() {
		// Clear SVG content (keep defs)
		const defs = this.svg.querySelector('defs');
		while (this.svg.firstChild) {
			this.svg.removeChild(this.svg.firstChild);
		}
		if (defs) {
			this.svg.appendChild(defs);
		}
		
		// Draw connections first (behind nodes)
		this.connections.forEach(conn => {
			const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
			line.setAttribute('x1', conn.from.x);
			line.setAttribute('y1', conn.from.y);
			line.setAttribute('x2', conn.to.x);
			line.setAttribute('y2', conn.to.y);
			line.setAttribute('class', 'connection');
			
			if (conn.from.state && conn.to.state) {
			line.setAttribute('stroke', 'rgba(255, 152, 0, 0.5)');
			line.setAttribute('stroke-width', '2.5');
		} else {
			line.setAttribute('stroke', 'rgba(117, 117, 117, 0.2)');
			line.setAttribute('stroke-width', '1.5');
		}
		
		conn.element = line;
		this.svg.appendChild(line);
	});
	
	// Draw nodes
		this.nodes.forEach(node => {
			if (node.type === 'switch') {
				const g = this.drawSwitch(node);
				node.element = g;
				this.svg.appendChild(g);
			} else {
				const circle = this.drawLight(node);
				node.element = circle;
				this.svg.appendChild(circle);
			}
		});
	}
	
	drawSwitch(node) {
		const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
		g.setAttribute('transform', `translate(${node.x}, ${node.y})`);
		g.setAttribute('class', 'switch-node');
		g.style.cursor = 'pointer';
		g.style.pointerEvents = 'all'; // Enable clicks on this element
		
		// Switch body (portrait rectangle)
		const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
		rect.setAttribute('x', -15);
		rect.setAttribute('y', -25);
		rect.setAttribute('width', 30);
		rect.setAttribute('height', 50);
		rect.setAttribute('rx', 4);
		rect.setAttribute('fill', 'rgba(30, 30, 30, 0.9)');
		rect.setAttribute('stroke', 'rgba(255, 152, 0, 0.6)');
		rect.setAttribute('stroke-width', '1.5');
		g.appendChild(rect);
		
		// Toggle indicator (square that moves)
		const indicator = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
		indicator.setAttribute('x', -8);
		indicator.setAttribute('y', node.state ? 8 : -20);
		indicator.setAttribute('width', 16);
		indicator.setAttribute('height', 16);
		indicator.setAttribute('rx', 2);
		indicator.setAttribute('fill', node.state ? '#FF9800' : '#3A3A3A');
		indicator.setAttribute('class', 'switch-indicator');
		g.appendChild(indicator);
		
		// Click handler
		g.addEventListener('click', (e) => {
			e.stopPropagation();
			e.preventDefault();
			this.toggleSwitch(node);
		});
		
		// Touch handler for mobile
		g.addEventListener('touchstart', (e) => {
			e.stopPropagation();
			e.preventDefault();
			this.toggleSwitch(node);
		}, { passive: false });
		
		return g;
	}
	
	drawLight(node) {
		const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
		circle.setAttribute('cx', node.x);
		circle.setAttribute('cy', node.y);
		circle.setAttribute('r', 10);
		circle.setAttribute('class', 'light-node');
		
		if (node.state) {
			circle.setAttribute('fill', '#FFB74D');
			circle.setAttribute('stroke', '#FF9800');
			circle.setAttribute('stroke-width', '3');
			
			// Add glow
			circle.style.filter = 'drop-shadow(0 0 12px rgba(255, 152, 0, 0.8))';
		} else {
			circle.setAttribute('fill', 'rgba(30, 30, 30, 0.5)');
			circle.setAttribute('stroke', 'rgba(117, 117, 117, 0.4)');
			circle.setAttribute('stroke-width', '2');
			circle.style.filter = 'none';
		}
		
		return circle;
	}
	
	toggleSwitch(switchNode) {
		switchNode.state = !switchNode.state;
		
		// Update connected lights
		if (switchNode.connectedLights) {
			switchNode.connectedLights.forEach(light => {
				light.state = switchNode.state;
			});
		}
		
		this.render();
	}
	
	animate() {
		const now = Date.now();
		
		// Update node positions (floating effect)
		this.nodes.forEach(node => {
			// Apply velocity
			node.x += node.vx;
			node.y += node.vy;
			
			// Spring back to base position
			const dx = node.baseX - node.x;
			const dy = node.baseY - node.y;
			node.vx += dx * 0.001;
			node.vy += dy * 0.001;
			
			// Damping
			node.vx *= 0.98;
			node.vy *= 0.98;
			
			// Random nudges
			if (Math.random() < 0.01) {
				node.vx += (Math.random() - 0.5) * 0.5;
				node.vy += (Math.random() - 0.5) * 0.5;
			}
			
			// Auto-toggle switches
			if (node.type === 'switch') {
				if (now - node.lastToggle > node.toggleTimer) {
					this.toggleSwitch(node);
					node.lastToggle = now;
					node.toggleTimer = Math.random() * 5000 + 3000;
				}
			}
		});
		
		// Update visual elements
		this.updatePositions();
		
		this.animationFrame = requestAnimationFrame(() => this.animate());
	}
	
	updatePositions() {
		// Update connections
		this.connections.forEach(conn => {
			if (conn.element) {
				conn.element.setAttribute('x1', conn.from.x);
				conn.element.setAttribute('y1', conn.from.y);
				conn.element.setAttribute('x2', conn.to.x);
				conn.element.setAttribute('y2', conn.to.y);
				
				if (conn.from.state && conn.to.state) {
				conn.element.setAttribute('stroke', 'rgba(255, 152, 0, 0.5)');
				conn.element.setAttribute('stroke-width', '2.5');
			} else {
				conn.element.setAttribute('stroke', 'rgba(117, 117, 117, 0.2)');
					conn.element.setAttribute('stroke-width', '1.5');
				}
			}
		});
		
		// Update nodes
		this.nodes.forEach(node => {
			if (node.type === 'switch' && node.element) {
				node.element.setAttribute('transform', `translate(${node.x}, ${node.y})`);
				
				// Update indicator position with smooth transition
				const indicator = node.element.querySelector('.switch-indicator');
				if (indicator) {
					const targetY = node.state ? 8 : -20;
					indicator.setAttribute('y', targetY);
					indicator.setAttribute('fill', node.state ? '#FF9800' : '#3A3A3A');
				}
			} else if (node.type === 'light' && node.element) {
				node.element.setAttribute('cx', node.x);
				node.element.setAttribute('cy', node.y);
			}
		});
	}
	
	handleResize() {
		// Regenerate network on resize
		this.nodes = [];
		this.connections = [];
		this.generateNetwork();
	}
	
	getRandomItems(array, count) {
		const shuffled = [...array].sort(() => Math.random() - 0.5);
		return shuffled.slice(0, Math.min(count, array.length));
	}
	
	destroy() {
		if (this.animationFrame) {
			cancelAnimationFrame(this.animationFrame);
		}
		if (this.svg && this.svg.parentNode) {
			this.svg.parentNode.removeChild(this.svg);
		}
	}
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
	const heroSection = document.querySelector('.hero');
	if (heroSection) {
		new NetworkBackground(heroSection);
	}
});

