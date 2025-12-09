#!/usr/bin/env bash
# Automated test script for VomeSync Home Assistant integration
# Usage: ./test-ha-integration.sh

set -e

# Configuration (override with environment variables if needed)
HA_URL="${HA_URL:-https://hadev.vome.io}"
HA_TOKEN="${HA_TOKEN:-eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJkZWVjMzRiYTNlY2Y0NjNlYThhMDcyMjUxZGFhNWFlYSIsImlhdCI6MTc2NDk1MTgxNSwiZXhwIjoyMDgwMzExODE1fQ.B-CBRU5NlolZwaiz9fLYuvJIaEck2ui0Pu_fN9FNoB8}"
VOMESYNC_SERVER="${VOMESYNC_SERVER:-http://95.216.77.237:3000}"
CONFIG_ENTRY_ID=""

# Colours for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Colour

function log_info() {
	echo -e "${GREEN}[INFO]${NC} $1"
}

function log_error() {
	echo -e "${RED}[ERROR]${NC} $1"
}

function log_warn() {
	echo -e "${YELLOW}[WARN]${NC} $1"
}

function ha_api_call() {
	local method="$1"
	local endpoint="$2"
	local data="$3"
	
	if [ -n "$data" ]; then
		curl -s -X "$method" \
			-H "Authorization: Bearer $HA_TOKEN" \
			-H "Content-Type: application/json" \
			-d "$data" \
			"${HA_URL}/api/${endpoint}"
	else
		curl -s -X "$method" \
			-H "Authorization: Bearer $HA_TOKEN" \
			"${HA_URL}/api/${endpoint}"
	fi
}

function restart_ha_core() {
	log_info "Restarting Home Assistant Core..."
	ha_api_call POST "services/homeassistant/restart" '{}' || true
	log_info "Waiting for Home Assistant to restart (30s)..."
	sleep 30
}

function get_vomesync_config_entry() {
	log_info "Finding VomeSync config entry..."
	local entries=$(ha_api_call GET "config/config_entries/entry")
	CONFIG_ENTRY_ID=$(echo "$entries" | jq -r '.[] | select(.domain == "vomesync") | .entry_id' | head -n 1)
	
	if [ -z "$CONFIG_ENTRY_ID" ]; then
		log_warn "No VomeSync config entry found"
		return 1
	fi
	
	log_info "Found VomeSync config entry: $CONFIG_ENTRY_ID"
	return 0
}

function create_switch_via_ha() {
	local name="$1"
	local description="$2"
	local location="${3:-Test Location}"
	local category="${4:-Other}"
	local publicise="${5:-false}"
	
	log_info "Creating switch via HA: $name"
	
	# Call the vomesync.create_switch service
	local result=$(ha_api_call POST "services/vomesync/create_switch" '{
		"name": "'"$name"'",
		"description": "'"$description"'",
		"location": "'"$location"'",
		"category": "'"$category"'",
		"publicise": '"$publicise"'
	}')
	
	echo "$result"
}

function get_ha_states() {
	log_info "Fetching all HA states..."
	ha_api_call GET "states" | jq '.[] | select(.entity_id | startswith("switch.vomesync_"))'
}

function test_vomesync_integration() {
	log_info "=== VomeSync Integration Test ==="
	log_info ""
	
	# Check if HA is accessible
	log_info "Checking Home Assistant connectivity..."
	local ha_status=$(curl -s -o /dev/null -w "%{http_code}" \
		-H "Authorization: Bearer $HA_TOKEN" \
		"${HA_URL}/api/")
	
	if [ "$ha_status" != "200" ]; then
		log_error "Cannot connect to Home Assistant (HTTP $ha_status)"
		exit 1
	fi
	log_info "✓ Home Assistant is accessible"
	
	# Check if VomeSync server is accessible
	log_info "Checking VomeSync server connectivity..."
	local server_status=$(curl -s -o /dev/null -w "%{http_code}" "${VOMESYNC_SERVER}/api/health")
	
	if [ "$server_status" != "200" ]; then
		log_error "Cannot connect to VomeSync server (HTTP $server_status)"
		exit 1
	fi
	log_info "✓ VomeSync server is accessible"
	
	# Find VomeSync config entry
	if ! get_vomesync_config_entry; then
		log_error "VomeSync integration not configured in Home Assistant"
		log_info "Please configure the integration manually first"
		exit 1
	fi
	
	# Get current states
	log_info ""
	log_info "Current VomeSync switches in HA:"
	get_ha_states | jq -r '.entity_id' || log_info "  No switches found"
	
	# Create a test switch
	log_info ""
	local timestamp=$(date +%s)
	local switch_name="AutoTest_${timestamp}"
	log_info "Creating test switch: $switch_name"
	
	local create_result=$(create_switch_via_ha \
		"$switch_name" \
		"Automated test switch created at $(date)" \
		"Automated Test" \
		"Test" \
		"false")
	
	log_info "Create result:"
	echo "$create_result" | jq '.' || echo "$create_result"
	
	# Wait for entity to appear
	log_info ""
	log_info "Waiting 5 seconds for entity to appear..."
	sleep 5
	
	# Check if switch appears in HA
	log_info "Checking if switch appears in Home Assistant..."
	local switch_entity=$(get_ha_states | jq -r --arg name "$switch_name" 'select(.attributes.friendly_name == $name) | .entity_id')
	
	if [ -n "$switch_entity" ]; then
		log_info "✓ Switch successfully created: $switch_entity"
		
		# Try toggling the switch
		log_info ""
		log_info "Testing switch toggle..."
		ha_api_call POST "services/switch/toggle" "{\"entity_id\": \"$switch_entity\"}" > /dev/null
		sleep 2
		
		local switch_state=$(ha_api_call GET "states/$switch_entity" | jq -r '.state')
		log_info "Switch state after toggle: $switch_state"
	else
		log_error "✗ Switch not found in Home Assistant"
		log_info "All VomeSync switches:"
		get_ha_states | jq -r '.entity_id'
	fi
	
	log_info ""
	log_info "=== Test Complete ==="
}

# Main execution
case "${1:-test}" in
	restart)
		restart_ha_core
		;;
	test)
		test_vomesync_integration
		;;
	states)
		get_ha_states | jq '.'
		;;
	*)
		echo "Usage: $0 {restart|test|states}"
		echo "  restart - Restart Home Assistant Core"
		echo "  test    - Run integration test (default)"
		echo "  states  - Show all VomeSync switch states"
		exit 1
		;;
esac

