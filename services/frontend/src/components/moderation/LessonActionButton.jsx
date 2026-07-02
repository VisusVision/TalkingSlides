import { useEffect, useId, useMemo, useState } from 'react';
import { CheckCircle2, ExternalLink, Flag, Info, ShieldAlert, XCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import {
  adminApproveLesson,
  adminBlockLesson,
  adminRequestLessonChanges,
  reportLesson,
} from '../../api';
import { isStaffOrAdmin } from '../../lib/auth';
import { safeInternalReturnTo } from '../../utils/routeSession';
import Button from '../ui/Button';
import ModalShell from '../ui/ModalShell';
import { useI18n } from '../../i18n/I18nProvider';

const REPORT_CATEGORIES = [
  { value: 'inappropriate_content', labelKey: 'moderation.categories.inappropriateContent' },
  { value: 'wrong_information', labelKey: 'moderation.categories.wrongInformation' },
  { value: 'copyright', labelKey: 'moderation.categories.copyright' },
  { value: 'technical_problem', labelKey: 'moderation.categories.technicalProblem' },
  { value: 'other', labelKey: 'moderation.categories.other' },
];

function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

function lessonIdFrom(lesson) {
  return lesson?.id || lesson?.project_id || lesson?.projectId || '';
}

function lessonReviewUrl(lesson, projectId) {
  const params = new URLSearchParams();
  params.set('mode', 'review');
  params.set('view', 'editor');
  params.set('lesson', String(projectId));
  params.set('source', 'lesson-actions');
  params.set('sourcePage', typeof window !== 'undefined' ? window.location.pathname || '/' : '/');
  const reviewId = lesson?.admin_review_request_id || lesson?.review_id || lesson?.reviewRequestId;
  const reportId = lesson?.moderation_report_id || lesson?.report_id || lesson?.reportId;
  if (reviewId) params.set('review', String(reviewId));
  if (reportId) params.set('report', String(reportId));
  if (typeof window !== 'undefined') {
    const returnTo = `${window.location.pathname || '/'}${window.location.search || ''}`;
    params.set('returnTo', safeInternalReturnTo(returnTo, '/moderation'));
  }
  return `/studio?${params.toString()}`;
}

export default function LessonActionButton({
  lesson,
  user,
  onLoginRequest,
  onCompleted,
  compact = false,
  className = '',
}) {
  const { t } = useI18n();
  const titleId = useId();
  const projectId = lessonIdFrom(lesson);
  const staff = isStaffOrAdmin(user);
  const lessonTitle = String(lesson?.title || `Lesson #${projectId || ''}`).trim();
  const reviewUrl = useMemo(() => lessonReviewUrl(lesson, projectId), [lesson, projectId]);
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState(REPORT_CATEGORIES[0].value);
  const [message, setMessage] = useState('');
  const [adminReason, setAdminReason] = useState('');
  const [unpublishOnChange, setUnpublishOnChange] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const selectedCategoryLabel = useMemo(
    () => t(REPORT_CATEGORIES.find((item) => item.value === category)?.labelKey || 'moderation.issue'),
    [category, t],
  );

  useEffect(() => {
    if (!open) return;
    setCategory(REPORT_CATEGORIES[0].value);
    setMessage('');
    setAdminReason('');
    setUnpublishOnChange(false);
    setBusyAction('');
    setError('');
    setNotice('');
  }, [open, projectId, staff]);

  if (!projectId) return null;

  const handleOpen = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (!staff && !user) {
      const currentPath = `${window.location.pathname}${window.location.search}`;
      if (typeof onLoginRequest === 'function') onLoginRequest(currentPath);
      return;
    }
    setOpen(true);
  };

  const closeModal = () => {
    if (!busyAction) setOpen(false);
  };

  const submitReport = async (event) => {
    event.preventDefault();
    if (busyAction) return;
    setBusyAction('report');
    setError('');
    setNotice('');
    try {
      const payload = await reportLesson(projectId, { category, message });
      setNotice(payload?.deduped ? t('moderation.reportAlreadyReceived') : t('moderation.reportReceived'));
      if (typeof onCompleted === 'function') onCompleted(payload);
    } catch (reportError) {
      setError(reportError.message || t('moderation.couldNotSubmitReport'));
    } finally {
      setBusyAction('');
    }
  };

  const runAdminAction = async (action) => {
    if (busyAction) return;
    setBusyAction(action);
    setError('');
    setNotice('');
    try {
      let payload;
      if (action === 'block') {
        payload = await adminBlockLesson(projectId, adminReason);
      } else if (action === 'approve') {
        payload = await adminApproveLesson(projectId, adminReason);
      } else {
        payload = await adminRequestLessonChanges(projectId, {
          reason: adminReason,
          unpublish: unpublishOnChange,
        });
      }
      setNotice(payload?.message || t('moderation.actionSaved'));
      if (typeof onCompleted === 'function') onCompleted(payload);
    } catch (adminError) {
      setError(adminError.message || t('moderation.couldNotUpdateState'));
    } finally {
      setBusyAction('');
    }
  };

  return (
    <>
      <button
        type="button"
        aria-label={staff ? t('moderation.openActions') : t('moderation.reportIssue')}
        title={staff ? t('moderation.actions') : t('moderation.reportIssue')}
        onClick={handleOpen}
        onMouseDown={(event) => event.stopPropagation()}
        className={joinClasses(
          'focus-ring inline-flex shrink-0 items-center justify-center rounded-full border border-[var(--border-subtle)] bg-[color:var(--surface-container)] text-[var(--text-secondary)] shadow-sm transition hover:bg-[color:var(--hover-surface-strong)] hover:text-[var(--text-primary)]',
          compact ? 'h-9 w-9' : 'h-10 w-10',
          className,
        )}
      >
        {staff ? <ShieldAlert size={compact ? 16 : 18} /> : <Info size={compact ? 16 : 18} />}
      </button>

      <ModalShell
        open={open}
        eyebrow={staff ? t('moderation.staffModeration') : selectedCategoryLabel}
        title={staff ? lessonTitle : t('moderation.reportLesson')}
        titleId={titleId}
        onClose={closeModal}
        closeDisabled={Boolean(busyAction)}
        maxWidthClass="max-w-lg"
      >
        {notice ? (
          <div className="mb-4 rounded-2xl bg-[color:var(--status-success-bg)] p-3 text-sm font-medium text-[color:var(--status-success-fg)]">
            {notice}
          </div>
        ) : null}
        {error ? (
          <div className="mb-4 rounded-2xl bg-[color:var(--feedback-danger-bg)] p-3 text-sm font-medium text-[color:var(--feedback-danger-fg)]">
            {error}
          </div>
        ) : null}

        {staff ? (
          <div className="space-y-4">
            <label className="block text-sm font-medium text-[var(--text-secondary)]">
              {t('moderation.publisherNote')}
              <textarea
                value={adminReason}
                onChange={(event) => setAdminReason(event.target.value)}
                maxLength={4000}
                placeholder={t('moderation.addModerationNote')}
                className="focus-ring mt-2 min-h-[112px] w-full resize-y rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]"
              />
            </label>

            <label className="flex items-start gap-3 rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container-high)] p-3 text-sm text-[var(--text-secondary)]">
              <input
                type="checkbox"
                checked={unpublishOnChange}
                onChange={(event) => setUnpublishOnChange(event.target.checked)}
                className="mt-1 h-4 w-4 rounded border-[var(--border-subtle)]"
              />
              <span>{t('moderation.unpublishWhenRequestingChanges')}</span>
            </label>

            <div className="grid gap-2 sm:grid-cols-3">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => runAdminAction('block')}
                disabled={Boolean(busyAction)}
                fullWidth
              >
                <XCircle size={14} />
                <span>{busyAction === 'block' ? t('moderation.blocking') : t('moderation.block')}</span>
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => runAdminAction('request_changes')}
                disabled={Boolean(busyAction)}
                fullWidth
              >
                <Flag size={14} />
                <span>{busyAction === 'request_changes' ? t('moderation.saving') : t('moderation.requestChanges')}</span>
              </Button>
              <Button
                size="sm"
                onClick={() => runAdminAction('approve')}
                disabled={Boolean(busyAction)}
                fullWidth
              >
                <CheckCircle2 size={14} />
                <span>{busyAction === 'approve' ? t('moderation.approving') : t('moderation.approve')}</span>
              </Button>
            </div>

            <Link
              to={reviewUrl}
              className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-3 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)]"
              onClick={closeModal}
            >
              <ExternalLink size={14} />
              <span>{t('moderation.openDetail')}</span>
            </Link>
          </div>
        ) : (
          <form className="space-y-4" onSubmit={submitReport}>
            <label className="block text-sm font-medium text-[var(--text-secondary)]">
              {t('moderation.category')}
              <select
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                className="focus-ring mt-2 h-11 w-full rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
              >
                {REPORT_CATEGORIES.map((item) => (
                  <option key={item.value} value={item.value}>{t(item.labelKey)}</option>
                ))}
              </select>
            </label>

            <label className="block text-sm font-medium text-[var(--text-secondary)]">
              {t('moderation.message')}
              <textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                maxLength={2000}
                placeholder={t('moderation.addReportContext')}
                className="focus-ring mt-2 min-h-[112px] w-full resize-y rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]"
              />
            </label>

            <div className="flex flex-wrap justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={closeModal} disabled={Boolean(busyAction)}>
                {t('common.cancel')}
              </Button>
              <Button size="sm" type="submit" disabled={Boolean(busyAction)}>
                <Flag size={14} />
                <span>{busyAction === 'report' ? t('moderation.submitting') : t('moderation.submitReport')}</span>
              </Button>
            </div>
          </form>
        )}
      </ModalShell>
    </>
  );
}
