#!/usr/bin/env bash
# Run VomeSync Home Assistant integration tests

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}VomeSync Integration Test Suite${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TEST_DIR="$PROJECT_ROOT/hacs-addon/tests"

# Check if test directory exists
if [ ! -d "$TEST_DIR" ]; then
	echo -e "${RED}Error: Test directory not found at $TEST_DIR${NC}"
	exit 1
fi

# Create/activate virtual environment if it doesn't exist
VENV_DIR="$PROJECT_ROOT/venv"
if [ ! -d "$VENV_DIR" ]; then
	echo -e "${YELLOW}Creating virtual environment...${NC}"
	python3 -m venv "$VENV_DIR"
fi

echo -e "${YELLOW}Activating virtual environment...${NC}"
source "$VENV_DIR/bin/activate"

# Install test dependencies (pin pip to HA requirement)
echo -e "${YELLOW}Installing test dependencies...${NC}"
pip install -q "pip<23.2"
pip install -q -r "$TEST_DIR/requirements.txt"

# Run flake8 linting (optional)
if command -v flake8 &> /dev/null; then
	echo ""
	echo -e "${GREEN}Running flake8 linting...${NC}"
	python -m flake8 \
		--extend-ignore=W191,W293,W291,E501 \
		--max-line-length=120 \
		--exclude=__pycache__,venv,.git \
		"$PROJECT_ROOT/custom_components/vomesync/" || echo -e "${YELLOW}Warning: Linting issues found${NC}"
fi

# Run pytest with coverage
echo ""
echo -e "${GREEN}Running pytest...${NC}"
cd "$TEST_DIR"

python -m pytest \
	-v \
	--tb=short \
	--color=yes \
	.

TEST_EXIT_CODE=$?

echo ""
if [ $TEST_EXIT_CODE -eq 0 ]; then
	echo -e "${GREEN}======================================${NC}"
	echo -e "${GREEN}✓ All tests passed!${NC}"
	echo -e "${GREEN}======================================${NC}"
else
	echo -e "${RED}======================================${NC}"
	echo -e "${RED}✗ Some tests failed${NC}"
	echo -e "${RED}======================================${NC}"
	exit 1
fi

# Optional: Run specific test files
if [ $# -gt 0 ]; then
	echo ""
	echo -e "${GREEN}Running specific tests: $@${NC}"
	python -m pytest -v --tb=short --color=yes "$@"
fi

