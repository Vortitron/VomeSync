export default [
	{
		ignores: ["node_modules/**", "dist/**"],
	},
	{
		files: ["**/*.js", "**/*.mjs", "**/*.cjs"],
		languageOptions: {
			ecmaVersion: "latest",
			sourceType: "module",
			globals: {
				console: "readonly",
				process: "readonly",
				__dirname: "readonly",
				module: "readonly",
				require: "readonly",
				jest: "readonly",
				describe: "readonly",
				it: "readonly",
				expect: "readonly",
				beforeAll: "readonly",
				afterAll: "readonly",
				beforeEach: "readonly",
				afterEach: "readonly",
			},
		},
		rules: {
			"consistent-return": "off",
			"require-await": "off",
			"no-unused-vars": "off",
			"no-trailing-spaces": "off",
			"no-mixed-spaces-and-tabs": "off",
			"indent": "off",
			"prefer-const": "off",
		},
	},
];

