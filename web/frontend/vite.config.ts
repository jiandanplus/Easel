import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { portableAssets } from './portable-assets.js'

// Development only: `EASEL_DEV_BACKEND=http://127.0.0.1:17868 npm run dev` shows the latest
// frontend code against an Easel backend that is already running, without packaging the app.
// The backend only accepts writes from its own origin, so the proxy presents that origin.
const backend = process.env.EASEL_DEV_BACKEND

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), portableAssets()],
  base: './',  // 相对路径，适配 proxy
  server: backend ? {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: backend,
        configure: proxy => proxy.on('proxyReq', request => {
          if (request.getHeader('origin')) request.setHeader('origin', backend)
        }),
      },
    },
  } : undefined,
})
