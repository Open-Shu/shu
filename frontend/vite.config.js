import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Load env file based on `mode` in the current working directory.
  const env = loadEnv(mode, process.cwd(), '');

  // Proxy target for Vite dev server (server-side only, NOT exposed to browser)
  // - Shell env DEV_SERVER_API_PROXY_TARGET (from docker-compose) takes precedence
  // - Falls back to VITE_API_BASE_URL from .env files (for local dev)
  // - Final fallback: localhost:8000
  const proxyTarget = process.env.DEV_SERVER_API_PROXY_TARGET || env.VITE_API_BASE_URL || 'http://localhost:8000';

  return {
    plugins: [
      react({
        // Enable JSX in .js files (not just .jsx)
        include: '**/*.{jsx,js}',
      }),
    ],
    esbuild: {
      loader: 'jsx',
      include: /src\/.*\.jsx?$/,
      exclude: [],
    },
    optimizeDeps: {
      esbuildOptions: {
        loader: {
          '.js': 'jsx',
        },
      },
    },
    server: {
      port: 3000,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
        },
        '/auth': {
          target: proxyTarget,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: mode !== 'production',
    },
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./vitest-setup.js', './src/setupTests.js'],
      testTimeout: 15000,
      hookTimeout: 15000,
      css: true,
      exclude: [
        '**/node_modules/**',
        '**/dist/**',
        '**/tests/**', // Exclude Playwright E2E tests
      ],
      coverage: {
        provider: 'v8',
        reporter: ['text', 'text-summary', 'html', 'lcov'],
        exclude: ['src/index.js', 'src/**/*.test.{js,jsx}', 'src/**/__tests__/**'],
      },
    },
  };
});
