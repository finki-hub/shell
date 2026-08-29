import {
  base,
  browser,
  jsxA11y,
  perfectionist,
  prettier,
  react,
  typescript,
} from 'eslint-config-imperium';

const config = [
  { ignores: ['dist'] },
  ...base,
  { files: ['**/*.mts'], plugins: { jsdoc: base[0].plugins.jsdoc } },
  browser,
  react,
  jsxA11y,
  { ...typescript, files: ['**/*.{ts,tsx,mts}'] },
  prettier,
  perfectionist,
];

export default config;
