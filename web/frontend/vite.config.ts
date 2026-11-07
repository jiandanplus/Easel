import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { portableAssets } from './portable-assets.js'


// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), portableAssets()],
  base: './',  // 相对路径，适配 proxy
})
