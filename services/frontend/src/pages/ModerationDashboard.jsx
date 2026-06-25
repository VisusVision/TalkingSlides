import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
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
import { usePageLoading } from '../components/ui/PageLoading';
import { featureEnabled, useCapabilities } from '../lib/capabilities';
import {
  clearRouteSessionState,
  onRouteReset,
  readRouteSessionState,
  safeInternalReturnTo,
  writeRouteSessionState,
} from '../utils/routeSession';

function normalizeReviewRequests(payload) {
  return Array.isArray(payload) ? payload : payload?.results || [];
}

const MODERATION_PAGE_SIZE = 25;

function moderationItemKey(item) {
  return `${item?.source_type || 'review_request'}:${item?.id}`;
}

function parseModerationLocation(search) {
  const params = new URLSearchParams(search || '');
  const tab = String(params.get('tab') || '').trim();
  const filter = String(params.get('filter') || '').trim();
  const item = String(params.get('item') || '').trim();
  const reviewId = String(params.get('review') || '').trim();
  const reportId = String(params.get('report') || '').trim();
  return {
    hasDirectState: Boolean(tab || filter || item || reviewId || reportId),
    activeTabKey: REVIEW_TABS.some((candidate) => candidate.key === tab) ? tab : '',
    activeFilter: normalizedModerationFilter(tab, filter),
    expandedId: item
      || (reviewId && reviewId !== '1' ? `review_request:${reviewId}` : '')
      || (reportId ? `report:report-${reportId}` : ''),
  };
}

function moderationReturnPath(location, { tab, filter, itemKey } = {}) {
  const params = new URLSearchParams(location.search || '');
  params.set('tab', tab || 'open');
  params.set('filter', filter || 'all');
  if (itemKey) params.set('item', itemKey);
  const query = params.toString();
  return `${location.pathname || '/moderation'}${query ? `?${query}` : ''}`;
}

function studioReviewUrl(review, returnPath) {
  const projectId = review?.project_id;
  if (!projectId) return '/moderation';
  const params = new URLSearchParams();
  params.set('mode', 'review');
  params.set('view', 'editor');
  params.set('lesson', String(projectId));
  params.set('source', 'moderation');
  params.set('sourceItem', moderationItemKey(review));
  params.set('returnTo', safeInternalReturnTo(returnPath, '/moderation'));
  if (review?.source_type === 'review_request' && review.id) {
    params.set('review', String(review.id));
  }
  const reportId = review?.report_id || (
    review?.source_type === 'report'
      ? String(review.id || '').replace(/^report-/, '')
      : ''
  );
  if (reportId) params.set('report', String(reportId));
  if (review?.admin_review_request_id) {
    params.set('review', String(review.admin_review_request_id));
  }
  return `/studio?${params.toString()}`;
}

function appendUniqueModerationItems(previous, incoming) {
  const merged = Array.isArray(previous) ? [...previous] : [];
  const seen = new Set(merged.map(moderationItemKey));
  (Array.isArray(incoming) ? incoming : []).forEach((item) => {
    const key = moderationItemKey(item);
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(item);
  });
  return merged;
}

function normalizeReviewRequestPage(payload, { limit = MODERATION_PAGE_SIZE, offset = 0 } = {}) {
  const results = normalizeReviewRequests(payload);
  const count = Number(payload?.count ?? payload?.total ?? results.length);
  const safeCount = Number.isFinite(count) ? count : results.length;
  const nextOffset = payload?.next_offset ?? payload?.next ?? null;
  const numericNextOffset = Number(nextOffset);
  const fallbackHasMore = offset + results.length < safeCount;
  const hasMore = Boolean(payload?.has_more ?? (Number.isFinite(numericNextOffset) ? true : fallbackHasMore));
  return {
    results,
    count: safeCount,
    limit: Number(payload?.limit ?? limit) || limit,
    offset: Number(payload?.offset ?? offset) || offset,
    hasMore,
    nextOffset: hasMore
      ? (Number.isFinite(numericNextOffset) ? numericNextOffset : offset + results.length)
      : null,
  };
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

function normalizedModerationFilter(tabKey, filterKey) {
  const tab = REVIEW_TABS.some((candidate) => candidate.key === tabKey) ? tabKey : 'open';
  const filters = tab === 'history' ? HISTORY_FILTERS : OPEN_FILTERS;
  const filter = String(filterKey || '').trim();
  return filters.some((candidate) => candidate.key === filter) ? filter : '';
}

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
  const key = moderationItemKey(review);
  if (!review?.id) return '';
  if (Object.prototype.hasOwnProperty.call(responsesById, key)) {
    return String(responsesById[key] || '');
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

export default function ModerationDashboard({ user, searchQuery = '' }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { capabilities } = useCapabilities();
  const visualModerationEnabled = featureEnabled(capabilities, 'visual_moderation');
  const directModerationState = useMemo(
    () => parseModerationLocation(location.search),
    [location.search],
  );
  const storedModerationState = useMemo(
    () => (directModerationState.hasDirectState ? {} : readRouteSessionState('moderation', user)),
    [directModerationState.hasDirectState, user],
  );
  const initialModerationTabKey = directModerationState.activeTabKey
    || (REVIEW_TABS.some((tab) => tab.key === storedModerationState.activeTabKey)
      ? storedModerationState.activeTabKey
      : 'open');
  const initialModerationFilter = directModerationState.activeFilter
    || normalizedModerationFilter(initialModerationTabKey, storedModerationState.activeFilter)
    || 'all';
  const [reviewRequests, setReviewRequests] = useState([]);
  const [detailsById, setDetailsById] = useState({});
  const [expandedId, setExpandedId] = useState(
    () => directModerationState.expandedId || String(storedModerationState.expandedId || '') || null,
  );
  const [responsesById, setResponsesById] = useState({});
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [detailLoadingId, setDetailLoadingId] = useState(null);
  const [actionBusy, setActionBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [activeTabKey, setActiveTabKey] = useState(
    () => initialModerationTabKey,
  );
  const [activeFilter, setActiveFilter] = useState(
    () => initialModerationFilter,
  );
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState(String(searchQuery || '').trim());
  const [pageInfo, setPageInfo] = useState({
    count: 0,
    hasMore: false,
    nextOffset: null,
    limit: MODERATION_PAGE_SIZE,
    offset: 0,
  });
  const [filterPanelOpen, setFilterPanelOpen] = useState(false);
  const [pendingDecision, setPendingDecision] = useState(null);
  const [requestChangesDialog, setRequestChangesDialog] = useState(null);
  const filterPanelRef = useRef(null);
  const loadMoreRef = useRef(null);
  const requestSequenceRef = useRef(0);
  const initialPageLimitRef = useRef(
    Math.max(MODERATION_PAGE_SIZE, Number(storedModerationState.loadedCount || 0) || 0),
  );

  const activeTab = useMemo(
    () => REVIEW_TABS.find((tab) => tab.key === activeTabKey) || REVIEW_TABS[0],
    [activeTabKey],
  );
  const activeFilters = activeTabKey === 'history' ? HISTORY_FILTERS : OPEN_FILTERS;
  const activeFilterOption = activeFilters.find((filter) => filter.key === activeFilter) || activeFilters[0];
  const activeFilterCount = activeFilter === 'all' ? 0 : 1;
  const activeFilterLabel = activeFilterCount ? activeFilterOption.label : 'No filters';
  const activeSearchQuery = debouncedSearchQuery.trim();
  const showingCount = reviewRequests.length;
  const totalCount = pageInfo.count || showingCount;
  const initialReviewLoading = loading && reviewRequests.length === 0;
  usePageLoading(initialReviewLoading, 'moderation-dashboard');

  useEffect(() => {
    if (!directModerationState.hasDirectState) return;
    setActiveTabKey(directModerationState.activeTabKey || 'open');
    setActiveFilter(directModerationState.activeFilter || 'all');
    setExpandedId(directModerationState.expandedId || null);
  }, [directModerationState]);

  useEffect(() => {
    if (activeFilters.some((filter) => filter.key === activeFilter)) return;
    setActiveFilter('all');
  }, [activeFilter, activeFilters]);

  useEffect(() => {
    writeRouteSessionState('moderation', user, {
      activeTabKey,
      activeFilter,
      expandedId,
      loadedCount: reviewRequests.length,
      scrollY: typeof window !== 'undefined' ? window.scrollY : 0,
    });
  }, [activeFilter, activeTabKey, expandedId, reviewRequests.length, user]);

  useEffect(() => onRouteReset('moderation', () => {
    clearRouteSessionState('moderation', user);
    initialPageLimitRef.current = MODERATION_PAGE_SIZE;
    setActiveTabKey('open');
    setActiveFilter('all');
    setExpandedId(null);
    setDetailsById({});
    setResponsesById({});
    setFilterPanelOpen(false);
    setPendingDecision(null);
    setRequestChangesDialog(null);
    setPageInfo({
      count: 0,
      hasMore: false,
      nextOffset: null,
      limit: MODERATION_PAGE_SIZE,
      offset: 0,
    });
    if (location.search) {
      navigate('/moderation', { replace: true });
    }
    window.scrollTo({ top: 0, behavior: 'auto' });
  }), [location.search, navigate, user]);

  useEffect(() => {
    if (initialReviewLoading || directModerationState.hasDirectState || !storedModerationState.scrollY) return undefined;
    const restoreId = window.requestAnimationFrame(() => {
      window.scrollTo({ top: Number(storedModerationState.scrollY) || 0, behavior: 'auto' });
    });
    return () => window.cancelAnimationFrame(restoreId);
  }, [directModerationState.hasDirectState, initialReviewLoading, storedModerationState.scrollY]);

  useEffect(() => {
    const persistScroll = () => {
      writeRouteSessionState('moderation', user, {
        activeTabKey,
        activeFilter,
        expandedId,
        loadedCount: reviewRequests.length,
        scrollY: window.scrollY,
      });
    };
    window.addEventListener('pagehide', persistScroll);
    window.addEventListener('beforeunload', persistScroll);
    return () => {
      persistScroll();
      window.removeEventListener('pagehide', persistScroll);
      window.removeEventListener('beforeunload', persistScroll);
    };
  }, [activeFilter, activeTabKey, expandedId, reviewRequests.length, user]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearchQuery(String(searchQuery || '').trim());
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  const loadReviewPage = useCallback(async ({
    offset = 0,
    limit = MODERATION_PAGE_SIZE,
    replace = false,
    silent = false,
  } = {}) => {
    const sequence = requestSequenceRef.current + 1;
    requestSequenceRef.current = sequence;
    setError('');
    if (replace) {
      if (!silent) setLoading(true);
    } else {
      setLoadingMore(true);
    }
    try {
      const payload = await listModerationReviewRequests({
        tab: activeTabKey,
        filter: activeFilter,
        limit,
        offset,
        q: activeSearchQuery || undefined,
      });
      if (requestSequenceRef.current !== sequence) return;
      const page = normalizeReviewRequestPage(payload, { limit, offset });
      setReviewRequests((previous) => (
        replace ? page.results : appendUniqueModerationItems(previous, page.results)
      ));
      setPageInfo({
        count: page.count,
        hasMore: page.hasMore,
        nextOffset: page.nextOffset,
        limit: page.limit,
        offset: page.offset,
      });
    } catch (reviewError) {
      if (requestSequenceRef.current !== sequence) return;
      if (replace) setReviewRequests([]);
      setError(reviewError.message || 'Could not load moderation review requests.');
    } finally {
      if (requestSequenceRef.current === sequence) {
        if (replace) setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [activeFilter, activeSearchQuery, activeTabKey]);

  const loadReviewRequests = useCallback(async ({ silent = false } = {}) => {
    const initialLimit = initialPageLimitRef.current || MODERATION_PAGE_SIZE;
    initialPageLimitRef.current = MODERATION_PAGE_SIZE;
    await loadReviewPage({ offset: 0, limit: initialLimit, replace: true, silent });
  }, [loadReviewPage]);

  const refreshLoadedReviewRequests = useCallback(async ({ silent = false } = {}) => {
    const limit = Math.max(MODERATION_PAGE_SIZE, reviewRequests.length || 0);
    await loadReviewPage({ offset: 0, limit, replace: true, silent });
  }, [loadReviewPage, reviewRequests.length]);

  const handleLoadMore = useCallback(() => {
    if (loading || loadingMore || !pageInfo.hasMore) return;
    loadReviewPage({
      offset: pageInfo.nextOffset ?? reviewRequests.length,
      limit: MODERATION_PAGE_SIZE,
      replace: false,
    });
  }, [loadReviewPage, loading, loadingMore, pageInfo.hasMore, pageInfo.nextOffset, reviewRequests.length]);

  useEffect(() => {
    loadReviewRequests();
  }, [loadReviewRequests]);

  useEffect(() => {
    if (typeof IntersectionObserver === 'undefined') return undefined;
    const node = loadMoreRef.current;
    if (!node || !pageInfo.hasMore || loading || loadingMore) return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        handleLoadMore();
      }
    }, { rootMargin: '360px 0px' });
    observer.observe(node);
    return () => observer.disconnect();
  }, [handleLoadMore, loading, loadingMore, pageInfo.hasMore]);

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
    const itemKey = moderationItemKey(review);
    const nextExpanded = expandedId === itemKey ? null : itemKey;
    setExpandedId(nextExpanded);
    setError('');

    if (!nextExpanded || detailsById[itemKey]) return;
    if (review.source_type !== 'review_request') return;

    setDetailLoadingId(itemKey);
    try {
      const detail = await getModerationReviewRequest(review.id);
      setDetailsById((previous) => ({ ...previous, [itemKey]: detail }));
    } catch (detailError) {
      setError(detailError.message || 'Could not load moderation review request.');
    } finally {
      setDetailLoadingId(null);
    }
  };

  useEffect(() => {
    if (!expandedId || detailsById[expandedId] || detailLoadingId) return;
    const review = reviewRequests.find((item) => moderationItemKey(item) === expandedId);
    if (!review || review.source_type !== 'review_request') return;
    let active = true;
    setDetailLoadingId(expandedId);
    getModerationReviewRequest(review.id)
      .then((detail) => {
        if (!active) return;
        setDetailsById((previous) => ({ ...previous, [expandedId]: detail }));
      })
      .catch((detailError) => {
        if (!active) return;
        setError(detailError.message || 'Could not load moderation review request.');
      })
      .finally(() => {
        if (active) setDetailLoadingId(null);
      });
    return () => {
      active = false;
    };
  }, [detailLoadingId, detailsById, expandedId, reviewRequests]);

  const mergeReviewPayload = useCallback((payload) => {
    if (!payload?.id) return;
    const payloadKey = moderationItemKey(payload);
    setReviewRequests((previous) => previous.map((item) => (
      moderationItemKey(item) === payloadKey ? { ...item, ...payload } : item
    )));
    setDetailsById((previous) => ({
      ...previous,
      [payloadKey]: { ...(previous[payloadKey] || {}), ...payload },
    }));
    setResponsesById((previous) => ({ ...previous, [payloadKey]: String(payload.admin_response || '') }));
  }, []);

  const handleDecision = async (review, decision, detail = null) => {
    if (!review?.project_id) return;
    const itemKey = moderationItemKey(review);
    const key = `${decision}-${itemKey}`;
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
      await refreshLoadedReviewRequests({ silent: true });
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
    const itemKey = moderationItemKey(review);
    const key = `request_changes-${itemKey}`;
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
      await refreshLoadedReviewRequests({ silent: true });
    } catch (actionError) {
      setError(actionError.message || 'Could not update moderation state.');
    } finally {
      setActionBusy('');
    }
  };

  const handleDismissReport = async (review) => {
    const reportId = review?.report_id || String(review?.id || '').replace(/^report-/, '');
    if (!reportId) return;
    const key = `dismiss-${moderationItemKey(review)}`;
    setActionBusy(key);
    setError('');
    setNotice('');
    try {
      await runModerationReportAction(reportId, 'dismiss');
      setNotice('Report dismissed.');
      await refreshLoadedReviewRequests({ silent: true });
    } catch (actionError) {
      setError(actionError.message || 'Could not dismiss report.');
    } finally {
      setActionBusy('');
    }
  };

  const handleRescan = async (review, detail = null) => {
    if (!review?.project_id) return;
    const key = `rescan-${moderationItemKey(review)}`;
    setActionBusy(key);
    setError('');
    setNotice('');
    try {
      const response = draftAdminResponse(responsesById, review, detail);
      await runAdminProjectModerationAction(review.project_id, 'rescan', response, 'manual_admin_rescan');
      setNotice('Moderation rescan started.');
      await refreshLoadedReviewRequests({ silent: true });
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
            {showingCount} of {totalCount} {activeTab.countLabel}
          </span>
        </div>

        <div data-testid="moderation-filter-row" className="flex flex-wrap items-center gap-2">
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

          <div className="relative flex w-full sm:ml-auto sm:w-auto" ref={filterPanelRef}>
            <button
              type="button"
              onClick={() => setFilterPanelOpen((open) => !open)}
              className={`focus-ring inline-flex w-full items-center justify-center gap-2 rounded-full px-3 py-2 text-sm font-semibold transition sm:w-auto ${
                activeFilterCount
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-inverse)] shadow-sm'
                  : 'bg-[color:var(--surface-muted)] text-[var(--text-primary)] hover:bg-[color:var(--hover-surface-strong)]'
              }`}
              aria-expanded={filterPanelOpen}
              aria-label={`Filter moderation queue: ${activeFilterCount ? activeFilterLabel : 'All'}`}
              title={`Filter moderation queue: ${activeFilterCount ? activeFilterLabel : 'All'}`}
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
              <div className="absolute left-0 right-0 top-full z-20 mt-2 w-full rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-elevated)] p-3 shadow-xl sm:left-auto sm:right-0 sm:w-[min(64rem,calc(100vw-2rem))] sm:max-w-[calc(100vw-2rem)]">
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
        </div>

        {(activeSearchQuery || (!activeFilterCount && !activeSearchQuery)) && (
          <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-[var(--text-secondary)]">
            {activeSearchQuery && (
              <span className="max-w-full rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">
                Search: {activeSearchQuery}
              </span>
            )}
            {!activeFilterCount && !activeSearchQuery && (
              <span className="rounded-full bg-[color:var(--surface-muted)] px-2.5 py-1">
                No active search or filters
              </span>
            )}
          </div>
        )}

        {initialReviewLoading ? (
          <div className="space-y-3" aria-label="Loading review requests">
            {Array.from({ length: 3 }, (_, index) => (
              <div key={`moderation-loading-${index}`} className="rounded-2xl token-surface p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1 space-y-3">
                    <div className="visus-loading-sheen h-4 w-2/3 rounded-full bg-[color:var(--surface-container-high)]" />
                    <div className="visus-loading-sheen h-3 w-full rounded-full bg-[color:var(--surface-container-high)]" />
                    <div className="visus-loading-sheen h-3 w-4/5 rounded-full bg-[color:var(--surface-container-high)]" />
                  </div>
                  <div className="visus-loading-sheen h-8 w-24 shrink-0 rounded-full bg-[color:var(--surface-container-high)]" />
                </div>
              </div>
            ))}
          </div>
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
            {reviewRequests.map((review) => {
              const itemKey = moderationItemKey(review);
              const detail = detailsById[itemKey] || null;
              const expanded = expandedId === itemKey;
              const response = draftAdminResponse(responsesById, review, detail);
              const savedResponse = savedAdminResponse(review, detail);
              const responseChanged = response !== savedResponse;
              const approveBusy = actionBusy === `approve-${itemKey}`;
              const rejectBusy = actionBusy === `reject-${itemKey}`;
              const requestChangesBusy = actionBusy === `request_changes-${itemKey}`;
              const rescanBusy = actionBusy === `rescan-${itemKey}`;
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
              const reviewReturnPath = moderationReturnPath(location, {
                tab: activeTabKey,
                filter: activeFilter,
                itemKey,
              });
              const reviewStudioUrl = studioReviewUrl(review, reviewReturnPath);

              return (
                <article key={itemKey} className="rounded-2xl token-surface p-4">
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
                      to={reviewStudioUrl}
                      className="focus-ring inline-flex h-9 items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-3 text-sm font-medium text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)]"
                    >
                      <ExternalLink size={14} />
                      <span>Open in read-only Studio</span>
                    </Link>
                    <Button size="sm" variant="secondary" onClick={() => handleToggleDetail(review)} disabled={detailLoadingId === itemKey}>
                      <ChevronDown size={14} className={expanded ? 'rotate-180 transition' : 'transition'} />
                      <span>{expanded ? 'Hide details' : 'View details'}</span>
                    </Button>
                  </div>

                  {expanded && (
                    <div className="mt-4 space-y-3 rounded-xl border border-[var(--border-subtle)] p-3">
                      {detailLoadingId === itemKey ? (
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
                            [itemKey]: event.target.value,
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
                              <span>{actionBusy === `dismiss-${itemKey}` ? 'Dismissing...' : 'Dismiss report'}</span>
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
            <div ref={loadMoreRef} className="h-1" aria-hidden="true" />
            {pageInfo.hasMore && (
              <div className="flex justify-center pt-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={handleLoadMore}
                  disabled={loadingMore || loading}
                >
                  <ChevronDown size={14} />
                  <span>{loadingMore ? 'Loading...' : 'Load more'}</span>
                </Button>
              </div>
            )}
            {loadingMore && !pageInfo.hasMore && (
              <p className="text-center text-sm text-[var(--text-secondary)]">Loading more review requests...</p>
            )}
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
                onClick={() => handleDecision(
                  pendingDecision.review,
                  pendingDecision.decision,
                  detailsById[moderationItemKey(pendingDecision.review)] || null,
                )}
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
