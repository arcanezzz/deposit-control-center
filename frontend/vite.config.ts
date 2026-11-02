import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In Docker, nginx serves the build and proxies /api to the backend.
// For `npm run dev` outside Docker, Vite proxies /api to the API on localhost:8000.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://localhost:8000' } },
  build: { chunkSizeWarningLimit: 2000 },
})
