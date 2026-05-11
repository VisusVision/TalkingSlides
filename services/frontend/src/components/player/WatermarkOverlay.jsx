import { useEffect, useMemo, useState } from 'react';

const WATERMARK_POSITIONS = [
  'left-[7%] top-[12%]',
  'right-[9%] top-[18%]',
  'left-[15%] bottom-[18%]',
  'right-[12%] bottom-[15%]',
  'left-1/2 top-[38%] -translate-x-1/2',
  'left-[30%] bottom-[32%]',
];

function envEnabled(value) {
  return String(value ?? 'true').trim().toLowerCase() !== 'false';
}

function truthy(value) {
  return value === true || value === 'true' || value === 1 || value === '1';
}

function textSeed(value) {
  return String(value || '')
    .split('')
    .reduce((sum, char) => sum + char.charCodeAt(0), 0);
}

export default function WatermarkOverlay({ lesson, watermark }) {
  const payload = watermark || lesson?.watermark || {};
  const enabled = truthy(payload?.enabled);
  const text = String(payload?.text || '').trim();
  const uiEnabled = envEnabled(import.meta.env.VITE_PLAYER_WATERMARK_ENABLED);
  const [tick, setTick] = useState(0);

  const basePosition = useMemo(
    () => (Number(lesson?.id || 0) + textSeed(text)) % WATERMARK_POSITIONS.length,
    [lesson?.id, text],
  );

  useEffect(() => {
    if (!enabled || !text || !uiEnabled) return undefined;
    const intervalId = window.setInterval(() => {
      setTick((value) => value + 1);
    }, 12000);
    return () => window.clearInterval(intervalId);
  }, [enabled, text, uiEnabled]);

  if (!enabled || !text || !uiEnabled) return null;

  const positionClass = WATERMARK_POSITIONS[(basePosition + tick) % WATERMARK_POSITIONS.length];

  return (
    <div className="pointer-events-none absolute inset-0 z-20 overflow-hidden" aria-hidden="true">
      <div
        className={[
          'pointer-events-none absolute max-w-[78%] select-none rounded-md border border-white/20',
          'bg-black/20 px-3 py-1.5 text-[11px] font-semibold leading-tight text-white/80 shadow-lg',
          'backdrop-blur-[1px] sm:text-xs',
          positionClass,
        ].join(' ')}
        style={{ textShadow: '0 1px 2px rgba(0,0,0,0.65)' }}
      >
        {text}
      </div>
    </div>
  );
}
