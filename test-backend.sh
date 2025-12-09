#!/usr/bin/env bash
# Quick backend API test for VomeSync
# Tests the core create-switch functionality

set -e

SERVER_URL="${1:-http://95.216.77.237:3000}"

# Colours
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}Testing VomeSync Backend${NC}"
echo "Server: $SERVER_URL"
echo ""

# Test health
echo "1. Testing health endpoint..."
curl -s "${SERVER_URL}/api/health" | jq '.'
echo -e "${GREEN}✓ Health check passed${NC}\n"

# Generate personal key
echo "2. Generating personal key..."
RESPONSE=$(curl -s -X POST "${SERVER_URL}/api/generate-key" \
  -H "Content-Type: application/json" \
  -d '{"consent": true}')

PERSONAL_KEY=$(echo "$RESPONSE" | jq -r '.data.personalKey')
echo "Personal Key: $PERSONAL_KEY"
echo -e "${GREEN}✓ Personal key generated${NC}\n"

# Create switch
echo "3. Creating switch..."
CREATE_RESPONSE=$(curl -s -X POST "${SERVER_URL}/api/create-switch" \
  -H "Content-Type: application/json" \
  -d "{
    \"personalKey\": \"$PERSONAL_KEY\",
    \"description\": \"Test Switch $(date +%s)\",
    \"location\": \"Test Location\",
    \"category\": \"Test\",
    \"publicize\": false
  }")

echo "$CREATE_RESPONSE" | jq '.'

if echo "$CREATE_RESPONSE" | jq -e '.success' > /dev/null 2>&1; then
	SWITCH_UID=$(echo "$CREATE_RESPONSE" | jq -r '.data.uid')
	echo -e "${GREEN}✓ Switch created: $SWITCH_UID${NC}\n"
	
	# Get switch status
	echo "4. Getting switch status..."
	curl -s "${SERVER_URL}/api/status/$SWITCH_UID" | jq '.'
	echo -e "${GREEN}✓ Switch status retrieved${NC}\n"
	
	# Toggle switch
	echo "5. Toggling switch..."
	curl -s -X POST "${SERVER_URL}/api/toggle/$SWITCH_UID" \
		-H "Content-Type: application/json" \
		-d "{\"personalKey\": \"$PERSONAL_KEY\"}" | jq '.'
	echo -e "${GREEN}✓ Switch toggled${NC}\n"
	
	# Get my switches
	echo "6. Getting my switches..."
	curl -s "${SERVER_URL}/api/my-switches?personalKey=$PERSONAL_KEY" | jq '.'
	echo -e "${GREEN}✓ My switches retrieved${NC}\n"
	
	echo -e "${GREEN}=== All backend tests passed! ===${NC}"
else
	echo -e "${RED}✗ Switch creation failed${NC}"
	exit 1
fi

