import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  console.log('Vite Mode:', mode)
  console.log('Loaded VITE_API_BASE_URL:', env.VITE_API_BASE_URL)
  
  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 3000,
      proxy: {
        '/api/v1/files/upload-temp': {
          target: 'http://127.0.0.1:3001',
          changeOrigin: true,
        },
        '/api': {
          target: env.VITE_API_BASE_URL 
            ? env.VITE_API_BASE_URL.replace(/\/api\/?$/, '') 
            : 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
        // Proxy for AIOrchestraService ingestion API
        '/ai-orchestra': {
          target: env.VITE_AI_ORCHESTRA_URL 
            ? env.VITE_AI_ORCHESTRA_URL.replace(/\/api\/v1\/?$/, '') 
            : 'http://127.0.0.1:8888',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/ai-orchestra/, '/api/v1'),
        },
      },
    },
  }
})
