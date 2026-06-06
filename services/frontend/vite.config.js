import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: '0.0.0.0',
    headers: {
      'Permissions-Policy': 'display-capture=(), camera=(), microphone=(), geolocation=(), payment=(), usb=(), picture-in-picture=()',
      'X-Frame-Options': 'DENY',
      'X-Content-Type-Options': 'nosniff',
      'Referrer-Policy': 'same-origin'
    }
  }
})
