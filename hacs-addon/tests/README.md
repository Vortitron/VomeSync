# VomeSync Integration Tests

Automated test suite for the VomeSync Home Assistant integration.

## Overview

The test suite covers:
- **Config Flow**: Initial setup and options flow (import switches, create, subscribe, link entities)
- **Coordinator**: Data fetching, caching, rate limiting, linked entity triggers
- **Switch Platform**: Entity creation, state management, service calls
- **API Client**: HTTP requests, error handling
- **WebSocket Client**: Real-time connections, reconnection logic, exponential backoff

## Running Tests

### Quick Start

```bash
# Run all tests
./scripts/run-integration-tests.sh

# Run specific test file
./scripts/run-integration-tests.sh test_config_flow.py

# Run specific test function
./scripts/run-integration-tests.sh test_config_flow.py::test_options_flow_import_switches
```

### Manual Execution

```bash
# Activate virtual environment
source venv/bin/activate

# Install dependencies
pip install -r hacs-addon/tests/requirements.txt

# Run pytest
cd hacs-addon/tests
pytest -v
```

## Test Structure

```
hacs-addon/tests/
├── conftest.py              # Shared fixtures
├── requirements.txt         # Test dependencies
├── test_config_flow.py      # Config and options flow tests
├── test_coordinator.py      # Coordinator logic tests
├── test_switch.py           # Switch platform tests
├── test_api_client.py       # API client tests
└── README.md                # This file
```

## Key Test Scenarios

### Import/Cache System

```python
# Test auto-import on create
test_coordinator_auto_imports_new_switch()

# Test cache updates from API
test_coordinator_updates_cache_on_fetch()

# Test switch creation from cache
test_switch_created_from_imported_cache()
```

### Rate Limiting

```python
# Test toggle rate limiting (1s cooldown)
test_coordinator_rate_limits_toggle()

# Test linked entity trigger rate limiting (2s cooldown)
test_coordinator_rate_limits_linked_entity_triggers()
```

### Entity Linking

```python
# Test linking entities via options flow
test_options_flow_link_entities()

# Test linked entities are triggered
test_coordinator_triggers_linked_entities()

# Test attributes show linked entities
test_switch_extra_attributes_include_linked_entities()
```

### Multi-Installation Support

```python
# Test selective import
test_options_flow_import_switches()

# Test remove from installation (not delete from server)
test_options_flow_remove_from_installation()
```

## Writing New Tests

### Use Existing Fixtures

```python
@pytest.mark.asyncio
async def test_my_feature(hass, config_entry, mock_switch_data):
    """Test description."""
    # Your test code here
    pass
```

### Mock Coordinator

```python
mock_coordinator = MagicMock()
mock_coordinator.switches = {"uid": {"state": True}}
mock_coordinator.toggle_switch = AsyncMock(return_value=True)
```

### Assert Options Updates

```python
hass.config_entries.async_update_entry.assert_called()
call_args = hass.config_entries.async_update_entry.call_args
updated_options = call_args[1]["options"]
assert "imported_switches" in updated_options
```

## Continuous Integration

### GitHub Actions Example

```yaml
name: Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Run tests
        run: ./scripts/run-integration-tests.sh
```

## Coverage Goals

- **Config Flow**: 95%+ coverage
- **Coordinator**: 90%+ coverage
- **Switch Platform**: 90%+ coverage
- **API Client**: 85%+ coverage

## Troubleshooting

### Import Errors

```bash
# Ensure custom_components is in Python path
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

### Async Warnings

```bash
# Install pytest-asyncio
pip install pytest-asyncio
```

### Mock Issues

```bash
# Use unittest.mock for HA components
from unittest.mock import AsyncMock, MagicMock, patch
```

## Best Practices

1. **Always use `@pytest.mark.asyncio`** for async tests
2. **Mock external dependencies** (API calls, WebSocket connections)
3. **Test edge cases** (rate limits, errors, empty data)
4. **Verify state changes** (options updates, cache updates)
5. **Use descriptive names** (`test_coordinator_updates_cache_on_fetch`)

## Related Documentation

- [Home Assistant Testing Guide](https://developers.home-assistant.io/docs/development_testing)
- [pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio Documentation](https://pytest-asyncio.readthedocs.io/)

