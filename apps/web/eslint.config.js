import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactRefresh from 'eslint-plugin-react-refresh';
export default tseslint.config({ ignores: ['dist'], extends: [js.configs.recommended, ...tseslint.configs.recommended], files: ['src/**/*.{ts,tsx}'], plugins: {'react-refresh': reactRefresh}, rules: {'react-refresh/only-export-components': 'off', '@typescript-eslint/no-unused-vars': ['error', {argsIgnorePattern: '^_'}]}});
