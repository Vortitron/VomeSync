#!/bin/bash

# VomeSync Deployment Script
# This script handles the deployment of VomeSync services

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_ROOT"

# Prefer modern Docker Compose v2 (`docker compose`), fall back to legacy `docker-compose`
compose() {
	if command -v docker &> /dev/null && docker compose version &> /dev/null; then
		docker compose "$@"
		return
	fi

	if command -v docker-compose &> /dev/null; then
		docker-compose "$@"
		return
	fi

	log_error "Docker Compose is not installed. Install Docker Compose v2 (recommended) or docker-compose."
	exit 1
}

# Volume helpers (avoid accidental "data loss" when compose project names change)
docker_volume_exists() {
	local name="${1:?volume name required}"
	docker volume ls --format '{{.Name}}' 2>/dev/null | grep -Fxq "$name"
}

find_single_volume_by_suffix() {
	local suffix="${1:?suffix required}" # e.g. "_redis_data"
	local matches
	mapfile -t matches < <(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E "${suffix}$" || true)

	if [[ ${#matches[@]} -eq 1 ]]; then
		echo "${matches[0]}"
		return 0
	fi

	if [[ ${#matches[@]} -gt 1 ]]; then
		log_warning "Multiple Docker volumes match '${suffix}'. Set VOMESYNC_REDIS_VOLUME_NAME / VOMESYNC_LOGS_VOLUME_NAME in .env to pick the right one."
		return 1
	fi

	return 1
}

resolve_volume_name() {
	local var_name="${1:?var name required}"
	local default_name="${2:?default volume name required}"
	local suffix="${3:?suffix required}"

	# If caller already set an explicit volume name, honour it.
	if [[ -n "${!var_name:-}" ]]; then
		return 0
	fi

	# Prefer the explicit default name if it exists.
	if docker_volume_exists "$default_name"; then
		export "$var_name"="$default_name"
		return 0
	fi

	# Otherwise, if we can unambiguously find a previous volume (from another compose project),
	# adopt it to avoid "losing" persisted data after upgrades or directory changes.
	local candidate
	candidate="$(find_single_volume_by_suffix "$suffix" || true)"
	if [[ -n "$candidate" ]]; then
		export "$var_name"="$candidate"
		log_warning "Adopting existing Docker volume for persistence: ${var_name}=${candidate}"
		return 0
	fi

	# Fall back to the default name (compose will create it on first run).
	export "$var_name"="$default_name"
}

prepare_volumes() {
	resolve_volume_name VOMESYNC_REDIS_VOLUME_NAME vomesync_redis_data "_redis_data"
	resolve_volume_name VOMESYNC_LOGS_VOLUME_NAME vomesync_webserver_logs "_webserver_logs"
}

# Networking helpers
is_port_in_use() {
	local port="${1:?port required}"
	ss -ltnH "sport = :${port}" 2>/dev/null | grep -q .
}

find_free_port() {
	local port="${1:?port required}"
	while is_port_in_use "$port"; do
		port=$((port + 1))
	done
	echo "$port"
}

docker_container_exists() {
	local name="${1:?container name required}"
	docker ps -a --format '{{.Names}}' | grep -Fxq "$name"
}

docker_mapped_port() {
	local name="${1:?container name required}"
	local internal="${2:?internal port required}" # e.g. 443
	local mapping
	if ! docker_container_exists "$name"; then
		return 1
	fi

	# Example output: "0.0.0.0:8443" or "127.0.0.1:6381"
	mapping="$(docker port "$name" "${internal}/tcp" 2>/dev/null | head -n 1 || true)"
	if [[ -z "$mapping" ]]; then
		return 1
	fi

	echo "${mapping##*:}"
}

resolve_host_port() {
	local var_name="${1:?var name required}"
	local default_port="${2:?default port required}"
	local container_name="${3:-}"
	local internal_port="${4:-}"

	# If caller already set an explicit port, honour it.
	if [[ -n "${!var_name:-}" ]]; then
		return 0
	fi

	# If the container already exists, keep the existing published port (stable upgrades).
	if [[ -n "$container_name" && -n "$internal_port" ]]; then
		local existing
		existing="$(docker_mapped_port "$container_name" "$internal_port" || true)"
		if [[ -n "$existing" ]]; then
			export "$var_name"="$existing"
			return 0
		fi
	fi

	# Otherwise, pick a free port starting from the default.
	if is_port_in_use "$default_port"; then
		local free
		free="$(find_free_port "$default_port")"
		export "$var_name"="$free"
		log_warning "Port ${default_port} is in use; using ${var_name}=${free} for this run"
		return 0
	fi

	export "$var_name"="$default_port"
}

prepare_ports() {
	# Production services
	resolve_host_port VOMESYNC_API_HOST_PORT 3090 vomesync-webserver 3090
	resolve_host_port VOMESYNC_WS_HOST_PORT 3001 vomesync-webserver 3001
	resolve_host_port VOMESYNC_WEBSITE_HOST_PORT 8111 vomesync-website 80

	# Dev services
	resolve_host_port VOMESYNC_API_DEV_HOST_PORT 3091 vomesync-webserver-dev 3091
	resolve_host_port VOMESYNC_WS_DEV_HOST_PORT 3002 vomesync-webserver-dev 3002
	resolve_host_port VOMESYNC_WEBSITE_DEV_HOST_PORT 8112 vomesync-website-dev 80
	resolve_host_port VOMESYNC_REDIS_DEV_HOST_PORT 6381 vomesync-redis-dev 6379

	# Proxy
	resolve_host_port VOMESYNC_PROXY_HTTP_HOST_PORT 8080 vomesync-proxy 80
	resolve_host_port VOMESYNC_PROXY_HTTPS_HOST_PORT 8443 vomesync-proxy 443
}

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
	echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
	echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
	echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
	echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
check_root() {
	if [[ $EUID -eq 0 ]]; then
		log_warning "Running as root. This is not recommended for security reasons."
		read -p "Do you want to continue? (y/N): " -n 1 -r
		echo
		if [[ ! $REPLY =~ ^[Yy]$ ]]; then
			log_info "Exiting..."
			exit 1
		fi
	fi
}

# Check prerequisites
check_prerequisites() {
	log_info "Checking prerequisites..."
	
	# Check Docker
	if ! command -v docker &> /dev/null; then
		log_error "Docker is not installed. Please install Docker first."
		exit 1
	fi
	
	# Check Docker Compose (v2 preferred)
	if ! docker compose version &> /dev/null && ! command -v docker-compose &> /dev/null; then
		log_error "Docker Compose is not installed. Please install Docker Compose v2 (recommended) or docker-compose."
		exit 1
	fi
	
	# Check if Docker daemon is running
	if ! docker info &> /dev/null; then
		log_error "Docker daemon is not running. Please start Docker first."
		exit 1
	fi
	
	log_success "Prerequisites check passed"
}

# Setup environment file
setup_environment() {
	local env_file="$DOCKER_DIR/.env"
	local env_example="$DOCKER_DIR/env.example"
	
	if [[ ! -f "$env_file" ]]; then
		if [[ -f "$env_example" ]]; then
			log_info "Creating .env file from template..."
			cp "$env_example" "$env_file"
			
			# Generate secure secrets
			local jwt_secret=$(openssl rand -base64 32 | tr -d "=+/" | cut -c1-32)
			local redis_password=$(openssl rand -base64 24 | tr -d "=+/" | cut -c1-24)
			
			# Update secrets in .env file
			sed -i "s/your-super-secret-jwt-key-change-this-in-production/$jwt_secret/" "$env_file"
			sed -i "s/your-redis-password-change-this/$redis_password/" "$env_file"
			
			log_success "Environment file created with secure secrets"
			log_warning "Please review and update $env_file with your configuration"
		else
			log_error "Environment template file not found: $env_example"
			exit 1
		fi
	else
		log_info "Environment file already exists: $env_file"
	fi
}

# Build and start services
deploy_services() {
	log_info "Building and starting VomeSync services..."
	
	cd "$DOCKER_DIR"
	prepare_volumes
	prepare_ports
	
	# Pull latest images
	log_info "Pulling latest base images..."
	compose pull
	
	# Build custom images
	log_info "Building VomeSync webserver image..."
	compose build vomesync-webserver
	
	# Start services
	log_info "Starting services..."
	compose up -d
	
	# Wait for services to be healthy
	log_info "Waiting for services to be healthy..."
	local max_attempts=30
	local attempt=0
	
	while [[ $attempt -lt $max_attempts ]]; do
		if compose ps | grep -q "healthy"; then
			log_success "Services are healthy"
			break
		fi
		
		log_info "Waiting for services... (attempt $((attempt + 1))/$max_attempts)"
		sleep 5
		((attempt++))
	done
	
	if [[ $attempt -eq $max_attempts ]]; then
		log_error "Services failed to become healthy within timeout"
		compose logs --tail=50
		exit 1
	fi
}

# Show service status
show_status() {
	cd "$DOCKER_DIR"
	prepare_ports
	
	log_info "Service Status:"
	echo
	compose ps
	echo
	
	log_info "Service URLs:"
	echo "  LIVE (production)"
	echo "    Proxy (combined): http://localhost:${VOMESYNC_PROXY_HTTP_HOST_PORT:-8080}  (sync.vome.io)"
	echo "    Proxy (HTTPS):    https://localhost:${VOMESYNC_PROXY_HTTPS_HOST_PORT:-8443}"
	echo "    API (direct):     http://localhost:${VOMESYNC_API_HOST_PORT:-3090}"
	echo "    WebSocket:        ws://localhost:${VOMESYNC_WS_HOST_PORT:-3001}"
	echo "    Website (direct): http://localhost:${VOMESYNC_WEBSITE_HOST_PORT:-8111}"
	echo
	echo "  DEV (development)"
	echo "    API:              http://localhost:${VOMESYNC_API_DEV_HOST_PORT:-3091}"
	echo "    WebSocket:        ws://localhost:${VOMESYNC_WS_DEV_HOST_PORT:-3002}"
	echo "    Website:          http://localhost:${VOMESYNC_WEBSITE_DEV_HOST_PORT:-8112}"
	echo "    Redis (local):    127.0.0.1:${VOMESYNC_REDIS_DEV_HOST_PORT:-6381}"
	echo
	
	log_info "To view logs:"
	echo "  docker compose logs -f [service-name]"
	echo
	
	log_info "To stop services:"
	echo "  docker compose down"
}

# Update services
update_services() {
	log_info "Updating VomeSync services..."
	
	cd "$DOCKER_DIR"
	prepare_volumes
	prepare_ports
	
	# Pull latest code (if git repository)
	if [[ -d "$PROJECT_ROOT/.git" ]]; then
		log_info "Updating git repository..."
		cd "$PROJECT_ROOT"
		if ! git diff --quiet || ! git diff --cached --quiet; then
			log_warning "Git working tree has local changes; skipping git pull"
		else
			# Avoid merges on servers; fast-forward only.
			if ! git pull --ff-only; then
				log_warning "git pull failed; continuing with existing working copy"
			fi
		fi
		cd "$DOCKER_DIR"
	fi
	
	# Rebuild and restart
	log_info "Rebuilding services..."
	compose build --no-cache vomesync-webserver
	compose up -d --force-recreate
	
	log_success "Services updated successfully"
}

update_services_dev() {
	log_info "Updating VomeSync DEV services..."
	
	cd "$DOCKER_DIR"
	prepare_volumes
	prepare_ports
	
	log_info "Rebuilding dev services..."
	compose build --no-cache vomesync-webserver-dev
	compose up -d --force-recreate vomesync-redis-dev vomesync-webserver-dev vomesync-website-dev
	
	log_success "Dev services updated successfully"
}

update_services_live() {
	log_info "Updating VomeSync LIVE services..."
	
	cd "$DOCKER_DIR"
	prepare_volumes
	prepare_ports
	
	# Pull latest code (if git repository)
	if [[ -d "$PROJECT_ROOT/.git" ]]; then
		log_info "Updating git repository..."
		cd "$PROJECT_ROOT"
		if ! git diff --quiet || ! git diff --cached --quiet; then
			log_warning "Git working tree has local changes; skipping git pull"
		else
			# Avoid merges on servers; fast-forward only.
			if ! git pull --ff-only; then
				log_warning "git pull failed; continuing with existing working copy"
			fi
		fi
		cd "$DOCKER_DIR"
	fi
	
	log_info "Rebuilding live services..."
	compose build --no-cache vomesync-webserver
	compose up -d --force-recreate vomesync-redis vomesync-webserver vomesync-website vomesync-proxy
	
	log_success "Live services updated successfully"
}

# Backup data
backup_data() {
	local backup_dir="$PROJECT_ROOT/backups/$(date +%Y%m%d_%H%M%S)"
	
	log_info "Creating backup in $backup_dir..."
	mkdir -p "$backup_dir"
	
	# Backup Redis data
	compose exec -T vomesync-redis redis-cli --rdb - > "$backup_dir/redis_dump.rdb" || true
	
	# Backup logs
	cp -r "$DOCKER_DIR/logs" "$backup_dir/" 2>/dev/null || true
	
	# Backup configuration
	cp "$DOCKER_DIR/.env" "$backup_dir/" 2>/dev/null || true
	
	log_success "Backup created: $backup_dir"
}

# Main function
main() {
	local command="${1:-deploy}"
	
	case "$command" in
		deploy)
			check_root
			check_prerequisites
			setup_environment
			deploy_services
			show_status
			;;
		update)
			update_services
			show_status
			;;
		update-dev)
			update_services_dev
			show_status
			;;
		update-live|push-live)
			update_services_live
			show_status
			;;
		status)
			show_status
			;;
		backup)
			backup_data
			;;
		logs)
			cd "$DOCKER_DIR"
			compose logs -f "${2:-}"
			;;
		stop)
			cd "$DOCKER_DIR"
			log_info "Stopping services..."
			compose down
			log_success "Services stopped"
			;;
		restart)
			cd "$DOCKER_DIR"
			log_info "Restarting services..."
			compose restart
			show_status
			;;
		clean)
			cd "$DOCKER_DIR"
			log_warning "This will remove all containers, images, and data!"
			read -p "Are you sure? (y/N): " -n 1 -r
			echo
			if [[ $REPLY =~ ^[Yy]$ ]]; then
				compose down -v --rmi all
				log_success "Cleanup completed"
			fi
			;;
		*)
			echo "Usage: $0 {deploy|update|update-dev|update-live|push-live|status|logs|stop|restart|backup|clean}"
			echo
			echo "Commands:"
			echo "  deploy  - Initial deployment (default)"
			echo "  update      - Update ALL services (dev + live)"
			echo "  update-dev  - Update DEV services only"
			echo "  update-live - Update LIVE services only"
			echo "  push-live   - Alias for update-live"
			echo "  status  - Show service status"
			echo "  logs    - Show service logs (optionally specify service name)"
			echo "  stop    - Stop all services"
			echo "  restart - Restart all services"
			echo "  backup  - Create data backup"
			echo "  clean   - Remove all containers and data (DESTRUCTIVE)"
			exit 1
			;;
	esac
}

# Run main function with all arguments
main "$@"
