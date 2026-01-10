/**
 * Unit tests for log redaction
 */

const redact = require('../../../src/utils/redact');

describe('redact', () => {
	test('redacts sensitive fields in nested objects', () => {
		const input = {
			apiKey: '00000000-0000-4000-8000-000000000000',
			personalKey: '11111111-1111-4111-8111-111111111111',
			apiKeyId: 'a'.repeat(64),
			headers: {
				'X-Api-Key': '22222222-2222-4222-8222-222222222222',
				'x-personal-key': '33333333-3333-4333-8333-333333333333'
			}
		};

		const out = redact(input);

		expect(out.apiKey).toBe('[REDACTED]');
		expect(out.personalKey).toBe('[REDACTED]');
		expect(out.apiKeyId).toBe('a'.repeat(64));
		expect(out.headers['X-Api-Key']).toBe('[REDACTED]');
		expect(out.headers['x-personal-key']).toBe('[REDACTED]');
	});

	test('redacts accessKey URL fragments and header-like strings', () => {
		const input = {
			message: 'Open https://example.com/switch/vs_test#accessKey=secret123 and set x-api-key: secret123'
		};
		const out = redact(input);
		expect(String(out.message)).toContain('#accessKey=[REDACTED]');
		expect(String(out.message)).toContain('x-api-key: [REDACTED]');
	});
});


