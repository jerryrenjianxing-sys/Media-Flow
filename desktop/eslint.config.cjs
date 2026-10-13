'use strict';
// Development tooling is shared with the existing console workspace; it is
// excluded from the desktop runtime and requires console npm dependencies.
const javascript = require('../control_console/node_modules/@eslint/js');
const globals = require('../control_console/node_modules/globals');

module.exports = [
  { ignores: ['node_modules/**', 'out/**', 'dist/**'] },
  {
    ...javascript.configs.recommended,
    files: ['main.cjs', 'preload.cjs', 'eslint.config.cjs', 'lib/**/*.cjs', 'tests/**/*.cjs'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'commonjs', globals: globals.node },
    rules: { ...javascript.configs.recommended.rules, 'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }] }
  },
  {
    ...javascript.configs.recommended,
    files: ['splash.cjs'],
    languageOptions: { ecmaVersion: 'latest', sourceType: 'script', globals: globals.browser },
    rules: { ...javascript.configs.recommended.rules, 'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }] }
  }
];
