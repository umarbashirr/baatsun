import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The dev server's HMR socket is allowed in index.html's CSP so `npm run dev`
// works. The built bundle has no dev server, and leaving it in would let the
// window talk to whatever local process happens to listen on that port.
const stripDevCsp = {
  name: 'baatsun-strip-dev-csp',
  apply: 'build',
  transformIndexHtml(html) {
    return html.replace(' ws://localhost:5173', '')
  },
}

export default defineConfig({
  root: 'src/renderer',
  // Relative, because the packaged app is loaded over file:// — absolute asset
  // paths would resolve against the filesystem root and 404 every bundle.
  base: './',
  plugins: [react(), tailwindcss(), stripDevCsp],
  build: {
    outDir: '../../dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
})
