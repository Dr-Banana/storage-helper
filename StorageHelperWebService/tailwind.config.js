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
        // Legacy home colors — kept for backward compatibility
        home: {
          primary: {
            50: '#fef9f3',
            100: '#fdf2e7',
            200: '#fae4c9',
            300: '#f6d0a5',
            400: '#f1b570',
            500: '#ec9a3a',
            600: '#dd7f1f',
            700: '#b86318',
            800: '#944f1a',
            900: '#784218',
          },
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
          background: {
            light: '#FAF9F6',
            DEFAULT: '#F5F5F4',
            dark: '#E7E5E4',
          },
          text: {
            light: '#A8A29E',
            DEFAULT: '#57534E',
            dark: '#292524',
          },
          success: {
            50: '#f0fdf4',
            100: '#dcfce7',
            200: '#bbf7d0',
            300: '#86efac',
            400: '#4ade80',
            500: '#22c55e',
            600: '#16a34a',
          },
          warning: {
            50: '#fffbeb',
            100: '#fef3c7',
            200: '#fde68a',
            300: '#fcd34d',
            400: '#fbbf24',
            500: '#f59e0b',
            600: '#d97706',
          },
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
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'Helvetica', 'Arial', 'sans-serif'],
      },
      borderRadius: {
        'home': '12px',
      },
      boxShadow: {
        'home': '0 2px 8px rgba(0, 0, 0, 0.08)',
        'home-lg': '0 4px 16px rgba(0, 0, 0, 0.12)',
        'warm': '0 2px 8px rgba(41, 37, 36, 0.06)',
        'warm-lg': '0 8px 24px rgba(41, 37, 36, 0.10)',
      },
      backgroundImage: {
        'warm-paper': 'linear-gradient(135deg, #FAF9F6 0%, #F5F5F4 100%)',
      },
    },
  },
  plugins: [
    typography,
  ],
}
