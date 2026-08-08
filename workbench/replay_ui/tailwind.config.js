/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // 麻将主题色（教育与图表配色 #10）
        board: {
          felt: '#2a9d8f',
          feltDark: '#264653',
          wood: '#8b4513',
          woodLight: '#a0522d',
        },
        tile: {
          white: '#f8f8f0',
          ivory: '#faf0e6',
          red: '#c0392b',
          green: '#27ae60',
          blue: '#2980b9',
          gold: '#d4af37',
        },
        player: {
          east: '#e74c3c',
          south: '#3498db',
          west: '#2ecc71',
          north: '#9b59b6',
        },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Microsoft YaHei', 'sans-serif'],
        mono: ['Menlo', 'Monaco', 'Courier New', 'monospace'],
      },
      borderRadius: {
        sm: '6px',
        DEFAULT: '10px',
        md: '10px',
        lg: '16px',
        xl: '20px',
      },
      boxShadow: {
        card: '0 1px 3px rgba(0,0,0,0.06)',
        'card-hover': '0 4px 12px rgba(0,0,0,0.1)',
        'card-glass': '0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.08)',
        'accent': '0 2px 8px var(--accent-shadow)',
        'accent-lg': '0 4px 16px var(--accent-shadow)',
      },
      spacing: {
        sidebar: '224px',
        'sidebar-collapsed': '72px',
        panel: '320px',
        'mobile-header': '56px',
      },
      zIndex: {
        base: '0',
        dropdown: '50',
        sticky: '100',
        overlay: '150',
        modal: '200',
        toast: '300',
        tooltip: '400',
      },
      transitionDuration: {
        fast: '0.15s',
        DEFAULT: '0.2s',
        slow: '0.3s',
      },
    },
  },
  plugins: [],
}
