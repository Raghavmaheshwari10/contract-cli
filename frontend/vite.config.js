import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3001,
    proxy: {
      '/api': {
        target: 'https://contract-cli-six.vercel.app',
        changeOrigin: true,
      }
    }
  },
  build: {
    // Output to frontend/dist — do NOT touch public/ until React migration is complete.
    // When ready to ship React: change outDir to '../public' and emptyOutDir to true.
    outDir: 'dist',
    emptyOutDir: true,
  }
})
