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
import { useI18n } from '../i18n/I18nProvider';
import {
  isSafeNotificationActionUrl,
  notifyNotificationsChanged,
  notificationPageInfo,
} from '../utils/notifications';
import NotificationTypeIcon from '../components/ui/NotificationTypeIcon';

const PAGE_SIZE = 20;
const FILTERS = [
  { id: 'all', labelKey: 'notifications.allFilter' },
  { id: 'unread', labelKey: 'notifications.unreadFilterLabel' },
];

function NotificationRow({ notification, onOpen, onMarkRead }) {
  const { t, formatDateTime } = useI18n();
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
                {formatDateTime(notification.created_at)}
              </span>
            </div>
            {notification.body && (
              <p className="mt-1 break-words text-sm leading-6 text-[var(--text-secondary)]">
                {notification.body}
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs font-semibold text-[var(--outline)]">
              {unread ? <span>{t('common.unreadStatus')}</span> : <span>{t('common.read')}</span>}
              {actionUrl && <span>{t('common.openDestination')}</span>}
            </div>
          </button>

          {unread && (
            <button
              type="button"
              onClick={() => onMarkRead(notification)}
              className="focus-ring mt-3 inline-flex h-8 items-center gap-1.5 rounded-full bg-[var(--surface-container-high)] px-3 text-xs font-semibold text-[var(--text-secondary)] transition hover:bg-[color:var(--hover-accent-soft)] hover:text-[var(--text-primary)]"
            >
              <Check size={14} />
              {t('common.markRead')}
            </button>
          )}
        </div>
      </div>
    </article>
  );
}

export default function Notifications({ user }) {
  const { t, formatNumber } = useI18n();
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
      setError(loadError?.message || t('notifications.loadError'));
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [pageInfo.nextOffset, t, unreadOnly]);

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
      setError(readError?.message || t('notifications.updateOneError'));
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
      setError(markError?.message || t('notifications.markAllError'));
    } finally {
      setMarkAllLoading(false);
    }
  };

  const emptyTitle = unreadOnly ? t('notifications.noneUnread') : t('notifications.none');
  const emptyBody = unreadOnly
    ? t('notifications.noneUnreadPageBody')
    : t('notifications.nonePageBody');

  return (
    <div className="mx-auto flex w-[calc(100vw-3rem)] max-w-5xl min-w-0 flex-col gap-5 overflow-x-hidden px-3 pb-8 sm:w-full sm:px-5 lg:px-6">
      <section className="min-w-0 rounded-none bg-transparent py-2">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <p className="label-sm">{t('notifications.title')}</p>
            <h1 className="display-lg mt-2 text-[var(--text-primary)]">{t('notifications.center')}</h1>
            <p className="body-md mt-2 max-w-[19rem] sm:max-w-2xl">
              {t('notifications.centerBody')}
            </p>
          </div>
          <button
            type="button"
            onClick={handleMarkAllRead}
            disabled={markAllLoading || unreadCount === 0}
            className="focus-ring inline-flex h-10 w-full items-center justify-center gap-2 rounded-full bg-[var(--surface-container-highest)] px-4 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-[color:var(--hover-surface-strong)] disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
          >
            <CheckCheck size={16} />
            <span>{markAllLoading ? t('common.updating') : t('notifications.markAllRead')}</span>
          </button>
        </div>
      </section>

      <SurfaceCard className="min-w-0 space-y-4 overflow-hidden">
        <div className="flex flex-col gap-3 border-b border-[color:var(--border-subtle)] pb-4 sm:flex-row sm:items-center sm:justify-between">
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
                  {t(option.labelKey)}
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
            {t('notifications.count', {
              count: formatNumber(pageInfo.count),
              plural: pageInfo.count === 1 ? '' : 's',
            })}
          </p>
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-200">
            {error}
          </div>
        )}

        {loading && (
          <div className="flex items-center gap-2 rounded-lg bg-[var(--surface-container-low)] px-4 py-6 text-sm text-[var(--text-secondary)]">
            <Loader2 size={16} className="animate-spin" />
            {t('notifications.loadingTitle')}...
          </div>
        )}

        {!loading && notifications.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center rounded-lg bg-[var(--surface-container-low)] px-4 py-12 text-center">
            <span className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-[var(--surface-container-high)] text-[var(--text-secondary)]">
              {unreadOnly ? <CheckCheck size={22} /> : <Inbox size={22} />}
            </span>
            <h2 className="mt-4 text-base font-semibold text-[var(--text-primary)]">{emptyTitle}</h2>
            <p className="mt-2 max-w-sm text-sm leading-6 text-[var(--text-secondary)]">{emptyBody}</p>
          </div>
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
              <span>{loadingMore ? `${t('common.loading')}...` : t('common.loadMore')}</span>
            </button>
          </div>
        )}
      </SurfaceCard>
    </div>
  );
}
