import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bell, Check, CheckCheck, Inbox, Loader2 } from 'lucide-react';
import {
  fetchNotificationUnreadCount,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '../api';
import SurfaceCard from '../components/ui/SurfaceCard';
import EmptyState from '../components/ui/EmptyState';
import { PageContainer, PageHeader, PageToolbar } from '../components/ui/PageLayout';
import ProductGuidance from '../components/guidance/ProductGuidance';
import { useProductGuidanceCopy } from '../components/guidance/productGuidanceCopy';
import {
  formatNotificationTime,
  isSafeNotificationActionUrl,
  notifyNotificationsChanged,
  notificationPageInfo,
} from '../utils/notifications';
import NotificationTypeIcon from '../components/ui/NotificationTypeIcon';

const PAGE_SIZE = 20;
const FAILURE_EVENT_TYPES = new Set([
  'publisher_lesson_render_failed',
  'publisher_avatar_render_failed',
]);
const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'unread', label: 'Unread' },
];

function NotificationRow({ notification, onOpen, onMarkRead }) {
  const unread = !notification.is_read;
  const actionUrl = String(notification.action_url || '').trim();

  return (
    <article
      className={`motion-interactive min-w-0 overflow-hidden rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-container-low)] p-3 sm:p-4 ${
        unread ? 'border-[color:color-mix(in_srgb,var(--accent-primary),transparent_52%)] bg-[color:var(--hover-accent-soft)]' : ''
      }`}
    >
      <div className="flex gap-3">
        <span className="relative mt-1 shrink-0">
          <NotificationTypeIcon eventType={notification.event_type} />
          {unread && (
            <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-[var(--accent-primary)] ring-2 ring-[color:var(--surface-container-low)]" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => onOpen(notification)}
            className="focus-ring block w-full min-w-0 rounded-md text-left"
          >
            <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between sm:gap-2">
              <h2 className="min-w-0 break-words text-sm font-semibold text-[var(--text-primary)] sm:text-base">
                {notification.title}
              </h2>
              <span className="shrink-0 text-xs font-medium text-[var(--outline)]">
                {formatNotificationTime(notification.created_at)}
              </span>
            </div>
            {notification.body && (
              <p className="mt-1 break-words text-sm leading-6 text-[var(--text-secondary)]">
                {notification.body}
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs font-semibold text-[var(--outline)]">
              {unread ? <span>Unread</span> : <span>Read</span>}
              {actionUrl && <span>Open destination</span>}
            </div>
          </button>

          {unread && (
            <button
              type="button"
              onClick={() => onMarkRead(notification)}
              className="focus-ring motion-interactive mt-3 inline-flex h-8 items-center gap-1.5 rounded-full bg-[var(--surface-container-high)] px-3 text-xs font-semibold text-[var(--text-secondary)] hover:bg-[color:var(--hover-accent-soft)] hover:text-[var(--text-primary)]"
            >
              <Check size={14} />
              Mark read
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

export default function Notifications({ user }) {
  const navigate = useNavigate();
  const guidanceCopy = useProductGuidanceCopy();
  const [filter, setFilter] = useState('all');
  const [notifications, setNotifications] = useState([]);
  const [pageInfo, setPageInfo] = useState({ count: 0, hasMore: false, nextOffset: null });
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [markAllLoading, setMarkAllLoading] = useState(false);

  const unreadOnly = filter === 'unread';

  const refreshUnreadCount = useCallback(async () => {
    try {
      const data = await fetchNotificationUnreadCount();
      setUnreadCount(Number(data?.unread_count || 0));
    } catch {
      setUnreadCount(0);
    }
  }, []);

  const loadNotifications = useCallback(async ({ reset = false } = {}) => {
    const nextOffset = reset ? 0 : pageInfo.nextOffset;
    if (!reset && nextOffset === null) return;

    setError('');
    if (reset) {
      setLoading(true);
    } else {
      setLoadingMore(true);
    }

    try {
      const payload = await fetchNotifications({
        limit: PAGE_SIZE,
        offset: nextOffset || 0,
        unreadOnly,
      });
      const info = notificationPageInfo(payload, {
        limit: PAGE_SIZE,
        offset: nextOffset || 0,
      });
      setPageInfo(info);
      setNotifications((current) => (reset ? info.results : [...current, ...info.results]));
    } catch (loadError) {
      setError(loadError?.message || 'Failed to load notifications');
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [pageInfo.nextOffset, unreadOnly]);

  useEffect(() => {
    refreshUnreadCount();
  }, [refreshUnreadCount, user?.id]);

  useEffect(() => {
    setNotifications([]);
    setPageInfo({ count: 0, hasMore: false, nextOffset: null });
    loadNotifications({ reset: true });
  }, [filter, user?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const updateAfterRead = (notification) => {
    const wasUnread = !notification.is_read;
    setNotifications((items) => {
      if (unreadOnly) {
        return items.filter((item) => item.id !== notification.id);
      }
      return items.map((item) => (
        item.id === notification.id ? { ...item, is_read: true } : item
      ));
    });
    if (wasUnread) {
      setUnreadCount((count) => Math.max(0, count - 1));
      notifyNotificationsChanged();
    }
  };

  const handleMarkRead = async (notification) => {
    if (!notification || notification.is_read) return true;
    try {
      await markNotificationRead(notification.id);
      updateAfterRead(notification);
      return true;
    } catch (readError) {
      setError(readError?.message || 'Failed to update notification');
      return false;
    }
  };

  const handleOpenNotification = async (notification) => {
    const ok = await handleMarkRead(notification);
    if (!ok) return;
    const actionUrl = String(notification.action_url || '').trim();
    if (isSafeNotificationActionUrl(actionUrl)) {
      navigate(actionUrl);
    }
  };

  const handleMarkAllRead = async () => {
    setMarkAllLoading(true);
    setError('');
    try {
      await markAllNotificationsRead();
      setUnreadCount(0);
      setNotifications((items) => (
        unreadOnly ? [] : items.map((item) => ({ ...item, is_read: true }))
      ));
      setPageInfo((current) => ({
        ...current,
        count: unreadOnly ? 0 : current.count,
        hasMore: unreadOnly ? false : current.hasMore,
        nextOffset: unreadOnly ? null : current.nextOffset,
      }));
      notifyNotificationsChanged();
    } catch (markError) {
      setError(markError?.message || 'Failed to mark notifications read');
    } finally {
      setMarkAllLoading(false);
    }
  };

  const emptyTitle = unreadOnly ? 'No unread notifications' : 'No notifications yet';
  const emptyBody = unreadOnly
    ? 'Everything visible here has been read.'
    : 'New lesson activity, comments, and render updates will appear here.';
  const EmptyIcon = unreadOnly ? CheckCheck : Inbox;
  const notificationsGuidance = useMemo(() => {
    const unreadNotifications = notifications.filter((notification) => !notification.is_read);
    const failedUnread = unreadNotifications.find((notification) => FAILURE_EVENT_TYPES.has(String(notification.event_type || '')));
    const latestUnread = failedUnread || unreadNotifications[0] || null;

    if (failedUnread) {
      return {
        status: 'failed',
        title: guidanceCopy.notificationsFailedTitle,
        description: guidanceCopy.notificationsFailedDescription,
        primaryAction: {
          label: guidanceCopy.notificationsOpenAction,
          onClick: () => handleOpenNotification(failedUnread),
        },
        items: [{
          key: 'failed-unread-' + failedUnread.id,
          status: 'failed',
          title: guidanceCopy.notificationsFailureItemTitle,
          description: guidanceCopy.notificationsFailedDescription,
        }],
      };
    }

    if (unreadCount > 0) {
      return {
        status: 'needs-attention',
        title: guidanceCopy.notificationsUnreadTitle,
        description: guidanceCopy.notificationsUnreadDescription,
        primaryAction: latestUnread
          ? {
              label: guidanceCopy.notificationsOpenAction,
              onClick: () => handleOpenNotification(latestUnread),
            }
          : null,
        items: latestUnread
          ? [{
              key: 'unread-' + latestUnread.id,
              status: 'needs-attention',
              title: guidanceCopy.notificationsUnreadItemTitle,
              description: guidanceCopy.notificationsUnreadDescription,
            }]
          : [],
      };
    }

    if (pageInfo.count > 0) {
      return {
        status: 'completed',
        title: guidanceCopy.notificationsCaughtUpTitle,
        description: guidanceCopy.notificationsCaughtUpDescription,
        primaryAction: null,
        items: [],
      };
    }

    return {
      status: 'neutral',
      title: guidanceCopy.notificationsEmptyTitle,
      description: guidanceCopy.notificationsEmptyDescription,
      primaryAction: null,
      items: [],
    };
  }, [guidanceCopy, handleOpenNotification, notifications, pageInfo.count, unreadCount]);

  return (
    <PageContainer width="standard" motion="enter" className="overflow-x-hidden">
      <PageHeader
        eyebrow="Notifications"
        title="Notification center"
        description="Review comments, followed publisher updates, and render status changes."
        motion="fade"
        actions={(
          <button
            type="button"
            onClick={handleMarkAllRead}
            disabled={markAllLoading || unreadCount === 0}
            className="focus-ring motion-interactive inline-flex h-10 w-full items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-4 text-sm font-semibold text-[var(--text-primary)] hover:bg-[color:var(--hover-surface-strong)] disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          >
            <CheckCheck size={16} />
            <span>{markAllLoading ? 'Updating...' : 'Mark all read'}</span>
          </button>
        )}
      />


      {!loading && !error && (
        <ProductGuidance
          status={notificationsGuidance.status}
          eyebrow={guidanceCopy.notificationsEyebrow}
          title={notificationsGuidance.title}
          description={notificationsGuidance.description}
          primaryAction={notificationsGuidance.primaryAction}
          items={notificationsGuidance.items}
        />
      )}
      <SurfaceCard className="min-w-0 space-y-4 overflow-hidden">
        <PageToolbar surface={false} motion="fade" className="border-b border-[color:var(--border-subtle)] pb-4" aria-label="Notification filters">
          <div className="inline-flex w-fit rounded-full bg-[var(--surface-container-high)] p-1">
            {FILTERS.map((option) => {
              const selected = filter === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setFilter(option.id)}
                  className={`focus-ring motion-interactive inline-flex h-9 items-center gap-2 rounded-full px-4 text-sm font-semibold active:scale-[0.98] ${
                    selected
                      ? 'bg-[color:var(--hover-accent-soft)] text-[var(--accent-primary)]'
                      : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  {option.label}
                  {option.id === 'unread' && unreadCount > 0 && (
                    <span className="rounded-full bg-[var(--accent-primary)] px-1.5 text-[0.68rem] leading-5 text-white">
                      {unreadCount > 99 ? '99+' : unreadCount}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <p className="text-sm text-[var(--text-secondary)]">
            {pageInfo.count} {pageInfo.count === 1 ? 'notification' : 'notifications'}
          </p>
        </PageToolbar>

        {error && (
          <div className="rounded-lg border border-[color:color-mix(in_srgb,var(--feedback-danger-fg),transparent_65%)] bg-[color:var(--feedback-danger-bg)] px-4 py-3 text-sm text-[color:var(--feedback-danger-fg)]">
            {error}
          </div>
        )}

        {loading && (
          <div className="flex items-center gap-2 rounded-lg bg-[var(--surface-container-low)] px-4 py-6 text-sm text-[var(--text-secondary)]">
            <Loader2 size={16} className="animate-spin" />
            Loading notifications...
          </div>
        )}

        {!loading && notifications.length === 0 && !error && (
          <EmptyState
            icon={EmptyIcon}
            title={emptyTitle}
            description={emptyBody}
            className="rounded-lg bg-[var(--surface-container-low)]"
          />
        )}

        {!loading && notifications.length > 0 && (
          <div className="space-y-3">
            {notifications.map((notification) => (
              <NotificationRow
                key={notification.id}
                notification={notification}
                onOpen={handleOpenNotification}
                onMarkRead={handleMarkRead}
              />
            ))}
          </div>
        )}

        {!loading && pageInfo.hasMore && (
          <div className="flex justify-center pt-2">
            <button
              type="button"
              onClick={() => loadNotifications({ reset: false })}
              disabled={loadingMore}
              className="focus-ring motion-interactive inline-flex h-10 items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-4 text-sm font-semibold text-[var(--text-primary)] hover:bg-[color:var(--hover-surface-strong)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loadingMore ? <Loader2 size={16} className="animate-spin" /> : <Bell size={16} />}
              <span>{loadingMore ? 'Loading...' : 'Load more'}</span>
            </button>
          </div>
        )}
      </SurfaceCard>
    </PageContainer>
  );
}
