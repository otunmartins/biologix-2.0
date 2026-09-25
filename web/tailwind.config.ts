import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--font-sans)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      colors: {
        // One hue per verdict, used for pills, counts and the pipeline icons.
        precedented: { DEFAULT: '#1f7a4d', soft: '#e7f4ec', line: '#b7dcc6' },
        supported: { DEFAULT: '#1d6a8a', soft: '#e6f2f7', line: '#b3d5e3' },
        gap: { DEFAULT: '#9a5b00', soft: '#fdf3e1', line: '#f0d29a' },
        alert: { DEFAULT: '#b42318', soft: '#fdecea', line: '#f3bcb6' },
      },
      boxShadow: {
        card: '0 1px 2px rgba(16, 24, 40, 0.04), 0 1px 3px rgba(16, 24, 40, 0.06)',
      },
      keyframes: {
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: {
        shimmer: 'shimmer 1.4s infinite',
      },
    },
  },
  plugins: [],
};

export default config;
