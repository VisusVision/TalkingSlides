import { Monitor, MoonStar, Sun } from 'lucide-react';
import { useTheme } from './ThemeProvider';
import Button from './Button';

const NEXT_MODE = {
  system: 'dark',
  dark: 'light',
  light: 'system',
};

export default function ThemeToggle() {
  const { mode, setMode, resolvedTheme } = useTheme();

  const icon = mode === 'system'
    ? <Monitor size={16} />
    : resolvedTheme === 'dark'
      ? <MoonStar size={16} />
      : <Sun size={16} />;

  const label = mode === 'system' ? 'System' : resolvedTheme === 'dark' ? 'Dark' : 'Light';

  return (
    <Button
      variant="secondary"
      size="sm"
      aria-label={`Theme mode: ${label}. Click to switch mode.`}
      title={`Theme: ${label}`}
      onClick={() => setMode(NEXT_MODE[mode] || 'system')}
      className="min-w-[96px]"
    >
      {icon}
      <span>{label}</span>
    </Button>
  );
}
