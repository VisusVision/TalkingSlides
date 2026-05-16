import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ExternalLink,
  RefreshCcw,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import {
  approveModerationReviewRequest,
  getModerationReviewRequest,
  listModerationReviewRequests,
  rejectModerationReviewRequest,
  sendModerationReviewResponse,
} from '../api';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';

function normalizeReviewRequests(payload) {
  return Array.isArray(payload) ? payload : payload?.results || [];
}

function isOpenReview(review) {
  return String(review?.status || '').trim().toLowerCase() === 'open';
}

const REVIEW_STATUS_TABS = [
  { key: 'open', label: 'Open', countLabel: 'open' },
  { key: 'approved', label: 'Approved', countLabel: 'approved' },
  { key: 'rejected', label: 'Rejected', countLabel: 'rejected' },
  { key: 'all', label: 'All history', countLabel: 'total' },
];

function findingLocationLabel(finding) {
  if (finding?.location_label) return finding.location_label;
  if (finding?.slide_order !== undefined && finding?.slide_order !== null) {
    const slideNumber = Number(finding.slide_order) + 1;
    return Number.isFinite(slideNumber) ? `Slide ${slideNumber}` : 'Slide';
  }
  if (finding?.page_key) return `Page ${finding.page_key}`;
  if (finding?.timestamp_label) return finding.timestamp_label;
  return 'Project';
}

function findingKey(finding, index) {
  return `${finding?.category || 'finding'}-${finding?.object_id || finding?.page_key || index}`;
}

function severityTone(severity) {
  const normalized = String(severity || '').trim().toLowerCase();
  if (normalized === 'critical' || normalized === 'high') {
    return 'bg-[color:var(--status-danger-bg)] text-[color:var(--status-danger-fg)]';
  }
  if (normalized === 'medium') {
    return 'bg-[color:var(--status-warning-bg)] text-[color:var(--status-warning-fg)]';
  }
  return 'bg-[color:var(--surface-container-high)] text-[var(--text-secondary)]';
}

function reviewSummaryFindings(review, detail) {
  if (Array.isArray(detail?.findings) && detail.findings.length > 0) {
    return detail.findings;
  }
  return Array.isArray(review?.latest_findings_summary) ? review.latest_findings_summary : [];
}

function savedAdminResponse(review, detail) {
  return String(detail?.admin_response ?? review?.admin_response ?? '');
}

function draftAdminResponse(responsesById, review, detail) {
  if (!review?.id) return '';
  if (Object.prototype.hasOwnProperty.call(responsesById, review.id)) {
    return String(responsesById[review.id] || '');
  }
  return savedAdminResponse(review, detail);
}

export default function ModerationDashboard({ searchQuery = '' }) {
  const [reviewRequests, setReviewRequests] = useState([]);
  const [detailsById, setDetailsById] = useState({});
  const [expandedId, setExpandedId] = useState(null);
  const [responsesById, setResponsesById] = useState({});
  const [loading, setLoading] = useState(true);
  const [detailLoadingId, setDetailLoadingId] = useState(null);
  const [actionBusy, setActionBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [activeStatus, setActiveStatus] = useState('open');
  const [pendingDecision, setPendingDecision] = useState(null);

  const activeTab = useMemo(
    () => REVIEW_STATUS_TABS.find((tab) => tab.key === activeStatus) || REVIEW_STATUS_TABS[0],
    [activeStatus],
  );
  
  const filteredReviewRequests = useMemo(() => {
    if (!searchQuery) return reviewRequests;
    const q = searchQuery.toLowerCase();
    return reviewRequests.filter((review) => {
      const title = String(review.project_title || `Project #${review.project_id}`).toLowerCase();
      const publisher = String(review.requested_by_username || '').toLowerCase();
      const status = String(review.status || '').toLowerCase();
      const message = String(review.publisher_message || '').toLowerCase();
      const idStr = String(review.id);
      const projIdStr = String(review.project_id);
      
      return (
        title.includes(q)
        || publisher.includes(q)
        || status.includes(q)
        || message.includes(q)
        || idStr.includes(q)
        || projIdStr.includes(q)
      );
    });
  }, [reviewRequests, searchQuery]);

  const loadReviewRequests = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await listModerationReviewRequests(activeStatus);
      setReviewRequests(normalizeReviewRequests(payload));
    } catch (reviewError) {
      setReviewRequests([]);
      setError(reviewError.message || 'Could not load moderation review requests.');
    } finally {
      setLoading(false);
    }
  }, [activeStatus]);

  useEffect(() => {
    loadReviewRequests();
  }, [loadReviewRequests]);

  const handleToggleDetail = async (review) => {
    if (!review?.id) return;
    const nextExpanded = expandedId === review.id ? null : review.id;
    setExpandedId(nextExpanded);
    setError('');

    if (!nextExpanded || detailsById[review.id]) return;

    setDetailLoadingId(review.id);
    try {
      const detail = await getModerationReviewRequest(review.id);
      setDetailsById((previous) => ({ ...previous, [review.id]: detail }));
    } catch (detailError) {
      setError(detailError.message || 'Could not load moderation review request.');
    } finally {
      setDetailLoadingId(null);
    }
  };

  const mergeReviewPayload = useCallback((payload) => {
    if (!payload?.id) return;
    setReviewRequests((previous) => previous.map((item) => (
      item.id === payload.id ? { ...item, ...payload } : item
    )));
    setDetailsById((previous) => ({
      ...previous,
      [payload.id]: { ...(previous[payload.id] || {}), ...payload },
    }));
    setResponsesById((previous) => ({ ...previous, [payload.id]: String(payload.admin_response || '') }));
  }, []);

  const handleSendResponse = async (review, detail) => {
    if (!review?.id) return;
    const key = `response-${review.id}`;
    setActionBusy(key);
    setError('');
    setNotice('');
    try {
      const response = draftAdminResponse(responsesById, review, detail);
      const payload = await sendModerationReviewResponse(review.id, response);
      mergeReviewPayload(payload);
      setNotice('Admin response sent. Review request status was not changed.');
    } catch (responseError) {
      setError(responseError.message || 'Could not send admin response.');
    } finally {
      setActionBusy('');
    }
  };

  const handleDecision = async (review, decision, detail = null) => {
    if (!review?.id) return;
    const key = `${decision}-${review.id}`;
    setActionBusy(key);
    setError('');
    setNotice('');
    try {
      const response = draftAdminResponse(responsesById, review, detail);
      const payload = decision === 'approve'
        ? await approveModerationReviewRequest(review.id, response)
        : await rejectModerationReviewRequest(review.id, response);
      mergeReviewPayload(payload);
      if (decision === 'approve') {
        setNotice('Review request approved.');
      } else {
        setNotice('Review request rejected.');
      }
      setPendingDecision(null);
      await loadReviewRequests();
    } catch (decisionError) {
      setError(decisionError.message || 'Could not update moderation review request.');
    } finally {
      setActionBusy('');
    }
  };

  const requestDecision = (review, decision, detail) => {
    const response = draftAdminResponse(responsesById, review, detail);
    const savedResponse = savedAdminResponse(review, detail);
    if (response.trim() && response !== savedResponse) {
      setPendingDecision({ review, decision, response });
      return;
    }
    handleDecision(review, decision, detail);
  };

  return (
    <div className="space-y-6 pb-8">
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="label-sm">Staff Moderation</p>
          <h1 className="font-['Manrope'] text-4xl font-extrabold tracking-[-0.04em] text-[var(--text-primary)]">
            Moderation Dashboard
          </h1>
          <p className="mt-2 max-w-3xl text-sm text-[var(--text-secondary)]">
            Review publisher requests, inspect moderation findings, and approve or reject lessons that need staff attention.
          </p>
        </div>
        <Button variant="secondary" onClick={loadReviewRequests} disabled={loading || Boolean(actionBusy)}>
          <RefreshCcw size={16} />
          <span>Refresh</span>
        </Button>
      </header>

      {notice && (
        <SurfaceCard className="rounded-2xl bg-[color:var(--status-success-bg)] p-4">
          <p className="text-sm font-medium text-[color:var(--status-success-fg)]">{notice}</p>
        </SurfaceCard>
      )}

      {error && (
        <SurfaceCard className="rounded-2xl bg-[color:var(--feedback-danger-bg)] p-4">
          <p className="text-sm font-medium text-[color:var(--feedback-danger-fg)]">{error || 'Could not load moderation review requests.'}</p>
        </SurfaceCard>
      )}

      <SurfaceCard className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="label-sm">{activeTab.label}</p>
            <h2 className="font-['Manrope'] text-xl font-bold tracking-[-0.02em] text-[var(--text-primary)]">
              Review Requests
            </h2>
          </div>
          <span className="rounded-full bg-[color:var(--surface-muted)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
            {filteredReviewRequests.length} {activeTab.countLabel}
          </span>
        </div>

        <div className="flex flex-wrap gap-2">
          {REVIEW_STATUS_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => {
                setActiveStatus(tab.key);
                setExpandedId(null);
                setNotice('');
                setPendingDecision(null);
              }}
              className={`focus-ring rounded-full px-3 py-1.5 text-sm font-semibold transition ${
                activeStatus === tab.key
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-inverse)] shadow-sm'
                  : 'bg-[color:var(--surface-muted)] text-[var(--text-secondary)] hover:bg-[color:var(--hover-surface-strong)]'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {loading ? (
          <p className="text-sm text-[var(--text-secondary)]">Loading review requests...</p>
        ) : reviewRequests.length === 0 ? (
          <div className="rounded-2xl token-surface p-4">
            <div className="flex items-center gap-3">
              <span className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]">
                <ShieldCheck size={18} />
              </span>
              <div>
                <p className="font-semibold text-[var(--text-primary)]">No {activeTab.label.toLowerCase()} moderation review requests.</p>
                <p className="mt-1 text-sm text-[var(--text-secondary)]">
                  Use the smoke command in the operations docs if you need a test request.
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {filteredReviewRequests.map((review) => {
              const detail = detailsById[review.id] || null;
              const expanded = expandedId === review.id;
              const findings = reviewSummaryFindings(review, detail);
              const response = draftAdminResponse(responsesById, review, detail);
              const savedResponse = savedAdminResponse(review, detail);
              const responseChanged = response !== savedResponse;
              const approveBusy = actionBusy === `approve-${review.id}`;
              const rejectBusy = actionBusy === `reject-${review.id}`;
              const responseBusy = actionBusy === `response-${review.id}`;
              const canReview = isOpenReview(review);

              return (
                <article key={review.id} className="rounded-2xl token-surface p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="title-lg text-[var(--text-primary)]">
                          {review.project_title || `Project #${review.project_id}`}
                        </p>
                        <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1 text-xs font-semibold text-[var(--text-secondary)]">
                          Request #{review.id}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-[var(--text-secondary)]">
                        Project #{review.project_id} - requested by {review.requested_by_username || 'publisher'} - {review.moderation_status || 'moderation pending'}
                      </p>
                      {!canReview && (
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                          Reviewed by {review.reviewed_by_username || 'staff'}{review.reviewed_at ? ` on ${new Date(review.reviewed_at).toLocaleString()}` : ''}.
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2 text-xs font-semibold">
                      <span className={`rounded-full px-2.5 py-1 ${severityTone(review.highest_severity)}`}>
                        {review.highest_category || 'review'} / {review.highest_severity || 'unknown'}
                      </span>
                      <span className="rounded-full bg-[color:var(--status-warning-bg)] px-2.5 py-1 text-[color:var(--status-warning-fg)]">
                        {review.status || 'open'}
                      </span>
                    </div>
                  </div>

                  <p className="mt-3 text-sm text-[var(--text-secondary)]">
                    {review.publisher_message || 'No publisher message provided.'}
                  </p>

                  <div className="mt-3 flex flex-wrap gap-2">
                    <Link
                      to={`/studio?lesson=${review.project_id}`}
                      className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-3 text-sm font-medium text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)]"
                    >
                      <ExternalLink size={14} />
                      <span>Open in Studio</span>
                    </Link>
                    <Button size="sm" variant="secondary" onClick={() => handleToggleDetail(review)} disabled={detailLoadingId === review.id}>
                      <ChevronDown size={14} className={expanded ? 'rotate-180 transition' : 'transition'} />
                      <span>{expanded ? 'Hide details' : 'View details'}</span>
                    </Button>
                  </div>

                  {expanded && (
                    <div className="mt-4 space-y-3 rounded-xl border border-[var(--border-subtle)] p-3">
                      {detailLoadingId === review.id ? (
                        <p className="text-sm text-[var(--text-secondary)]">Loading request details...</p>
                      ) : (
                        <>
                          <div className="grid gap-2 text-xs text-[var(--text-secondary)] sm:grid-cols-2">
                            <p>Created: {review.created_at ? new Date(review.created_at).toLocaleString() : 'Unknown'}</p>
                            <p>Latest run: {detail?.run_id || detail?.project_moderation?.latest_run_id || 'Unknown'}</p>
                          </div>

                          <div className="space-y-2">
                            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[var(--text-secondary)]">
                              Findings
                            </p>
                            {findings.length === 0 ? (
                              <p className="text-sm text-[var(--text-secondary)]">No visible findings attached to this request.</p>
                            ) : (
                              findings.map((finding, index) => (
                                <div key={findingKey(finding, index)} className="rounded-xl bg-[color:var(--surface-muted)] p-3">
                                  <div className="flex flex-wrap gap-1.5 text-[0.68rem] font-semibold">
                                    <span className="rounded-full bg-[var(--surface-container-highest)] px-2 py-0.5 text-[var(--text-primary)]">
                                      {finding.category || 'unknown'}
                                    </span>
                                    <span className={`rounded-full px-2 py-0.5 ${severityTone(finding.severity)}`}>
                                      {finding.severity || 'low'}
                                    </span>
                                    {finding.decision && (
                                      <span className="rounded-full bg-[color:var(--surface-container-high)] px-2 py-0.5 text-[var(--text-secondary)]">
                                        {finding.decision}
                                      </span>
                                    )}
                                  </div>
                                  <p className="mt-2 text-sm text-[var(--text-primary)]">
                                    {finding.admin_message || finding.user_message || 'This content needs staff attention.'}
                                  </p>
                                  <p className="mt-1 text-xs text-[var(--text-secondary)]">{findingLocationLabel(finding)}</p>
                                  {finding.evidence_excerpt && (
                                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                      Evidence: {finding.evidence_excerpt}
                                    </p>
                                  )}
                                </div>
                              ))
                            )}
                          </div>
                        </>
                      )}
                    </div>
                  )}

                  {canReview ? (
                    <>
                      <label className="mt-4 block text-sm text-[var(--text-secondary)]">
                        Admin response
                        <textarea
                          value={response}
                          onChange={(event) => setResponsesById((previous) => ({
                            ...previous,
                            [review.id]: event.target.value,
                          }))}
                          maxLength={4000}
                          placeholder="Add a short response for the publisher..."
                          className="focus-ring mt-2 min-h-[88px] w-full resize-y rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]"
                        />
                      </label>

                      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                        <div className="inline-flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                          <AlertTriangle size={14} />
                          <span>Approving changes moderation to admin approved. Rejecting keeps publishing blocked.</span>
                        </div>
                        <div className="flex flex-wrap justify-end gap-2">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => handleSendResponse(review, detail)}
                            disabled={Boolean(actionBusy) || !responseChanged}
                          >
                            <span>{responseBusy ? 'Sending...' : 'Send response'}</span>
                          </Button>
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => requestDecision(review, 'reject', detail)}
                            disabled={Boolean(actionBusy)}
                          >
                            <XCircle size={14} />
                            <span>{rejectBusy ? 'Rejecting...' : 'Reject'}</span>
                          </Button>
                          <Button
                            size="sm"
                            onClick={() => requestDecision(review, 'approve', detail)}
                            disabled={Boolean(actionBusy)}
                          >
                            <CheckCircle2 size={14} />
                            <span>{approveBusy ? 'Approving...' : 'Approve'}</span>
                          </Button>
                        </div>
                      </div>
                      {responseChanged && (
                        <p className="mt-2 text-xs text-[var(--text-secondary)]">
                          Response text has unsent changes.
                        </p>
                      )}
                    </>
                  ) : (
                    <div className="mt-4 rounded-xl bg-[color:var(--surface-muted)] p-3 text-sm text-[var(--text-secondary)]">
                      {review.admin_response || 'No admin response was recorded.'}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </SurfaceCard>

      {pendingDecision && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4">
          <div role="dialog" aria-modal="true" className="w-full max-w-lg rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container)] p-5 shadow-2xl">
            <p className="label-sm">Confirm moderation decision</p>
            <h2 className="title-lg mt-2 text-[var(--text-primary)]">
              Do you want to {pendingDecision.decision === 'approve' ? 'approve' : 'reject'} with this response?
            </h2>
            <div className="mt-3 max-h-48 overflow-y-auto rounded-xl bg-[color:var(--surface-muted)] p-3 text-sm text-[var(--text-primary)]">
              <p className="whitespace-pre-wrap">{pendingDecision.response}</p>
            </div>
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setPendingDecision(null)} disabled={Boolean(actionBusy)}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant={pendingDecision.decision === 'reject' ? 'secondary' : 'primary'}
                onClick={() => handleDecision(pendingDecision.review, pendingDecision.decision, detailsById[pendingDecision.review.id] || null)}
                disabled={Boolean(actionBusy)}
              >
                {pendingDecision.decision === 'approve'
                  ? 'Confirm approve with response'
                  : 'Confirm reject with response'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
