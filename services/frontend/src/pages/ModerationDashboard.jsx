import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ExternalLink,
  Filter,
  RefreshCcw,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import {
  adminApproveLesson,
  adminBlockLesson,
  adminRequestLessonChanges,
  approveModerationReviewRequest,
  fetchAuthenticatedAssetBlobUrl,
  getModerationReviewRequest,
  listModerationReviewRequests,
  rejectModerationReviewRequest,
  runAdminProjectModerationAction,
  runModerationReportAction,
} from '../api';
import Button from '../components/ui/Button';
import SurfaceCard from '../components/ui/SurfaceCard';
import { featureEnabled, useCapabilities } from '../lib/capabilities';

function normalizeReviewRequests(payload) {
  return Array.isArray(payload) ? payload : payload?.results || [];
}

function normalizeReports(payload) {
  const reports = Array.isArray(payload) ? payload : payload?.results || [];
  return reports.map((report) => ({
    ...report,
    id: `report-${report.id}`,
    report_id: report.id,
    source_type: 'report',
    project_id: report.project_id,
    project_title: report.project_title,
    requested_by_username: report.reporter_username,
    publisher_username: report.publisher_username,
    status: report.status,
    moderation_status: 'user_report',
    publisher_message: `User report: ${report.category_label || report.category || 'Report'}${report.message ? `. ${report.message}` : ''}`,
    created_at: report.created_at,
  }));
}

function isOpenReview(review) {
  return String(review?.status || '').trim().toLowerCase() === 'open';
}

const REVIEW_TABS = [
  { key: 'open', label: 'Open', countLabel: 'items' },
  { key: 'history', label: 'History', countLabel: 'items' },
];

const OPEN_FILTERS = [
  { key: 'all', label: 'All open' },
  { key: 'review_requested', label: 'Review requested' },
  { key: 'auto_blocked', label: 'Auto blocked' },
  { key: 'manually_blocked', label: 'Manually blocked' },
  { key: 'visual', label: 'Visual' },
  { key: 'text_ocr', label: 'Text / OCR' },
  { key: 'reports', label: 'Reports' },
  { key: 'request_changes', label: 'Request changes' },
  { key: 'provider_unavailable', label: 'Provider unavailable' },
  { key: 'copyright', label: 'Copyright' },
  { key: 'other', label: 'Other' },
];

const HISTORY_FILTERS = [
  { key: 'all', label: 'All history' },
  { key: 'approved', label: 'Approved' },
  { key: 'rejected_blocked', label: 'Rejected / blocked' },
  { key: 'requested_changes', label: 'Requested changes' },
  { key: 'auto_blocked', label: 'Automatically blocked' },
  { key: 'reports_resolved', label: 'Reports resolved' },
  { key: 'copyright', label: 'Copyright' },
  { key: 'other', label: 'Other' },
];

function findingLocationLabel(finding) {
  if (finding?.asset_label) return finding.asset_label;
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
  return `${finding?.finding_id || finding?.id || finding?.category || 'finding'}-${finding?.object_id || finding?.page_key || index}`;
}

function findingIdentity(finding, index = 0) {
  return String(
    finding?.finding_id
      || finding?.id
      || [
        finding?.category || 'finding',
        finding?.asset_kind || '',
        finding?.object_id || '',
        finding?.page_key || '',
        finding?.slide_index ?? '',
        finding?.slide_number ?? '',
        index,
      ].join(':'),
  );
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

function findingReasonTitle(finding) {
  return String(finding?.reason_title || finding?.category || 'Moderation finding').replace(/_/g, ' ');
}

function findingAdminMessage(finding) {
  return String(
    finding?.admin_reason_message
      || finding?.reason_message
      || finding?.admin_message
      || finding?.user_message
      || 'This content needs staff attention.',
  );
}

function findingTechnicalLabel(finding) {
  const category = String(finding?.category || '').replace(/_/g, ' ');
  const severity = String(finding?.severity || '');
  return [category, severity].filter(Boolean).join(' / ');
}

const VISUAL_ASSET_KINDS = new Set([
  'cover',
  'custom_background',
  'slide_image',
  'draft_visual_asset',
  'video_frame',
  'profile_image',
  'channel_logo',
  'channel_banner',
]);

const TEXT_CONTENT_TYPES = new Set(['text', 'ocr', 'transcript', 'subtitle', 'language']);

function issueAssetKind(issue) {
  return String(issue?.asset_kind || '').trim().toLowerCase();
}

function isTextIssue(issue) {
  const contentType = String(issue?.content_type || '').trim().toLowerCase();
  const provider = String(issue?.provider || '').trim().toLowerCase();
  const objectType = String(issue?.object_type || '').trim().toLowerCase();
  const category = String(issue?.category || '').trim().toLowerCase();
  return TEXT_CONTENT_TYPES.has(contentType)
    || provider.includes('ocr')
    || provider.includes('text')
    || objectType.includes('ocr')
    || [
      'abusive_language',
      'copyright_text',
      'dangerous_instruction',
      'hate_or_harassment',
      'inappropriate_language',
      'language',
      'profanity',
      'self_harm_instruction',
      'sexual_text',
      'text_moderation',
      'violence_text',
    ].includes(category);
}

function isVisualIssue(issue) {
  if (!issue || isTextIssue(issue)) return false;
  const kind = issueAssetKind(issue);
  const contentType = String(issue?.content_type || '').trim().toLowerCase();
  const provider = String(issue?.provider || '').trim().toLowerCase();
  const category = String(issue?.category || '').trim().toLowerCase();
  return VISUAL_ASSET_KINDS.has(kind)
    || ['image', 'video_frame'].includes(contentType)
    || provider.includes('visual')
    || ['sexual', 'violence', 'graphic_content', 'self_harm', 'provider_unavailable'].includes(category);
}

function issueStatusLabel(issue) {
  const decision = String(issue?.decision || '').trim().toLowerCase();
  if (['block', 'blocked', 'rejected', 'revision_required'].includes(decision)) return 'Blocked';
  if (decision === 'approved' || decision === 'allow') return 'Approved';
  if (decision === 'needs_admin_review') return 'Needs admin review';
  return 'Needs review';
}

function reviewVisualIssues(review, detail) {
  const detailIssues = Array.isArray(detail?.visual_issues) ? detail.visual_issues : [];
  if (detailIssues.length > 0) return detailIssues.filter(isVisualIssue);
  const reviewIssues = Array.isArray(review?.visual_issues) ? review.visual_issues : [];
  if (reviewIssues.length > 0) return reviewIssues.filter(isVisualIssue);
  return reviewSummaryFindings(review, detail).filter(isVisualIssue);
}

function mergeFindingsWithVisualIssues(findings = [], visualIssues = []) {
  const merged = Array.isArray(findings) ? [...findings] : [];
  const seen = new Set(merged.map((finding, index) => findingIdentity(finding, index)));
  (Array.isArray(visualIssues) ? visualIssues : []).forEach((issue, index) => {
    const key = findingIdentity(issue, index);
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(issue);
  });
  return merged;
}

function primaryVisualIssue(review, detail) {
  const issues = reviewVisualIssues(review, detail);
  if (issues.length > 0) return issues[0];
  if (review?.primary_reason_title || review?.primary_asset_label) {
    return {
      reason_title: review.primary_reason_title,
      reason_message: review.primary_reason_message,
      admin_reason_message: review.primary_reason_message,
      asset_label: review.primary_asset_label,
      category: review.highest_category,
      severity: review.highest_severity,
    };
  }
  return null;
}

function primaryModerationIssue(review, detail) {
  const visualIssue = primaryVisualIssue(review, detail);
  if (visualIssue) return visualIssue;
  const findings = reviewSummaryFindings(review, detail);
  if (findings.length > 0) return findings[0];
  if (review?.primary_reason_title || review?.primary_asset_label) {
    return {
      reason_title: review.primary_reason_title,
      reason_message: review.primary_reason_message,
      admin_reason_message: review.primary_reason_message,
      asset_label: review.primary_asset_label,
      category: review.highest_category,
      severity: review.highest_severity,
    };
  }
  return null;
}

function requestChangesMessageForIssue(review, detail) {
  const issue = primaryModerationIssue(review, detail);
  const kind = issueAssetKind(issue);
  if (kind === 'cover') return 'Please replace the lesson cover image.';
  if (kind === 'custom_background') return 'Please replace the custom background image.';
  if (kind === 'slide_image' || kind === 'draft_visual_asset') {
    const slideNumber = Number(issue?.slide_number || (Number(issue?.slide_order) + 1));
    if (Number.isFinite(slideNumber) && slideNumber > 0) return `Please replace Slide ${slideNumber} image.`;
    return 'Please replace the flagged slide image.';
  }
  if (isTextIssue(issue)) return 'Please remove or rewrite the highlighted transcript text.';
  const label = String(issue?.asset_label || '').trim();
  if (label && isVisualIssue(issue)) return `Please replace the ${label.toLowerCase()}.`;
  return '';
}

function reviewAllowed(review, action) {
  const actions = Array.isArray(review?.allowed_actions) ? review.allowed_actions : [];
  if (actions.length === 0) return true;
  return actions.includes(action);
}

function sourceLabel(review) {
  const source = String(review?.source || review?.source_type || '').replace(/_/g, ' ');
  return source ? source.replace(/\b\w/g, (letter) => letter.toUpperCase()) : 'Moderation';
}

function itemTimestamp(review) {
  return review?.item_time || review?.updated_at || review?.reviewed_at || review?.created_at;
}

function ModerationPreview({ issue }) {
  const sourceUrl = String(issue?.preview_url || issue?.asset_url || '').trim();
  const [blobUrl, setBlobUrl] = useState('');
  const [loading, setLoading] = useState(Boolean(sourceUrl));
  const [failed, setFailed] = useState(!sourceUrl);
  const [statusCode, setStatusCode] = useState('');

  useEffect(() => {
    let cancelled = false;
    let objectUrl = '';
    setBlobUrl('');
    setStatusCode('');
    setFailed(!sourceUrl);
    setLoading(Boolean(sourceUrl));
    if (!sourceUrl) return undefined;
    fetchAuthenticatedAssetBlobUrl(sourceUrl)
      .then((url) => {
        if (cancelled) {
          if (url) URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setBlobUrl(url);
        setFailed(!url);
        setLoading(false);
      })
      .catch((error) => {
        if (!cancelled) {
          setStatusCode(error?.status ? String(error.status) : 'error');
          setFailed(true);
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [sourceUrl]);

  if (loading) {
    return (
      <div
        className="flex min-h-20 w-full items-center rounded-lg bg-[color:var(--surface-container-high)] px-3 py-2 text-xs font-medium text-[var(--text-secondary)] sm:w-40"
        data-preview-url-present={sourceUrl ? 'true' : 'false'}
        data-preview-fetch-status="loading"
      >
        Loading preview...
      </div>
    );
  }

  if (failed || !blobUrl) {
    return (
      <div
        className="flex min-h-20 w-full items-center rounded-lg bg-[color:var(--surface-container-high)] px-3 py-2 text-xs font-medium text-[var(--text-secondary)] sm:w-40"
        data-preview-url-present={sourceUrl ? 'true' : 'false'}
        data-preview-fetch-status={statusCode || (sourceUrl ? 'failed' : 'missing')}
      >
        Preview unavailable. Open read-only Studio to inspect this lesson.
      </div>
    );
  }

  return (
    <img
      src={blobUrl}
      alt={findingLocationLabel(issue)}
      className="h-24 w-full rounded-lg object-cover sm:w-40"
      data-preview-url-present="true"
      data-preview-fetch-status="200"
      onError={() => setFailed(true)}
    />
  );
}

export default function ModerationDashboard({ searchQuery = '' }) {
  const { capabilities } = useCapabilities();
  const visualModerationEnabled = featureEnabled(capabilities, 'visual_moderation');
  const [reviewRequests, setReviewRequests] = useState([]);
  const [detailsById, setDetailsById] = useState({});
  const [expandedId, setExpandedId] = useState(null);
  const [responsesById, setResponsesById] = useState({});
  const [loading, setLoading] = useState(true);
  const [detailLoadingId, setDetailLoadingId] = useState(null);
  const [actionBusy, setActionBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [activeTabKey, setActiveTabKey] = useState('open');
  const [activeFilter, setActiveFilter] = useState('all');
  const [filterPanelOpen, setFilterPanelOpen] = useState(false);
  const [pendingDecision, setPendingDecision] = useState(null);
  const [requestChangesDialog, setRequestChangesDialog] = useState(null);
  const filterPanelRef = useRef(null);

  const activeTab = useMemo(
    () => REVIEW_TABS.find((tab) => tab.key === activeTabKey) || REVIEW_TABS[0],
    [activeTabKey],
  );
  const activeFilters = activeTabKey === 'history' ? HISTORY_FILTERS : OPEN_FILTERS;
  const activeFilterOption = activeFilters.find((filter) => filter.key === activeFilter) || activeFilters[0];
  const activeFilterCount = activeFilter === 'all' ? 0 : 1;
  const activeFilterLabel = activeFilterCount ? activeFilterOption.label : 'No filters';
  
  const filteredReviewRequests = useMemo(() => {
    if (!searchQuery) return reviewRequests;
    const q = searchQuery.toLowerCase();
    return reviewRequests.filter((review) => {
      const title = String(review.project_title || `Project #${review.project_id}`).toLowerCase();
      const publisher = String(review.publisher_username || review.requested_by_username || '').toLowerCase();
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
      const payload = await listModerationReviewRequests({ tab: activeTabKey, filter: activeFilter });
      setReviewRequests(normalizeReviewRequests(payload));
    } catch (reviewError) {
      setReviewRequests([]);
      setError(reviewError.message || 'Could not load moderation review requests.');
    } finally {
      setLoading(false);
    }
  }, [activeFilter, activeTabKey]);

  useEffect(() => {
    loadReviewRequests();
  }, [loadReviewRequests]);

  useEffect(() => {
    if (!filterPanelOpen) return undefined;
    const handlePointerDown = (event) => {
      if (filterPanelRef.current?.contains(event.target)) return;
      setFilterPanelOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setFilterPanelOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('touchstart', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('touchstart', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [filterPanelOpen]);

  const handleToggleDetail = async (review) => {
    if (!review?.id) return;
    const nextExpanded = expandedId === review.id ? null : review.id;
    setExpandedId(nextExpanded);
    setError('');

    if (!nextExpanded || detailsById[review.id]) return;
    if (review.source_type !== 'review_request') return;

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

  const handleDecision = async (review, decision, detail = null) => {
    if (!review?.project_id) return;
    const key = `${decision}-${review.id}`;
    setActionBusy(key);
    setError('');
    setNotice('');
    try {
      const response = draftAdminResponse(responsesById, review, detail);
      if (review.source_type === 'review_request') {
        const payload = decision === 'approve'
          ? await approveModerationReviewRequest(review.id, response)
          : await rejectModerationReviewRequest(review.id, response);
        mergeReviewPayload(payload);
      } else if (decision === 'approve') {
        await adminApproveLesson(review.project_id, response);
      } else {
        await adminBlockLesson(review.project_id, response);
      }
      if (decision === 'approve') {
        setNotice('Lesson approved.');
      } else {
        setNotice('Lesson blocked.');
      }
      setPendingDecision(null);
      await loadReviewRequests();
    } catch (decisionError) {
      setError(decisionError.message || 'Could not update moderation review request.');
    } finally {
      setActionBusy('');
    }
  };

  const openRequestChangesDialog = (review, detail = null) => {
    const response = draftAdminResponse(responsesById, review, detail).trim();
    setRequestChangesDialog({
      review,
      message: response || requestChangesMessageForIssue(review, detail),
      unpublish: true,
    });
    setError('');
  };

  const handleRequestChanges = async () => {
    const review = requestChangesDialog?.review;
    const message = String(requestChangesDialog?.message || '').trim();
    if (!review?.project_id) return;
    if (!message) {
      setError('A message is required when requesting changes.');
      return;
    }
    const key = `request_changes-${review.id}`;
    setActionBusy(key);
    setError('');
    setNotice('');
    try {
      const payload = await adminRequestLessonChanges(review.project_id, {
        reason: message,
        unpublish: Boolean(requestChangesDialog?.unpublish),
      });
      setRequestChangesDialog(null);
      setNotice(payload?.message || 'Moderation action saved.');
      await loadReviewRequests();
    } catch (actionError) {
      setError(actionError.message || 'Could not update moderation state.');
    } finally {
      setActionBusy('');
    }
  };

  const handleDismissReport = async (review) => {
    const reportId = review?.report_id || String(review?.id || '').replace(/^report-/, '');
    if (!reportId) return;
    const key = `dismiss-${review.id}`;
    setActionBusy(key);
    setError('');
    setNotice('');
    try {
      await runModerationReportAction(reportId, 'dismiss');
      setNotice('Report dismissed.');
      await loadReviewRequests();
    } catch (actionError) {
      setError(actionError.message || 'Could not dismiss report.');
    } finally {
      setActionBusy('');
    }
  };

  const handleRescan = async (review, detail = null) => {
    if (!review?.project_id) return;
    const key = `rescan-${review.id}`;
    setActionBusy(key);
    setError('');
    setNotice('');
    try {
      const response = draftAdminResponse(responsesById, review, detail);
      await runAdminProjectModerationAction(review.project_id, 'rescan', response, 'manual_admin_rescan');
      setNotice('Moderation rescan started.');
      await loadReviewRequests();
    } catch (actionError) {
      setError(actionError.message || 'Could not start moderation rescan.');
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
        <div className="flex flex-wrap items-center gap-2">
          <span className={`rounded-full px-3 py-1.5 text-xs font-semibold ${
            visualModerationEnabled
              ? 'bg-[color:var(--status-success-bg)] text-[color:var(--status-success-fg)]'
              : 'bg-[color:var(--surface-muted)] text-[var(--text-secondary)]'
          }`}>
            {visualModerationEnabled ? 'Visual scan enabled' : 'Visual scan disabled'}
          </span>
          <Button variant="secondary" onClick={loadReviewRequests} disabled={loading || Boolean(actionBusy)}>
            <RefreshCcw size={16} />
            <span>Refresh</span>
          </Button>
        </div>
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
              Moderation queue
            </h2>
          </div>
          <span className="rounded-full bg-[color:var(--surface-muted)] px-3 py-1 text-xs font-semibold text-[var(--text-secondary)]">
            {filteredReviewRequests.length} {activeTab.countLabel}
          </span>
        </div>

        <div className="flex flex-wrap gap-2">
          {REVIEW_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => {
                setActiveTabKey(tab.key);
                setActiveFilter('all');
                setExpandedId(null);
                setNotice('');
                setPendingDecision(null);
                setRequestChangesDialog(null);
                setFilterPanelOpen(false);
              }}
              className={`focus-ring rounded-full px-3 py-1.5 text-sm font-semibold transition ${
                activeTabKey === tab.key
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-inverse)] shadow-sm'
                  : 'bg-[color:var(--surface-muted)] text-[var(--text-secondary)] hover:bg-[color:var(--hover-surface-strong)]'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="relative" ref={filterPanelRef}>
          <button
            type="button"
            onClick={() => setFilterPanelOpen((open) => !open)}
            className={`focus-ring inline-flex w-full items-center justify-center gap-2 rounded-full px-3 py-2 text-sm font-semibold transition sm:w-auto ${
              activeFilterCount
                ? 'bg-[var(--accent-primary)] text-[var(--accent-inverse)] shadow-sm'
                : 'bg-[color:var(--surface-muted)] text-[var(--text-primary)] hover:bg-[color:var(--hover-surface-strong)]'
            }`}
            aria-expanded={filterPanelOpen}
          >
            <Filter size={15} />
            <span>{activeFilterCount ? activeFilterLabel : 'Filters'}</span>
            {activeFilterCount > 0 && (
              <span className="rounded-full bg-[color:rgba(255,255,255,0.22)] px-2 py-0.5 text-[0.68rem] font-bold text-current">
                {activeFilterCount}
              </span>
            )}
            <ChevronDown size={14} className={filterPanelOpen ? 'rotate-180 transition' : 'transition'} />
          </button>

          {filterPanelOpen && (
            <div className="absolute left-0 z-20 mt-2 w-[min(64rem,calc(100vw-2rem))] max-w-[calc(100vw-2rem)] rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 shadow-xl">
              <div className="flex flex-wrap gap-2">
                {activeFilters.map((filter) => (
                  <button
                    key={filter.key}
                    type="button"
                    onClick={() => {
                      setActiveFilter(filter.key);
                      setExpandedId(null);
                      setNotice('');
                      setPendingDecision(null);
                      setFilterPanelOpen(false);
                    }}
                    className={`focus-ring min-w-0 rounded-full px-3 py-2 text-left text-xs font-semibold transition ${
                      activeFilter === filter.key
                        ? 'bg-[color:var(--surface-container-highest)] text-[var(--text-primary)] ring-1 ring-[var(--accent-primary)]'
                        : 'bg-[color:var(--surface-muted)] text-[var(--text-secondary)] hover:bg-[color:var(--hover-surface-strong)]'
                    }`}
                  >
                    {filter.label}
                  </button>
                ))}
              </div>
            </div>
          )}
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
              const response = draftAdminResponse(responsesById, review, detail);
              const savedResponse = savedAdminResponse(review, detail);
              const responseChanged = response !== savedResponse;
              const approveBusy = actionBusy === `approve-${review.id}`;
              const rejectBusy = actionBusy === `reject-${review.id}`;
              const requestChangesBusy = actionBusy === `request_changes-${review.id}`;
              const rescanBusy = actionBusy === `rescan-${review.id}`;
              const canReview = reviewAllowed(review, 'approve') || reviewAllowed(review, 'reject_block') || reviewAllowed(review, 'request_changes');
              const rescanRelevant = reviewAllowed(review, 'rescan');
              const canDismissReport = reviewAllowed(review, 'dismiss_report');
              const rejectLabel = reviewAllowed(review, 'keep_blocked') ? 'Keep blocked' : 'Reject / Block';
              const approveLabel = reviewAllowed(review, 'reopen_unreject') ? 'Reopen / Unreject' : review.queue === 'auto_rejected' ? 'Approve override' : 'Approve';
              const timeValue = itemTimestamp(review);
              const visualIssues = reviewVisualIssues(review, detail);
              const findings = mergeFindingsWithVisualIssues(reviewSummaryFindings(review, detail), visualIssues);
              const primaryIssue = primaryModerationIssue(review, detail);
              const primaryTitle = primaryIssue ? findingReasonTitle(primaryIssue) : (review.reason_label || review.reason_category || 'Moderation review');
              const primaryMessage = primaryIssue ? findingAdminMessage(primaryIssue) : (review.latest_message || review.publisher_message || 'No message provided.');
              const primaryGuidance = isVisualIssue(primaryIssue)
                ? 'Open details to inspect the visual preview before deciding.'
                : isTextIssue(primaryIssue)
                  ? 'Review the transcript text and request a rewrite if needed.'
                  : 'Review the attached moderation details before deciding.';

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
                        Project #{review.project_id} - publisher {review.publisher_username || 'unknown'} - {review.moderation_status || 'moderation pending'}
                      </p>
                      <p className="mt-1 text-xs text-[var(--text-secondary)]">
                        Source: {sourceLabel(review)}
                        {primaryTitle ? ` - Reason: ${primaryTitle}` : ''}
                        {timeValue ? ` - ${new Date(timeValue).toLocaleString()}` : ''}
                      </p>
                      {!canReview && review.reviewed_at && (
                        <p className="mt-1 text-xs text-[var(--text-secondary)]">
                          Reviewed by {review.reviewed_by_username || 'staff'} on {new Date(review.reviewed_at).toLocaleString()}.
                        </p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2 text-xs font-semibold">
                      <span className={`rounded-full px-2.5 py-1 ${severityTone(review.highest_severity)}`}>
                        {primaryTitle}
                      </span>
                      {review.highest_category || review.highest_severity ? (
                        <span className="rounded-full bg-[color:var(--surface-container-high)] px-2.5 py-1 text-[var(--text-secondary)]">
                          {review.highest_category || 'review'} / {review.highest_severity || 'unknown'}
                        </span>
                      ) : null}
                      <span className="rounded-full bg-[color:var(--status-warning-bg)] px-2.5 py-1 text-[color:var(--status-warning-fg)]">
                        {review.status || 'open'}
                      </span>
                      {visualIssues.length > 0 && (
                        <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1 text-[var(--text-secondary)]">
                          {visualIssues.length} visual {visualIssues.length === 1 ? 'issue' : 'issues'}
                        </span>
                      )}
                      {(review.finding_badges || []).slice(0, 3).map((badge) => (
                        <span key={badge} className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1 text-[var(--text-secondary)]">
                          {String(badge).replace(/_/g, ' ')}
                        </span>
                      ))}
                    </div>
                  </div>

                  <p className="mt-3 text-sm text-[var(--text-secondary)]">
                    {primaryMessage}
                  </p>
                  {primaryIssue && (
                    <div className="mt-3 rounded-xl border border-[color:var(--status-warning-fg)] bg-[color:var(--status-warning-bg)] p-3">
                      <div className="grid gap-2 text-sm sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
                        <div className="min-w-0">
                          <p className="font-semibold text-[var(--text-primary)]">{primaryTitle}</p>
                          <p className="mt-1 text-[var(--text-secondary)]">{findingLocationLabel(primaryIssue)}</p>
                          <p className="mt-1 text-[var(--text-secondary)]">{primaryGuidance}</p>
                        </div>
                        <span className="rounded-full bg-[color:var(--surface-container-high)] px-2.5 py-1 text-xs font-semibold text-[var(--text-secondary)]">
                          {issueStatusLabel(primaryIssue)}
                        </span>
                      </div>
                      {findingTechnicalLabel(primaryIssue) && (
                        <p className="mt-2 text-xs text-[var(--text-secondary)]">
                          Technical: {findingTechnicalLabel(primaryIssue)}
                        </p>
                      )}
                    </div>
                  )}

                  <div className="mt-3 flex flex-wrap gap-2">
                    <Link
                      to={`/studio?view=editor&lesson=${review.project_id}&review=1`}
                      className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-3 text-sm font-medium text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)]"
                    >
                      <ExternalLink size={14} />
                      <span>Open in read-only Studio</span>
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
                                  <div className="flex flex-col gap-3 sm:flex-row">
                                    {isVisualIssue(finding) && <ModerationPreview issue={finding} />}
                                    <div className="min-w-0 flex-1">
                                      <div className="flex flex-wrap gap-1.5 text-[0.68rem] font-semibold">
                                        <span className="rounded-full bg-[var(--surface-container-highest)] px-2 py-0.5 text-[var(--text-primary)]">
                                          {findingReasonTitle(finding)}
                                        </span>
                                        <span className={`rounded-full px-2 py-0.5 ${severityTone(finding.severity)}`}>
                                          {findingTechnicalLabel(finding) || 'review'}
                                        </span>
                                        {finding.decision && (
                                          <span className="rounded-full bg-[color:var(--surface-container-high)] px-2 py-0.5 text-[var(--text-secondary)]">
                                            {String(finding.decision).replace(/_/g, ' ')}
                                          </span>
                                        )}
                                      </div>
                                      <p className="mt-2 text-sm text-[var(--text-primary)]">
                                        {findingAdminMessage(finding)}
                                      </p>
                                      <p className="mt-1 text-xs font-semibold text-[var(--text-secondary)]">{findingLocationLabel(finding)}</p>
                                      {finding.evidence_excerpt || finding.technical_reason ? (
                                        <details className="mt-1 text-xs text-[var(--text-secondary)]">
                                          <summary className="cursor-pointer font-semibold text-[var(--accent-primary)]">Technical details</summary>
                                          <p className="mt-1 break-words">
                                            {finding.evidence_excerpt || finding.technical_reason}
                                          </p>
                                        </details>
                                      ) : null}
                                    </div>
                                  </div>
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
                            onClick={() => openRequestChangesDialog(review, detail)}
                            disabled={Boolean(actionBusy)}
                          >
                            <XCircle size={14} />
                            <span>{requestChangesBusy ? 'Requesting...' : 'Request changes'}</span>
                          </Button>
                          {canDismissReport && (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => handleDismissReport(review)}
                              disabled={Boolean(actionBusy)}
                            >
                              <XCircle size={14} />
                              <span>{actionBusy === `dismiss-${review.id}` ? 'Dismissing...' : 'Dismiss report'}</span>
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => requestDecision(review, 'reject', detail)}
                            disabled={Boolean(actionBusy)}
                          >
                            <XCircle size={14} />
                            <span>{rejectBusy ? 'Blocking...' : rejectLabel}</span>
                          </Button>
                          {rescanRelevant && (
                            <Button
                              size="sm"
                              variant="secondary"
                              onClick={() => handleRescan(review, detail)}
                              disabled={Boolean(actionBusy)}
                            >
                              <RefreshCcw size={14} />
                              <span>{rescanBusy ? 'Starting...' : 'Rescan'}</span>
                            </Button>
                          )}
                          <Button
                            size="sm"
                            onClick={() => requestDecision(review, 'approve', detail)}
                            disabled={Boolean(actionBusy)}
                          >
                            <CheckCircle2 size={14} />
                            <span>{approveBusy ? 'Approving...' : approveLabel}</span>
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

      {requestChangesDialog && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4">
          <div role="dialog" aria-modal="true" className="w-full max-w-lg rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-container)] p-5 shadow-2xl">
            <p className="label-sm">Request changes</p>
            <h2 className="title-lg mt-2 text-[var(--text-primary)]">
              Send a required message to the publisher
            </h2>
            <label className="mt-4 block text-sm text-[var(--text-secondary)]">
              Message
              <textarea
                value={requestChangesDialog.message}
                onChange={(event) => setRequestChangesDialog((previous) => ({
                  ...previous,
                  message: event.target.value,
                }))}
                maxLength={4000}
                className="focus-ring mt-2 min-h-[120px] w-full resize-y rounded-xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 text-sm text-[var(--text-primary)]"
                placeholder="Explain what needs to change before this lesson can be approved."
              />
            </label>
            <label className="mt-3 flex items-start gap-2 text-sm text-[var(--text-secondary)]">
              <input
                type="checkbox"
                checked={Boolean(requestChangesDialog.unpublish)}
                onChange={(event) => setRequestChangesDialog((previous) => ({
                  ...previous,
                  unpublish: event.target.checked,
                }))}
                className="mt-1"
              />
              <span>Block public access until the publisher updates and requests review.</span>
            </label>
            <div className="mt-4 flex flex-wrap justify-end gap-2">
              <Button size="sm" variant="ghost" onClick={() => setRequestChangesDialog(null)} disabled={Boolean(actionBusy)}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="primary"
                onClick={handleRequestChanges}
                disabled={Boolean(actionBusy) || !String(requestChangesDialog.message || '').trim()}
              >
                {actionBusy ? 'Sending...' : 'Send request changes'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
