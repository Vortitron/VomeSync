module.exports = {
	env: {
		browser: false,
		es2021: true,
		node: true,
		jest: true
	},
	extends: [
		'eslint:recommended'
	],
	parserOptions: {
		ecmaVersion: 'latest',
		sourceType: 'module'
	},
	rules: {
		// Enforce consistent indentation (tabs as per user preference)
		'indent': ['error', 'tab'],
		
		// Enforce consistent line endings
		'linebreak-style': ['error', 'unix'],
		
		// Enforce consistent quote style
		'quotes': ['error', 'single', { allowTemplateLiterals: true }],
		
		// Require semicolons
		'semi': ['error', 'always'],
		
		// Disallow unused variables
		'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
		
		// Disallow console.log in production
		'no-console': process.env.NODE_ENV === 'production' ? 'warn' : 'off',
		
		// Require consistent return statements
		'consistent-return': 'error',
		
		// Disallow multiple empty lines
		'no-multiple-empty-lines': ['error', { max: 2, maxEOF: 1 }],
		
		// Require space before blocks
		'space-before-blocks': 'error',
		
		// Require space before function parentheses
		'space-before-function-paren': ['error', {
			anonymous: 'always',
			named: 'never',
			asyncArrow: 'always'
		}],
		
		// Disallow trailing spaces
		'no-trailing-spaces': 'error',
		
		// Require comma-dangle for multiline
		'comma-dangle': ['error', 'never'],
		
		// Prefer const/let over var
		'no-var': 'error',
		'prefer-const': 'error',
		
		// Disallow duplicate keys in object literals
		'no-dupe-keys': 'error',
		
		// Disallow unreachable code
		'no-unreachable': 'error',
		
		// Require valid JSDoc comments
		'valid-jsdoc': 'off', // Disabled for now as it's deprecated
		
		// Async/await rules
		'require-await': 'error',
		'no-return-await': 'error'
	},
	globals: {
		// Jest globals
		'describe': 'readonly',
		'test': 'readonly',
		'expect': 'readonly',
		'beforeAll': 'readonly',
		'afterAll': 'readonly',
		'beforeEach': 'readonly',
		'afterEach': 'readonly',
		'jest': 'readonly',
		
		// Custom test globals
		'global': 'readonly'
	},
	overrides: [
		{
			files: ['**/*.test.js', '**/*.spec.js', '**/tests/**/*.js'],
			env: {
				jest: true
			},
			rules: {
				// Allow longer lines in tests for readability
				'max-len': 'off',
				
				// Allow console.log in tests
				'no-console': 'off'
			}
		}
	]
};
