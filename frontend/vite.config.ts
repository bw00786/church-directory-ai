import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  // The backend serves the built bundle at /app (see backend/app/main.py), so
  // asset URLs must be rooted there. `npm run dev` still serves at /.
  base: process.env.VITE_BASE_PATH ?? '/app/',
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: true,
    port: 5173,
    // Same-origin development proxy (used when VITE_API_BASE_URL is set to "").
    // Canonical API paths are /api/v1/...; the backend also serves its legacy
    // prefixes, so nothing is rewritten here.
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/health': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
