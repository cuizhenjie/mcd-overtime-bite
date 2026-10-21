import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // 用相对路径产出资源引用，这样同一份 dist 既能挂在域名根目录，
  // 也能挂在 GitHub Pages 的 /<repo>/ 子路径下，不需要重新构建。
  base: './',
})
