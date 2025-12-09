#!/bin/bash

# VomeSync Test Runner Script
# Runs all tests for the VomeSync project

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test configuration
RUN_UNIT_TESTS=true
RUN_INTEGRATION_TESTS=true
RUN_E2E_TESTS=false
RUN_LINTING=true
GENERATE_COVERAGE=true
CLEANUP_AFTER=true

# Parse command line arguments
while [[ $# -gt 0 ]]; do
	case $1 in
		--unit-only)
			RUN_INTEGRATION_TESTS=false
			RUN_E2E_TESTS=false
			shift
			;;
		--integration-only)
			RUN_UNIT_TESTS=false
			RUN_E2E_TESTS=false
			shift
			;;
		--e2e-only)
			RUN_UNIT_TESTS=false
			RUN_INTEGRATION_TESTS=false
			RUN_E2E_TESTS=true
			shift
			;;
		--with-e2e)
			RUN_E2E_TESTS=true
			shift
			;;
		--no-lint)
			RUN_LINTING=false
			shift
			;;
		--no-coverage)
			GENERATE_COVERAGE=false
			shift
			;;
		--no-cleanup)
			CLEANUP_AFTER=false
			shift
			;;
		--help)
			echo "Usage: $0 [OPTIONS]"
			echo ""
			echo "Options:"
			echo "  --unit-only       Run only unit tests"
			echo "  --integration-only Run only integration tests"
			echo "  --e2e-only        Run only end-to-end tests"
			echo "  --with-e2e        Include end-to-end tests (requires running server)"
			echo "  --no-lint         Skip linting"
			echo "  --no-coverage     Skip coverage report generation"
			echo "  --no-cleanup      Don't cleanup test containers after running"
			echo "  --help            Show this help message"
			exit 0
			;;
		*)
			echo "Unknown option: $1"
			echo "Use --help for usage information"
			exit 1
			;;
	esac
done

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

# Check prerequisites
check_prerequisites() {
	log_info "Checking prerequisites..."
	
	# Check Node.js
	if ! command -v node &> /dev/null; then
		log_error "Node.js is not installed"
		exit 1
	fi
	
	# Check npm
	if ! command -v npm &> /dev/null; then
		log_error "npm is not installed"
		exit 1
	fi
	
	# Check Python
	if ! command -v python3 &> /dev/null; then
		log_error "Python 3 is not installed"
		exit 1
	fi
	
	# Check Docker (for E2E tests)
	if [[ "$RUN_E2E_TESTS" == "true" ]] && ! command -v docker &> /dev/null; then
		log_error "Docker is required for E2E tests"
		exit 1
	fi
	
	log_success "Prerequisites check passed"
}

# Setup test environment
setup_test_environment() {
	log_info "Setting up test environment..."
	
	# Install webserver dependencies
	cd "$PROJECT_ROOT/webserver"
	if [[ ! -d "node_modules" ]]; then
		log_info "Installing webserver dependencies..."
		npm install
	fi
	
	# Install Python test dependencies
	if [[ "$RUN_UNIT_TESTS" == "true" ]] || [[ "$RUN_E2E_TESTS" == "true" ]]; then
		log_info "Installing Python test dependencies..."
		
		# Create virtual environment if it doesn't exist
		if [[ ! -d "$PROJECT_ROOT/venv" ]]; then
			python3 -m venv "$PROJECT_ROOT/venv"
		fi
		
		source "$PROJECT_ROOT/venv/bin/activate"
		
		# Install HACS addon test dependencies
		if [[ -f "$PROJECT_ROOT/hacs-addon/tests/requirements.txt" ]]; then
			pip install -r "$PROJECT_ROOT/hacs-addon/tests/requirements.txt"
		fi
		
		# Install E2E test dependencies
		if [[ "$RUN_E2E_TESTS" == "true" ]] && [[ -f "$PROJECT_ROOT/tests/e2e/requirements.txt" ]]; then
			pip install -r "$PROJECT_ROOT/tests/e2e/requirements.txt"
		fi
	fi
	
	log_success "Test environment setup complete"
}

# Run linting
run_linting() {
	if [[ "$RUN_LINTING" != "true" ]]; then
		return 0
	fi
	
	log_info "Running linting checks..."
	
	cd "$PROJECT_ROOT/webserver"
	
	# JavaScript/Node.js linting
	log_info "Running ESLint..."
	if npm run lint; then
		log_success "ESLint passed"
	else
		log_error "ESLint failed"
		return 1
	fi
	
	# Python linting (if available)
	if command -v flake8 &> /dev/null; then
		log_info "Running flake8..."
		if flake8 "$PROJECT_ROOT/hacs-addon" "$PROJECT_ROOT/tests"; then
			log_success "flake8 passed"
		else
			log_warning "flake8 issues found (not blocking)"
		fi
	fi
	
	log_success "Linting checks completed"
}

# Run webserver tests
run_webserver_tests() {
	log_info "Running webserver tests..."
	
	cd "$PROJECT_ROOT/webserver"
	
	local test_cmd="npm run test"
	if [[ "$GENERATE_COVERAGE" == "true" ]]; then
		test_cmd="npm run test:coverage"
	fi
	
	if [[ "$RUN_UNIT_TESTS" == "true" ]] && [[ "$RUN_INTEGRATION_TESTS" == "true" ]]; then
		# Run all tests
		if $test_cmd; then
			log_success "All webserver tests passed"
		else
			log_error "Webserver tests failed"
			return 1
		fi
	elif [[ "$RUN_UNIT_TESTS" == "true" ]]; then
		# Run only unit tests
		if npm run test:unit; then
			log_success "Webserver unit tests passed"
		else
			log_error "Webserver unit tests failed"
			return 1
		fi
	elif [[ "$RUN_INTEGRATION_TESTS" == "true" ]]; then
		# Run only integration tests
		if npm run test:integration; then
			log_success "Webserver integration tests passed"
		else
			log_error "Webserver integration tests failed"
			return 1
		fi
	fi
}

# Run Home Assistant integration tests
run_hass_tests() {
	if [[ "$RUN_UNIT_TESTS" != "true" ]]; then
		return 0
	fi
	
	log_info "Running Home Assistant integration tests..."
	
	cd "$PROJECT_ROOT"
	source venv/bin/activate
	
	# Run pytest for HACS addon
	if pytest hacs-addon/tests/ -v --tb=short; then
		log_success "Home Assistant integration tests passed"
	else
		log_error "Home Assistant integration tests failed"
		return 1
	fi
}

# Start test services for E2E tests
start_test_services() {
	if [[ "$RUN_E2E_TESTS" != "true" ]]; then
		return 0
	fi
	
	log_info "Starting test services for E2E tests..."
	
	cd "$PROJECT_ROOT/docker"
	
	# Copy environment template
	if [[ ! -f ".env" ]]; then
		cp env.example .env
		# Generate test secrets
		sed -i "s/your-super-secret-jwt-key-change-this-in-production/test-jwt-secret-$(date +%s)/" .env
		sed -i "s/your-redis-password-change-this/test-redis-pass-$(date +%s)/" .env
	fi
	
	# Start services
	if docker-compose up -d; then
		log_info "Waiting for services to be ready..."
		
		# Wait for API to be available
		local max_attempts=30
		local attempt=0
		while [[ $attempt -lt $max_attempts ]]; do
			if curl -f http://localhost:3000/api/health &> /dev/null; then
				log_success "Test services are ready"
				return 0
			fi
			
			sleep 2
			((attempt++))
		done
		
		log_error "Test services failed to start within timeout"
		return 1
	else
		log_error "Failed to start test services"
		return 1
	fi
}

# Run E2E tests
run_e2e_tests() {
	if [[ "$RUN_E2E_TESTS" != "true" ]]; then
		return 0
	fi
	
	log_info "Running end-to-end tests..."
	
	cd "$PROJECT_ROOT"
	source venv/bin/activate
	
	# Run E2E tests
	if pytest tests/e2e/ -v --tb=short; then
		log_success "End-to-end tests passed"
	else
		log_error "End-to-end tests failed"
		return 1
	fi
}

# Cleanup test services
cleanup_test_services() {
	if [[ "$CLEANUP_AFTER" != "true" ]] || [[ "$RUN_E2E_TESTS" != "true" ]]; then
		return 0
	fi
	
	log_info "Cleaning up test services..."
	
	cd "$PROJECT_ROOT/docker"
	
	if docker-compose down -v; then
		log_success "Test services cleaned up"
	else
		log_warning "Failed to cleanup test services"
	fi
}

# Generate test report
generate_test_report() {
	if [[ "$GENERATE_COVERAGE" != "true" ]]; then
		return 0
	fi
	
	log_info "Generating test report..."
	
	cd "$PROJECT_ROOT/webserver"
	
	if [[ -d "coverage" ]]; then
		log_info "Coverage report available at: webserver/coverage/lcov-report/index.html"
		
		# Generate coverage badge if possible
		if command -v lcov &> /dev/null; then
			local coverage_percentage=$(lcov --summary coverage/lcov.info 2>/dev/null | grep "lines" | grep -o '[0-9.]*%' | head -1)
			if [[ -n "$coverage_percentage" ]]; then
				log_info "Test coverage: $coverage_percentage"
			fi
		fi
	fi
	
	log_success "Test report generated"
}

# Main test execution
main() {
	local start_time=$(date +%s)
	local exit_code=0
	
	log_info "Starting VomeSync test suite..."
	
	# Run test phases
	check_prerequisites || exit_code=1
	
	if [[ $exit_code -eq 0 ]]; then
		setup_test_environment || exit_code=1
	fi
	
	if [[ $exit_code -eq 0 ]]; then
		run_linting || exit_code=1
	fi
	
	if [[ $exit_code -eq 0 ]] && [[ "$RUN_E2E_TESTS" == "true" ]]; then
		start_test_services || exit_code=1
	fi
	
	if [[ $exit_code -eq 0 ]] && ([[ "$RUN_UNIT_TESTS" == "true" ]] || [[ "$RUN_INTEGRATION_TESTS" == "true" ]]); then
		run_webserver_tests || exit_code=1
	fi
	
	if [[ $exit_code -eq 0 ]]; then
		run_hass_tests || exit_code=1
	fi
	
	if [[ $exit_code -eq 0 ]]; then
		run_e2e_tests || exit_code=1
	fi
	
	# Always try to cleanup
	cleanup_test_services
	
	# Generate report
	if [[ $exit_code -eq 0 ]]; then
		generate_test_report
	fi
	
	# Summary
	local end_time=$(date +%s)
	local duration=$((end_time - start_time))
	
	echo ""
	if [[ $exit_code -eq 0 ]]; then
		log_success "All tests completed successfully in ${duration}s"
	else
		log_error "Tests failed after ${duration}s"
	fi
	
	exit $exit_code
}

# Trap to ensure cleanup on exit
trap cleanup_test_services EXIT

# Run main function
main "$@"
