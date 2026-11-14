/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./public/index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      animation: {
        bounce: 'bounce 1.4s infinite'
      }
    }
  },
  plugins: []
};
