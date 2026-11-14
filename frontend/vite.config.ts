import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Server routes; in `npm run dev` they go to a running skintrackr server (`just py-serve`).
const serverRoutes = ['/oauth2', '/token', '/api', '/subscriptions', '/healthcheck']

export default defineConfig({
  plugins: [vue()],
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
