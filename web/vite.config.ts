import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// M6-025 构建配置：Vue 3 + TS + Vite；测试用 Vitest（jsdom）。
// 同源约束（第 23 章 §23.7）：开发期走 Vite proxy 保持同源；生产由 FastAPI 托管 web/dist。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // 开发期同源：默认代理到根 README 的 `uvicorn ... --port 8000`；
      // 可用 VITE_PROXY_TARGET 覆盖（如后端跑在 8765）。
      '/api': {
        target: process.env.VITE_PROXY_TARGET ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
    restoreMocks: true,
  },
})
