// Composed directly rather than through `eslint-config-next`, which still
// depends on `eslint-plugin-react` — untouched since April 2025, peer-capped
// at ESLint 9, and calling `context.getFilename()`, which ESLint 10 removed.
// ESLint 9 reached end of life on 2026-08-06 with no fix in sight upstream,
// so the rule sets `eslint-config-next` assembled are assembled here instead,
// minus that one plugin. Everything else it configured is reproduced below,
// rule for rule; the file reads best next to its `dist/index.js`.
//
// What `eslint-plugin-react` contributed and is now gone: its `recommended`
// set, of which `eslint-config-next` already switched off `react-in-jsx-scope`,
// `prop-types`, `no-unknown-property` and `jsx-no-target-blank`. The rest is
// JSX hygiene that TypeScript's `strict` mode largely subsumes (undefined
// components, duplicate props, unknown DOM props). Revisit when the plugin
// ships an ESLint-10 release.
import nextPlugin from "@next/eslint-plugin-next";
import { defineConfig, globalIgnores } from "eslint/config";
import importX from "eslint-plugin-import-x";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts"]),

  {
    name: "labdog/next",
    files: ["**/*.{js,jsx,mjs,ts,tsx,mts,cts}"],
    plugins: {
      "react-hooks": reactHooks,
      "import-x": importX,
      "jsx-a11y": jsxA11y,
      "@next/next": nextPlugin,
    },
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // `react-hooks/set-state-in-effect` and `react-hooks/purity` are errors
      // (from the recommended set): derive state during render, or reset it
      // with a `key`, instead of setting it synchronously in an effect.
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs["core-web-vitals"].rules,
      // `eslint-plugin-import-x` is the maintained fork of
      // `eslint-plugin-import`, whose peer range also stops at ESLint 9.
      "import-x/no-anonymous-default-export": "warn",
      "jsx-a11y/alt-text": ["warn", { elements: ["img"], img: ["Image"] }],
      "jsx-a11y/aria-props": "warn",
      "jsx-a11y/aria-proptypes": "warn",
      "jsx-a11y/aria-unsupported-elements": "warn",
      "jsx-a11y/role-has-required-aria-props": "warn",
      "jsx-a11y/role-supports-aria-props": "warn",
    },
  },

  // `eslint-config-next/typescript`, verbatim: the recommended set plus two
  // rules demoted to warnings.
  ...tseslint.configs.recommended,
  {
    name: "labdog/typescript",
    files: ["**/*.ts", "**/*.tsx"],
    rules: {
      "@typescript-eslint/no-unused-vars": "warn",
      "@typescript-eslint/no-unused-expressions": "warn",
    },
  },
]);
