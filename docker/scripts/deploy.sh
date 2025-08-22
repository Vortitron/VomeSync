#!/bin/bash

# VomeSync Deployment Script
# This script handles the deployment of VomeSync services

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_ROOT"

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
	
	# Check Docker Compose
	if ! command -v docker-compose &> /dev/null; then
		log_error "Docker Compose is not installed. Please install Docker Compose first."
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
	
	# Pull latest images
	log_info "Pulling latest base images..."
	docker-compose pull
	
	# Build custom images
	log_info "Building VomeSync webserver image..."
	docker-compose build vomesync-webserver
	
	# Start services
	log_info "Starting services..."
	docker-compose up -d
	
	# Wait for services to be healthy
	log_info "Waiting for services to be healthy..."
	local max_attempts=30
	local attempt=0
	
	while [[ $attempt -lt $max_attempts ]]; do
		if docker-compose ps | grep -q "healthy"; then
			log_success "Services are healthy"
			break
		fi
		
		log_info "Waiting for services... (attempt $((attempt + 1))/$max_attempts)"
		sleep 5
		((attempt++))
	done
	
	if [[ $attempt -eq $max_attempts ]]; then
		log_error "Services failed to become healthy within timeout"
		docker-compose logs --tail=50
		exit 1
	fi
}

# Show service status
show_status() {
	log_info "Service Status:"
	echo
	docker-compose ps
	echo
	
	log_info "Service URLs:"
	echo "  API Server: http://localhost:3000"
	echo "  WebSocket: ws://localhost:3001"
	echo "  Website: http://localhost:8080"
	echo "  Proxy: http://localhost:8080 (combined)"
	echo
	
	log_info "To view logs:"
	echo "  docker-compose logs -f [service-name]"
	echo
	
	log_info "To stop services:"
	echo "  docker-compose down"
}

# Update services
update_services() {
	log_info "Updating VomeSync services..."
	
	cd "$DOCKER_DIR"
	
	# Pull latest code (if git repository)
	if [[ -d "$PROJECT_ROOT/.git" ]]; then
		log_info "Pulling latest code..."
		cd "$PROJECT_ROOT"
		git pull
		cd "$DOCKER_DIR"
	fi
	
	# Rebuild and restart
	log_info "Rebuilding services..."
	docker-compose build --no-cache vomesync-webserver
	docker-compose up -d --force-recreate
	
	log_success "Services updated successfully"
}

# Backup data
backup_data() {
	local backup_dir="$PROJECT_ROOT/backups/$(date +%Y%m%d_%H%M%S)"
	
	log_info "Creating backup in $backup_dir..."
	mkdir -p "$backup_dir"
	
	# Backup Redis data
	docker-compose exec -T vomesync-redis redis-cli --rdb - > "$backup_dir/redis_dump.rdb" || true
	
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
		status)
			show_status
			;;
		backup)
			backup_data
			;;
		logs)
			cd "$DOCKER_DIR"
			docker-compose logs -f "${2:-}"
			;;
		stop)
			cd "$DOCKER_DIR"
			log_info "Stopping services..."
			docker-compose down
			log_success "Services stopped"
			;;
		restart)
			cd "$DOCKER_DIR"
			log_info "Restarting services..."
			docker-compose restart
			show_status
			;;
		clean)
			cd "$DOCKER_DIR"
			log_warning "This will remove all containers, images, and data!"
			read -p "Are you sure? (y/N): " -n 1 -r
			echo
			if [[ $REPLY =~ ^[Yy]$ ]]; then
				docker-compose down -v --rmi all
				log_success "Cleanup completed"
			fi
			;;
		*)
			echo "Usage: $0 {deploy|update|status|logs|stop|restart|backup|clean}"
			echo
			echo "Commands:"
			echo "  deploy  - Initial deployment (default)"
			echo "  update  - Update services with latest code"
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
