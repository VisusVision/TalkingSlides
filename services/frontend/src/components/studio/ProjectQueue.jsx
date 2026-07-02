import { Clock3, Eye, RefreshCcw, Trash2 } from 'lucide-react';
import Button from '../ui/Button';
import SurfaceCard from '../ui/SurfaceCard';
import { useI18n } from '../../i18n/I18nProvider';

function statusTone(status) {
  const value = String(status || '').toLowerCase();
  if (value === 'done' || value === 'ready') {
    return 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]';
  }
  if (value.includes('fail') || value.includes('error')) {
    return 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]';
  }
  if (value === 'running' || value === 'processing') {
    return 'bg-[color:var(--status-info-bg)] text-[color:var(--status-info-fg)]';
  }
  return 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]';
}

function labelFromStatus(status, t) {
  const value = String(status || '').toLowerCase();
  if (!value) return t('studio.status.draft');
  if (value === 'done' || value === 'ready') return t('studio.status.ready');
  if (value === 'running' || value === 'processing') return t('studio.status.processing');
  if (value.includes('fail') || value.includes('error')) return t('studio.status.failed');
  if (value === 'pending') return t('studio.status.queued');
  return value;
}

export default function ProjectQueue({
  projects,
  loading,
  onOpen,
  onRerender,
  onDelete,
}) {
  const { t, formatDate } = useI18n();

  return (
    <SurfaceCard className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="label-sm">{t('studio.queue')}</p>
          <h2 className="headline-md mt-1 text-[var(--text-primary)]">{t('studio.publishedLessons')}</h2>
        </div>
      </div>

      {loading ? (
        <p className="text-sm text-[var(--text-secondary)]">{t('studio.loadingProjects')}</p>
      ) : projects.length === 0 ? (
        <div className="rounded-2xl token-glass px-4 py-6 text-center">
          <p className="title-lg text-[var(--text-primary)]">{t('studio.noLessonsYet')}</p>
          <p className="body-md mt-1">{t('studio.noLessonsYetBody')}</p>
        </div>
      ) : (
        <div className="space-y-3">
          {projects.map((project) => (
            <article key={project.id} className="rounded-2xl token-surface-elevated p-4 shadow-soft">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="title-lg text-[var(--text-primary)]">{project.title || `Project #${project.id}`}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--text-secondary)]">
                    <span className="inline-flex items-center gap-1">
                      <Clock3 size={12} />
                      {formatDate(project.created_at || Date.now())}
                    </span>
                    <span className="rounded-full bg-[color:var(--surface-muted)] px-2 py-1">
                      {project.category_name || t('common.uncategorized')}
                    </span>
                  </div>
                </div>

                <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${statusTone(project.latest_job?.status || project.status)}`}>
                  {labelFromStatus(project.latest_job?.status || project.status, t)}
                </span>
              </div>

              <div className="mt-4 flex flex-wrap gap-2">
                <Button size="sm" onClick={() => onOpen(project)}>
                  <Eye size={14} />
                  <span>{t('common.open')}</span>
                </Button>
                <Button variant="secondary" size="sm" onClick={() => onRerender(project)}>
                  <RefreshCcw size={14} />
                  <span>{t('common.rerender')}</span>
                </Button>
                <Button variant="ghost" size="sm" onClick={() => onDelete(project)}>
                  <Trash2 size={14} />
                  <span>{t('common.delete')}</span>
                </Button>
              </div>
            </article>
          ))}
        </div>
      )}
    </SurfaceCard>
  );
}
