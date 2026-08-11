# VomeSync Testing Guide

This document provides comprehensive information about testing the VomeSync project, including unit tests, integration tests, end-to-end tests, and continuous integration.

## Table of Contents

1. [Test Overview](#test-overview)
2. [Prerequisites](#prerequisites)
3. [Running Tests](#running-tests)
4. [Test Types](#test-types)
5. [Test Configuration](#test-configuration)
6. [Writing Tests](#writing-tests)
7. [Continuous Integration](#continuous-integration)
8. [Upgrading Home Assistant](#upgrading-home-assistant)
9. [Troubleshooting](#troubleshooting)

## Test Overview

VomeSync includes a comprehensive test suite covering:

- **Unit Tests**: Test individual functions and components in isolation
- **Integration Tests**: Test API endpoints and WebSocket functionality
- **End-to-End Tests**: Test complete user workflows
- **Component Tests**: Test Home Assistant integration components
- **Performance Tests**: Verify system performance under load

### Test Coverage

- **Webserver**: Node.js/JavaScript tests using Jest
- **Home Assistant Integration**: Python tests using pytest
- **API Integration**: REST API endpoint testing
- **WebSocket**: Real-time communication testing
- **Docker**: Container and deployment testing

## Prerequisites

### Software Requirements

- **Node.js 18+** and npm
- **Python 3.11+** and pip
- **Docker** and Docker Compose (for E2E tests)
- **Redis** (for integration tests)

### Installation

```bash
# Install Node.js dependencies
cd webserver
npm install

# Install Python test dependencies
pip install -r hacs-addon/tests/requirements.txt
pip install -r tests/e2e/requirements.txt

# For linting (optional)
pip install flake8 black isort
```

## Running Tests

### Quick Test Run

```bash
# Run all tests (excludes E2E by default)
./scripts/run-tests.sh

# Run with different options
./scripts/run-tests.sh --help
```

### Individual Test Types

```bash
# Unit tests only
./scripts/run-tests.sh --unit-only

# Integration tests only  
./scripts/run-tests.sh --integration-only

# Include E2E tests (requires Docker)
./scripts/run-tests.sh --with-e2e

# E2E tests only
./scripts/run-tests.sh --e2e-only
```

### Manual Test Commands

**Webserver Tests:**
```bash
cd webserver

# All tests
npm test

# Unit tests only
npm run test:unit

# Integration tests only
npm run test:integration

# With coverage
npm run test:coverage

# Watch mode
npm run test:watch
```

**Home Assistant Integration Tests:**
```bash
# Run pytest for HACS addon
pytest hacs-addon/tests/ -v

# With coverage
pytest hacs-addon/tests/ --cov=custom_components --cov-report=html
```

**End-to-End Tests:**
```bash
# Start services first
cd docker
cp env.example .env
docker-compose up -d

# Run E2E tests
pytest tests/e2e/ -v

# Cleanup
docker-compose down -v
```

## Test Types

### Unit Tests

**Location**: `webserver/tests/unit/`

Test individual functions and utilities in isolation:

```javascript
// Example unit test
describe('AuthManager', () => {
  test('should generate valid UUID', () => {
    const key = AuthManager.generatePersonalKey();
    expect(key).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}/);
  });
});
```

**Covers:**
- Utility functions (auth, validation, Redis operations)
- Configuration loading
- Data transformation functions
- Error handling logic

### Integration Tests

**Location**: `webserver/tests/integration/`

Test API endpoints and WebSocket functionality:

```javascript
// Example integration test
describe('API Integration', () => {
  test('should create switch', async () => {
    const response = await request(app)
      .post('/api/create-switch')
      .set('X-Personal-Key', personalKey)
      .send(switchData)
      .expect(200);
    
    expect(response.body.data.uid).toBeDefined();
  });
});
```

**Covers:**
- REST API endpoints
- WebSocket connections and messaging
- Database interactions
- Authentication and authorization
- Error responses and status codes

### Home Assistant Component Tests

**Location**: `hacs-addon/tests/`

Test Home Assistant integration components:

```python
# Example component test
@pytest.mark.asyncio
async def test_coordinator_update(coordinator):
    coordinator.api_client.get_my_switches = AsyncMock(return_value=[])
    result = await coordinator._async_update_data()
    assert "switches" in result
```

**Covers:**
- API client functionality
- Coordinator data management
- Config flow UI
- Switch and sensor entities
- WebSocket client behavior

### End-to-End Tests

**Location**: `tests/e2e/`

Test complete user workflows:

```python
# Example E2E test
@pytest.mark.asyncio
async def test_complete_workflow(e2e_test):
    # Generate key
    key = await e2e_test.generate_personal_key()
    
    # Create switch
    switch = await e2e_test.create_switch(key, config)
    
    # Connect WebSocket
    ws = await e2e_test.connect_websocket(switch["uid"])
    
    # Toggle and verify update
    await e2e_test.toggle_switch(key, switch["uid"])
    message = await e2e_test.wait_for_websocket_message(ws)
    assert message["state"] is True
```

**Covers:**
- Complete API workflows
- Real-time WebSocket communication
- Multi-client scenarios
- Authentication flows
- Error handling scenarios

## Test Configuration

### Jest Configuration

**File**: `webserver/jest.config.js`

```javascript
module.exports = {
  testEnvironment: 'node',
  setupFilesAfterEnv: ['<rootDir>/tests/setup.js'],
  collectCoverageFrom: ['src/**/*.js'],
  coverageDirectory: 'coverage',
  maxWorkers: 1 // Sequential execution for Redis
};
```

### Test Redis (pinned on purpose)

The jest suites run against a throwaway Redis started by `redis-memory-server`
(`webserver/tests/globalSetup.js`), which downloads and compiles `redis-server`
from source the first time it is asked for a version.

`webserver/package.json` pins that version:

```json
"redisMemoryServer": { "version": "7.4.6" }
```

Two reasons the pin has to stay:

- **The default is a moving target.** `redis-memory-server` defaults to
  `stable`, i.e. *whatever redis.io publishes today*. That is now Redis 8.x,
  whose tarball bundles the RedisBloom/RediSearch/RedisJSON/TimeSeries modules
  and needs `cmake`, `rustc` and autotools to build. CI has none of them, so
  `npm ci` fails at postinstall — with nothing in this repo having changed.
- **It should match production.** `docker/docker-compose.yml` runs
  `redis:7-alpine`. Before the pin, developer machines were quietly testing
  against Redis 8.3 while production ran 7.

If you deliberately move the pin, bump `redis:7-alpine` with it.

CI additionally sets `REDISMS_DOWNLOAD_DIR` to a path under `JENKINS_HOME`
(`jenkins/pipelines/Jenkinsfile.vomesync-ci`), because the pipeline's `cleanWs()`
deletes the in-workspace cache and every build would otherwise recompile Redis.

### Pytest Configuration

**File**: `hacs-addon/tests/conftest.py`

```python
@pytest.fixture
def hass():
    """Mock Home Assistant instance."""
    return MagicMock(spec=HomeAssistant)
```

### Environment Variables

**Test Environment:**
```bash
NODE_ENV=test
JWT_SECRET=test-jwt-secret
REDIS_HOST=localhost
REDIS_PORT=6380  # Test Redis port
LOG_LEVEL=error  # Reduce log noise
```

## Writing Tests

### Test Structure Guidelines

1. **Arrange**: Set up test data and conditions
2. **Act**: Execute the function or endpoint being tested  
3. **Assert**: Verify the expected outcomes

### Best Practices

**General:**
- Use descriptive test names that explain what is being tested
- Keep tests isolated and independent
- Clean up test data after each test
- Mock external dependencies
- Test both success and failure scenarios

**JavaScript/Jest:**
```javascript
describe('Component Name', () => {
  beforeEach(() => {
    // Setup before each test
  });

  afterEach(() => {
    // Cleanup after each test
  });

  test('should perform specific action', async () => {
    // Test implementation
  });
});
```

**Python/Pytest:**
```python
class TestComponentName:
    @pytest.fixture
    def setup_data(self):
        return {"key": "value"}

    @pytest.mark.asyncio
    async def test_async_function(self, setup_data):
        # Test implementation
        pass
```

### Mocking Guidelines

**API Responses:**
```javascript
const mockApiClient = {
  createSwitch: jest.fn().mockResolvedValue(mockSwitchData),
  toggleSwitch: jest.fn().mockResolvedValue(mockToggleResult)
};
```

**WebSocket Messages:**
```javascript
const mockWebSocket = {
  send: jest.fn(),
  close: jest.fn(),
  on: jest.fn()
};
```

### Test Data

Use factories or fixtures for consistent test data:

```javascript
// Test utilities
global.testUtils = {
  generateTestUUID: () => `test-${Math.random().toString(36)}`,
  createTestSwitchData: (overrides = {}) => ({
    description: 'Test Switch',
    category: 'Test',
    ...overrides
  })
};
```

## Continuous Integration

### GitHub Actions

**File**: `.github/workflows/test.yml`

The CI pipeline includes:

1. **Linting**: ESLint, flake8, black, isort
2. **Unit Tests**: Jest for webserver, pytest for HA integration
3. **Integration Tests**: API and WebSocket testing
4. **E2E Tests**: Complete workflow testing
5. **Coverage**: Code coverage reporting
6. **Security**: Vulnerability scanning
7. **Docker**: Container build testing

### Coverage Requirements

- **Minimum Coverage**: 80% for critical components
- **Coverage Reports**: Generated in HTML and XML formats
- **Upload**: Coverage data sent to Codecov

### Quality Gates

Tests must pass for:
- All linting checks
- All unit and integration tests
- Security vulnerability scan
- Docker build and deployment test

## Performance Testing

### Load Testing

Use the included performance test scripts:

```bash
# Test WebSocket performance
node webserver/tests/performance/websocket-load.js

# Test API performance
node webserver/tests/performance/api-load.js
```

### Memory Monitoring

Monitor memory usage during tests:

```bash
# Enable memory monitoring
NODE_OPTIONS="--max-old-space-size=4096" npm test
```

### Metrics Collection

Key performance metrics:
- Response times (95th percentile < 500ms)
- WebSocket connection capacity (>1000 concurrent)
- Memory usage (stable over time)
- Error rates (<1% under normal load)

## Troubleshooting

### Common Issues

**1. Redis Connection Errors**
```bash
# Start Redis manually
redis-server --port 6380

# Or use Docker
docker run -d --name test-redis -p 6380:6379 redis:alpine
```

**2. Port Conflicts**
```bash
# Check what's using the port
sudo netstat -tulpn | grep :3000

# Kill process if needed
sudo kill -9 <PID>
```

**3. WebSocket Connection Issues**
```bash
# Test WebSocket endpoint manually
wscat -c ws://localhost:3001/ws?uid=test-uid
```

**4. Test Timeouts**
```bash
# Increase Jest timeout
jest.setTimeout(30000);

# Increase pytest timeout
pytest --timeout=60
```

**5. Memory Issues**
```bash
# Increase Node.js memory limit
NODE_OPTIONS="--max-old-space-size=8192" npm test
```

### Debug Mode

Enable debug logging:

```bash
# Jest debug mode
npm test -- --verbose --detectOpenHandles

# Pytest debug mode
pytest -v -s --tb=long

# E2E debug mode
pytest tests/e2e/ -v -s --log-cli-level=DEBUG
```

### Test Data Cleanup

If tests fail due to stale data:

```bash
# Clear Redis test data
redis-cli -p 6380 FLUSHDB

# Reset Docker environment
cd docker && docker-compose down -v
```

### CI/CD Troubleshooting

**View GitHub Actions logs:**
1. Go to repository → Actions tab
2. Click on failed workflow
3. Expand failed job steps
4. Check logs for specific error messages

**Local CI simulation:**
```bash
# Run the same commands as CI
./scripts/run-tests.sh --with-e2e --no-cleanup
```

## Test Metrics and Reporting

### Coverage Reports

- **Location**: `webserver/coverage/lcov-report/index.html`
- **Format**: HTML, LCOV, XML
- **Threshold**: 80% minimum coverage

### Test Results

- **Format**: JUnit XML for CI integration
- **Location**: `test-results/` directory
- **Retention**: 30 days in CI artifacts

### Performance Benchmarks

- **API Response Times**: < 100ms average
- **WebSocket Latency**: < 50ms for state updates
- **Memory Usage**: < 512MB for webserver
- **Concurrent Connections**: > 1000 WebSocket clients

This comprehensive testing setup ensures VomeSync maintains high quality, reliability, and performance across all components and deployment scenarios.

## Upgrading Home Assistant

**Run this first when bumping the pinned Home Assistant version:**

```bash
venv/bin/python -m pytest hacs-addon/tests/test_ha_compat_contract.py -v
```

`test_ha_compat_contract.py` pins the parts of Home Assistant's *behaviour* that
Vome depends on but which are not a documented API — so a release can change
them without a deprecation warning and without breaking an import. Each
assertion names what breaks and where.

A failure here is never flaky. It means a released behaviour we rely on has
changed and something of ours is now wrong. What it currently guards:

| Coupling | Depends on it | Why it is silent if it breaks |
| --- | --- | --- |
| A wrong password returns **HTTP 200** with `errors.base = invalid_auth`/`invalid_code` | `webserver/src/proxy/loginGuard.js` | The brute-force guard stops counting failures. No error, no log — just an unlimited password oracle on every friendly domain in `open` mode. |
| `FlowResultType.FORM`/`CREATE_ENTRY` serialise to `form`/`create_entry` | `loginGuard.classifyLoginResponse` | Same as above. |
| `/auth/login_flow/{flow_id}` is still the login endpoint | `loginGuard.LOGIN_FLOW_PATH_RE` | The guard watches a path nothing posts to. |
| Core 400s an unexpected `X-Forwarded-For` | `uiProxy.VOME_HOP_HEADERS` | The header strip stops being load-bearing — safe, but the reasoning behind it is no longer true. |
| Core's IP ban is off by default and keys on the socket peer | The decision to guard at the proxy | If Core could see real client addresses, the guard could move there. |

Add to this file rather than spreading a new assumption about Core through the
codebase with nothing watching it.

Beyond this file, an HA upgrade should also get a run of the full add-on suite
(`venv/bin/python -m pytest hacs-addon/tests/`) and a real smoke test on a
sandbox instance — see `docs/PLAN_HA_2026_8.md` for how the 2026.8 pass was
done, including the live-instance verification that caught the port change.
