/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      boxShadow: {
        soft: '0 16px 42px rgba(18, 20, 30, 0.10)',
        lift: '0 26px 52px rgba(18, 20, 30, 0.18)',
      },
      borderRadius: {
        xxl: '1.5rem',
      },
    },
  },
  plugins: [],
};
