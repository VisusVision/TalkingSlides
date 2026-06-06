import { createContext, useContext, useEffect, useMemo, useState } from 'react';
// Disabled for this pass: logo-based favicon switching.
// import logoBlack from '../../styles/images/black.svg';
// import logoWhite from '../../styles/images/white.svg';

const THEME_STORAGE_KEY = 'visus-theme-mode';

const ThemeContext = createContext(null);

function getSystemTheme() {
  if (typeof window === 'undefined') {
    return 'light';
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function getInitialMode() {
  if (typeof window === 'undefined') {
    return 'system';
  }
  const storedMode = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (storedMode === 'light' || storedMode === 'dark' || storedMode === 'system') {
    return storedMode;
  }
  return 'system';
}

function ensureFaviconLink(id, rel, type = '') {
  if (typeof document === 'undefined') return null;

  let link = document.getElementById(id);
  if (!(link instanceof HTMLLinkElement)) {
    link = document.createElement('link');
    link.id = id;
    document.head.appendChild(link);
  }

  link.rel = rel;
  if (type) {
    link.type = type;
  }
  return link;
}

function textFavicon(theme) {
  const isDark = theme === 'dark';
  const bg = isDark ? '#0b1220' : '#f2f6ff';
  const fg = isDark ? '#f7fbff' : '#0f172a';
  const border = isDark ? '#334155' : '#cbd5e1';
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect x="2" y="2" width="60" height="60" rx="12" fill="${bg}" stroke="${border}" stroke-width="2"/><text x="32" y="40" text-anchor="middle" font-size="22" font-family="Arial, sans-serif" font-weight="700" fill="${fg}">VV</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function updateThemeFavicons(resolvedTheme) {
  if (typeof window === 'undefined') return;

  const svgHref = textFavicon(resolvedTheme);

  const svgLink = ensureFaviconLink('visus-favicon-svg', 'icon', 'image/svg+xml');
  if (svgLink) {
    svgLink.href = svgHref;
  }

  const appleTouch = ensureFaviconLink('visus-favicon-apple', 'apple-touch-icon');
  if (appleTouch) {
    appleTouch.href = svgHref;
  }

  const pngLink = ensureFaviconLink('visus-favicon-png', 'alternate icon', 'image/png');
  if (!pngLink) return;

  const image = new Image();
  image.onload = () => {
    const canvas = document.createElement('canvas');
    canvas.width = 48;
    canvas.height = 48;
    const context = canvas.getContext('2d');
    if (!context) {
      pngLink.href = svgHref;
      return;
    }

    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    pngLink.href = canvas.toDataURL('image/png');
  };
  image.onerror = () => {
    pngLink.href = svgHref;
  };
  image.src = svgHref;
}

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(getInitialMode);
  const [systemTheme, setSystemTheme] = useState(getSystemTheme);

  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const handleChange = () => {
      setSystemTheme(mediaQuery.matches ? 'dark' : 'light');
    };

    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, []);

  const resolvedTheme = mode === 'system' ? systemTheme : mode;

  useEffect(() => {
    document.documentElement.classList.toggle('dark', resolvedTheme === 'dark');
    document.documentElement.setAttribute('data-theme', resolvedTheme);
    updateThemeFavicons(resolvedTheme);

    if (mode === 'system') {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
      return;
    }

    window.localStorage.setItem(THEME_STORAGE_KEY, mode);
  }, [mode, resolvedTheme]);

  const value = useMemo(
    () => ({
      mode,
      setMode,
      resolvedTheme,
    }),
    [mode, resolvedTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error('useTheme must be used inside ThemeProvider.');
  }
  return context;
}
