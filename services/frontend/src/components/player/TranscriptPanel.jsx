import SurfaceCard from '../ui/SurfaceCard';
import { ChevronDown } from 'lucide-react';
import { formatTimestamp } from '../../lib/watch';

export default function TranscriptPanel({
  lines,
  playbackTime,
  onJump,
  collapsed = false,
  onToggle,
}) {
  return (
    <SurfaceCard className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="label-sm">Transcript</p>
          <h2 className="title-lg mt-1 text-[var(--text-primary)]">Readable Timeline</h2>
        </div>
        {typeof onToggle === 'function' && (
          <button
            type="button"
            onClick={onToggle}
            className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full token-surface text-[var(--text-secondary)]"
            aria-label={collapsed ? 'Expand transcript' : 'Collapse transcript'}
          >
            <ChevronDown
              size={15}
              className={`transition ${collapsed ? '-rotate-90' : 'rotate-0'}`}
            />
          </button>
        )}
      </div>

      {!collapsed && (
        <div className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
        {lines.map((line) => {
          const active = playbackTime >= line.startSeconds && playbackTime < line.endSeconds;

          return (
            <button
              key={line.id}
              type="button"
              onClick={() => onJump(line.startSeconds)}
              className={`focus-ring w-full rounded-2xl border px-3 py-2 text-left transition ${
                active
                  ? 'border-[color:color-mix(in_srgb,var(--accent-secondary),transparent_34%)] bg-[color:color-mix(in_srgb,var(--accent-secondary),transparent_84%)]'
                  : 'border-transparent bg-[color:var(--surface-muted)] hover:border-[var(--border-subtle)]'
              }`}
            >
              <p className="text-xs text-[var(--text-secondary)]">{formatTimestamp(line.startSeconds)}</p>
              <p className="mt-1 text-sm text-[var(--text-primary)]">{line.text}</p>
            </button>
          );
        })}
        </div>
      )}

      {collapsed && (
        <p className="text-xs text-[var(--text-secondary)]">Collapsed</p>
      )}
    </SurfaceCard>
  );
}
