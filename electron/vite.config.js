import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  root: 'src/renderer',
  // Relative, because the packaged app is loaded over file:// — absolute asset
  // paths would resolve against the filesystem root and 404 every bundle.
  base: './',
  plugins: [react(), tailwindcss()],
  build: {
    outDir: '../../dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
})
