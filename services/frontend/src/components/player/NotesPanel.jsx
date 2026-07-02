import { CheckCircle2, ChevronDown, NotebookPen } from 'lucide-react';
import Button from '../ui/Button';
import SurfaceCard from '../ui/SurfaceCard';
import { useI18n } from '../../i18n/I18nProvider';

export default function NotesPanel({
  notes,
  onNotesChange,
  onSave,
  savedAtLabel,
  unsaved = false,
  saveActionLabel = 'Save Note',
  saveHint = '',
  collapsed = false,
  onToggle,
}) {
  const { t } = useI18n();

  return (
    <SurfaceCard className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="label-sm">{t('watch.studyNotes')}</p>
          <h2 className="title-lg mt-1 text-[var(--text-primary)]">{t('watch.personalNotebook')}</h2>
        </div>
        <div className="flex items-center gap-2">
          <NotebookPen size={17} className="text-[var(--text-secondary)]" />
          {typeof onToggle === 'function' && (
            <button
              type="button"
              onClick={onToggle}
              className="focus-ring inline-flex h-8 w-8 items-center justify-center rounded-full token-surface text-[var(--text-secondary)]"
              aria-label={collapsed ? t('watch.expandStudyNotes') : t('watch.collapseStudyNotes')}
            >
              <ChevronDown
                size={15}
                className={`transition ${collapsed ? '-rotate-90' : 'rotate-0'}`}
              />
            </button>
          )}
        </div>
      </div>

      {!collapsed && (
        <>
          <div className="token-glass rounded-2xl p-3">
            <textarea
              value={notes}
              onChange={(event) => onNotesChange(event.target.value)}
              placeholder={t('watch.notesPlaceholder')}
              className="focus-ring min-h-[220px] w-full resize-y rounded-xl border border-[var(--border-subtle)] bg-[color:var(--surface-elevated)] p-3 text-sm leading-relaxed text-[var(--text-primary)]"
            />
          </div>

          <div className="flex items-center justify-between gap-2">
            <p className="inline-flex items-center gap-1 text-xs text-[var(--text-secondary)]">
              <CheckCircle2 size={13} />
              {savedAtLabel || t('watch.autoSavedLocally')}
              {unsaved ? ` - ${t('watch.unsavedChanges')}` : ''}
            </p>
            <Button size="sm" onClick={onSave}>
              {saveActionLabel}
            </Button>
          </div>

          {saveHint && (
            <p className="rounded-xl bg-[color:color-mix(in_srgb,var(--surface-muted),transparent_6%)] px-3 py-2 text-xs text-[var(--text-secondary)]">
              {saveHint}
            </p>
          )}
        </>
      )}

      {collapsed && (
        <p className="text-xs text-[var(--text-secondary)]">
          {t('watch.notesCollapsed')}
        </p>
      )}
    </SurfaceCard>
  );
}
