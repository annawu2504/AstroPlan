import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import i18next from 'eslint-plugin-i18next'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist', 'src/types/astroplan.ts'] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
      i18next,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // Enforce all user-visible strings go through i18next
      'i18next/no-literal-string': [
        'error',
        {
          markupOnly: false,
          onlyAttribute: [],
          ignore: [
            '^[A-Z_][A-Z0-9_]*$',  // ALL_CAPS constants
            '^\\d+$',               // pure numbers
            '^[a-z]+:[a-z.]+$',    // i18n key patterns like "plan.status"
            '^#',                   // CSS colour values
            '^data-',               // data-testid
          ],
        },
      ],
    },
  },
)
