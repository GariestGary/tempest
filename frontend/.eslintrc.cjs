module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
    node: true,
  },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  plugins: ['@typescript-eslint', 'react-hooks', 'react-refresh'],
  extends: [
    'eslint:recommended',
    'plugin:@typescript-eslint/recommended',
    'plugin:react-hooks/recommended',
  ],
  ignorePatterns: ['dist/', 'coverage/', 'node_modules/'],
  rules: {
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': [
      'error',
      { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
    ],
    '@typescript-eslint/no-explicit-any': 'off',
    // Preserve the upstream React UI patterns in this preparation phase.
    // The v7 compiler-oriented checks below require broad state/ref rewrites;
    // keep the core Rules of Hooks and dependency checks enabled instead.
    'react-hooks/refs': 'off',
    'react-hooks/set-state-in-effect': 'off',
    'react-hooks/purity': 'off',
    // Two existing shared component modules export utilities alongside JSX.
    'react-refresh/only-export-components': 'off',
  },
}
