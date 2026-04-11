import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/events': { target: 'http://localhost:8080', changeOrigin: true },
      '/mission': { target: 'http://localhost:8080', changeOrigin: true },
      '/hitl': { target: 'http://localhost:8080', changeOrigin: true },
      '/command': { target: 'http://localhost:8080', changeOrigin: true },
      '/plan': { target: 'http://localhost:8080', changeOrigin: true },
      '/health': { target: 'http://localhost:8080', changeOrigin: true },
      '/labs': { target: 'http://localhost:8080', changeOrigin: true },
    },
  },
  worker: {
    format: 'es',
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
})
