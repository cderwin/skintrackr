import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Server routes; in `npm run dev` they go to a running skintrackr server (`just py-serve`).
const serverRoutes = ['/auth', '/token', '/api', '/healthcheck']

export default defineConfig({
  plugins: [vue()],
  // VITE_* variables (e.g. VITE_MAPTILER_KEY) come from the repository's .env
  envDir: '..',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(serverRoutes.map((route) => [route, 'http://localhost:8080'])),
  },
})
