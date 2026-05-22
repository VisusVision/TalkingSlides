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
import Button from '../ui/Button';
import ModalShell from '../ui/ModalShell';

const REPORT_CATEGORIES = [
  { value: 'inappropriate_content', label: 'Inappropriate content' },
  { value: 'wrong_information', label: 'Wrong information' },
  { value: 'copyright', label: 'Copyright / ownership concern' },
  { value: 'technical_problem', label: 'Technical problem' },
  { value: 'other', label: 'Other' },
];

function joinClasses(...parts) {
  return parts.filter(Boolean).join(' ');
}

function lessonIdFrom(lesson) {
  return lesson?.id || lesson?.project_id || lesson?.projectId || '';
}

export default function LessonActionButton({
  lesson,
  user,
  onLoginRequest,
  onCompleted,
  compact = false,
  className = '',
}) {
  const titleId = useId();
  const projectId = lessonIdFrom(lesson);
  const staff = isStaffOrAdmin(user);
  const lessonTitle = String(lesson?.title || `Lesson #${projectId || ''}`).trim();
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState(REPORT_CATEGORIES[0].value);
  const [message, setMessage] = useState('');
  const [adminReason, setAdminReason] = useState('');
  const [unpublishOnChange, setUnpublishOnChange] = useState(false);
  const [busyAction, setBusyAction] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const selectedCategoryLabel = useMemo(
    () => REPORT_CATEGORIES.find((item) => item.value === category)?.label || 'Issue',
    [category],
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
      setNotice(payload?.deduped ? 'Report already received.' : 'Report received.');
      if (typeof onCompleted === 'function') onCompleted(payload);
    } catch (reportError) {
      setError(reportError.message || 'Could not submit report.');
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
      setNotice(payload?.message || 'Moderation action saved.');
      if (typeof onCompleted === 'function') onCompleted(payload);
    } catch (adminError) {
      setError(adminError.message || 'Could not update moderation state.');
    } finally {
      setBusyAction('');
    }
  };

  return (
    <>
      <button
        type="button"
        aria-label={staff ? 'Open moderation actions' : 'Report lesson issue'}
        title={staff ? 'Moderation actions' : 'Report lesson issue'}
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
        eyebrow={staff ? 'Staff moderation' : selectedCategoryLabel}
        title={staff ? lessonTitle : 'Report lesson'}
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
              Publisher note
              <textarea
                value={adminReason}
                onChange={(event) => setAdminReason(event.target.value)}
                maxLength={4000}
                placeholder="Add a concise moderation note..."
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
              <span>Unpublish when requesting changes</span>
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
                <span>{busyAction === 'block' ? 'Blocking...' : 'Block'}</span>
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => runAdminAction('request_changes')}
                disabled={Boolean(busyAction)}
                fullWidth
              >
                <Flag size={14} />
                <span>{busyAction === 'request_changes' ? 'Saving...' : 'Request changes'}</span>
              </Button>
              <Button
                size="sm"
                onClick={() => runAdminAction('approve')}
                disabled={Boolean(busyAction)}
                fullWidth
              >
                <CheckCircle2 size={14} />
                <span>{busyAction === 'approve' ? 'Approving...' : 'Approve'}</span>
              </Button>
            </div>

            <Link
              to="/moderation"
              className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-3 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)]"
              onClick={closeModal}
            >
              <ExternalLink size={14} />
              <span>Open moderation detail</span>
            </Link>
          </div>
        ) : (
          <form className="space-y-4" onSubmit={submitReport}>
            <label className="block text-sm font-medium text-[var(--text-secondary)]">
              Category
              <select
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                className="focus-ring mt-2 h-11 w-full rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-3 text-sm text-[var(--text-primary)]"
              >
                {REPORT_CATEGORIES.map((item) => (
                  <option key={item.value} value={item.value}>{item.label}</option>
                ))}
              </select>
            </label>

            <label className="block text-sm font-medium text-[var(--text-secondary)]">
              Message
              <textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                maxLength={2000}
                placeholder="Add context for the moderation team..."
                className="focus-ring mt-2 min-h-[112px] w-full resize-y rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]"
              />
            </label>

            <div className="flex flex-wrap justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={closeModal} disabled={Boolean(busyAction)}>
                Cancel
              </Button>
              <Button size="sm" type="submit" disabled={Boolean(busyAction)}>
                <Flag size={14} />
                <span>{busyAction === 'report' ? 'Submitting...' : 'Submit report'}</span>
              </Button>
            </div>
          </form>
        )}
      </ModalShell>
    </>
  );
}
