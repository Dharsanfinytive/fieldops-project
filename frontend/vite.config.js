import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_BASE_URL || 'http://localhost:8000'

  return {
    plugins: [
      react(),
      tailwindcss(),
    ],
    server: {
      proxy: {
        '/__api': {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/__api/, ''),
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      fileParallelism: false,
      maxWorkers: 1,
      threads: {
        singleThread: true,
      },
      forks: {
        singleFork: true,
      }
    }
  }
})
