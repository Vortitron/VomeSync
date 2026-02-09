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
├── conftest.py              # Shared fixtures (MockHASSFactory-based)
├── flow_test_framework.py   # Reusable flow test framework (generic, not VomeSync-specific)
├── requirements.txt         # Test dependencies
├── test_config_flow.py      # Config and options flow tests (all 40 steps)
├── test_flow_coverage.py    # Auto-discovery and structural validation of all flow steps
├── test_flow_journeys.py    # Multi-step user workflow simulations
├── test_coordinator.py      # Coordinator logic tests
├── test_switch.py           # Switch platform tests
├── test_sensor.py           # Sensor platform tests
├── test_services.py         # HA service registration tests
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

## Flow Test Framework

The `flow_test_framework.py` module provides **generic, reusable utilities** for testing
any Home Assistant integration's config/options flows — not just VomeSync.

### Classes

| Class | Purpose |
|-------|---------|
| `FlowIntrospector` | Discovers all `async_step_*` methods in a flow class; builds a step registry |
| `FlowResultValidator` | Validates `FlowResult` dicts (type, step_id, menu_options, data_schema) |
| `MockHASSFactory` | Builder-pattern helpers for mock `hass`, `ConfigEntry`, coordinator, entity registry |
| `FlowStepRunner` | Convenience wrapper to run a step + validate the result in one call |

### Using the Framework for Other Plugins

```python
from flow_test_framework import FlowIntrospector, FlowResultValidator, FlowStepRunner, MockHASSFactory

# 1. Discover all steps in your flow class
introspector = FlowIntrospector(MyPluginOptionsFlow)
print(introspector.list_steps())  # ['init', 'configure', 'done', ...]

# 2. Validate menu options reference real steps
validator = FlowResultValidator(known_steps=introspector.step_ids)
result = await flow.async_step_init(None)
validator.assert_valid(result)

# 3. Use FlowStepRunner for multi-step journey tests
runner = FlowStepRunner(flow, auto_validate=True)
result = await runner.run_step("init")
runner.assert_menu("init")
result = await runner.run_step("configure", {"name": "Test"})
runner.assert_form("configure")

# 4. Create mock HA objects
hass = MockHASSFactory.create_hass()
entry = MockHASSFactory.create_config_entry(domain="my_plugin")
```

## Writing New Tests

### Use Existing Fixtures

```python
@pytest.mark.asyncio
async def test_my_feature(hass, config_entry, mock_coordinator):
    """Test description."""
    MockHASSFactory.wire_hass(hass, config_entry, mock_coordinator)
    flow = VomeSyncOptionsFlow(config_entry)
    flow.hass = hass
    # Your test code here
```

### Use FlowStepRunner for Journey Tests

```python
@pytest.mark.asyncio
async def test_full_journey(hass, config_entry, mock_coordinator):
    """Test a multi-step user journey."""
    MockHASSFactory.wire_hass(hass, config_entry, mock_coordinator)
    flow = VomeSyncOptionsFlow(config_entry)
    flow.hass = hass
    runner = FlowStepRunner(flow, auto_validate=False)

    result = await runner.run_step("init")
    runner.assert_menu("init")

    result = await runner.run_step("create_switch", {
        "name": "My Switch",
        "description": "",
        "location": "",
        "category": "Other",
        "publicize": False,
        "advanced_fields": False,
        "show_signing_key_after": False,
    })
    runner.assert_menu("manage_switch_action")
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

- **Config Flow**: 100% step coverage (all 40 steps tested)
- **Flow Journeys**: 9 multi-step user workflows
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

