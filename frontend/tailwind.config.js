/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      fontFamily: {
        serif: ['"Noto Serif SC"', '"Source Han Serif SC"', 'Georgia', 'serif'],
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      colors: {
        // Beidou Chinese-style palette
        primary: {
          DEFAULT: '#1a1a2e',
          light: '#16213e',
          dark: '#0f0f23',
        },
        gold: {
          DEFAULT: '#d4a574',
          light: '#e8c89e',
          dark: '#b8864e',
        },
        star: {
          DEFAULT: '#e8e8e8',
          dim: '#a0a0a0',
        },
        bg: {
          DEFAULT: '#0a0a1a',
          card: 'rgba(26, 26, 46, 0.8)',
        },
        accent: {
          purple: '#7c3aed',
          cyan: '#06b6d4',
          emerald: '#10b981',
        },
      },
      backgroundImage: {
        'gold-gradient': 'linear-gradient(135deg, #d4a574 0%, #b8864e 100%)',
        'star-gradient': 'radial-gradient(ellipse at bottom, #1B2735 0%, #090A0F 100%)',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}
