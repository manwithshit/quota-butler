import { defineConfig } from 'tsup';

export default defineConfig({
  entry: { cli: 'src/cli.ts' },
  format: ['esm'],
  target: 'node20',
  outDir: 'dist',
  outExtension: () => ({ js: '.mjs' }),
  clean: true,
  sourcemap: true,
  banner: { js: '#!/usr/bin/env node' },
});
