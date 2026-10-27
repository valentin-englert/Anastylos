import { readFileSync } from 'node:fs';
import { defineConfig } from 'vitest/config';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8'));

export default defineConfig({
  base: './',
  build: {
    outDir: 'dist',
    sourcemap: false,
    target: 'es2022',
  },
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    port: 5173,
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
});
