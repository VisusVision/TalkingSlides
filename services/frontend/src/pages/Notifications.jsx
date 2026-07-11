import { useCallback, useEffect, useState } from 'react';
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
import {
  formatNotificationTime,
  isSafeNotificationActionUrl,
  notifyNotificationsChanged,
  notificationPageInfo,
} from '../utils/notifications';
import NotificationTypeIcon from '../components/ui/NotificationTypeIcon';

const PAGE_SIZE = 20;
const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'unread', label: 'Unread' },
];

function NotificationRow({ notification, onOpen, onMarkRead }) {
  const unread = !notification.is_read;
  const actionUrl = String(notification.action_url || '').trim();

  return (
    <article
      className={`min-w-0 overflow-hidden rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-container-low)] p-3 transition sm:p-4 ${
        unread ? 'border-[color:rgba(107,56,212,0.35)] bg-[color:rgba(107,56,212,0.06)] dark:bg-[color:rgba(208,188,255,0.09)]' : ''
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
              className="focus-ring mt-3 inline-flex h-8 items-center gap-1.5 rounded-full bg-[var(--surface-container-high)] px-3 text-xs font-semibold text-[var(--text-secondary)] transition hover:bg-[color:var(--hover-accent-soft)] hover:text-[var(--text-primary)]"
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

  return (
    <PageContainer width="standard" className="overflow-x-hidden">
      <PageHeader
        eyebrow="Notifications"
        title="Notification center"
        description="Review comments, followed publisher updates, and render status changes."
        actions={(
          <button
            type="button"
            onClick={handleMarkAllRead}
            disabled={markAllLoading || unreadCount === 0}
            className="focus-ring inline-flex h-10 w-full items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-4 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)] disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          >
            <CheckCheck size={16} />
            <span>{markAllLoading ? 'Updating...' : 'Mark all read'}</span>
          </button>
        )}
      />

      <SurfaceCard className="min-w-0 space-y-4 overflow-hidden">
        <PageToolbar surface={false} className="border-b border-[color:var(--border-subtle)] pb-4" aria-label="Notification filters">
          <div className="inline-flex w-fit rounded-full bg-[var(--surface-container-high)] p-1">
            {FILTERS.map((option) => {
              const selected = filter === option.id;
              return (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setFilter(option.id)}
                  className={`focus-ring inline-flex h-9 items-center gap-2 rounded-full px-4 text-sm font-semibold transition ${
                    selected
                      ? 'bg-[color:rgba(107,56,212,0.12)] text-[var(--text-primary)] dark:bg-[color:rgba(208,188,255,0.2)]'
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
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-200">
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
              className="focus-ring inline-flex h-10 items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-4 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)] disabled:cursor-not-allowed disabled:opacity-50"
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
