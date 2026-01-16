import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Home-style color scheme - warm and cozy
        home: {
          // Primary colors - warm beige and orange
          primary: {
            50: '#fef9f3',
            100: '#fdf2e7',
            200: '#fae4c9',
            300: '#f6d0a5',
            400: '#f1b570',
            500: '#ec9a3a', // Primary color
            600: '#dd7f1f',
            700: '#b86318',
            800: '#944f1a',
            900: '#784218',
          },
          // Secondary colors - soft blue
          secondary: {
            50: '#f0f9ff',
            100: '#e0f2fe',
            200: '#bae6fd',
            300: '#7dd3fc',
            400: '#38bdf8',
            500: '#0ea5e9',
            600: '#0284c7',
            700: '#0369a1',
            800: '#075985',
            900: '#0c4a6e',
          },
          // Background colors - cream and beige
          background: {
            light: '#fefbf7',
            DEFAULT: '#faf7f2',
            dark: '#f5f1e8',
          },
          // Text colors - dark brown
          text: {
            light: '#8b7355',
            DEFAULT: '#5d4e37',
            dark: '#3d3324',
          },
          // Success colors - soft green
          success: {
            50: '#f0fdf4',
            100: '#dcfce7',
            200: '#bbf7d0',
            300: '#86efac',
            400: '#4ade80',
            500: '#22c55e',
            600: '#16a34a',
          },
          // Warning colors - warm yellow
          warning: {
            50: '#fffbeb',
            100: '#fef3c7',
            200: '#fde68a',
            300: '#fcd34d',
            400: '#fbbf24',
            500: '#f59e0b',
            600: '#d97706',
          },
          // Error colors - soft red
          error: {
            50: '#fef2f2',
            100: '#fee2e2',
            200: '#fecaca',
            300: '#fca5a5',
            400: '#f87171',
            500: '#ef4444',
            600: '#dc2626',
          },
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      borderRadius: {
        'home': '12px',
      },
      boxShadow: {
        'home': '0 2px 8px rgba(0, 0, 0, 0.08)',
        'home-lg': '0 4px 16px rgba(0, 0, 0, 0.12)',
      },
    },
  },
  plugins: [
    typography,
  ],
}
