import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Bell, CheckCheck, Search } from 'lucide-react';
import {
  fetchNotificationUnreadCount,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '../../api';
import {
  formatNotificationTime,
  isSafeNotificationActionUrl,
  NOTIFICATIONS_CHANGED_EVENT,
  notifyNotificationsChanged,
  notificationResults,
} from '../../utils/notifications';
import ProfileMenu from './ProfileMenu';

const SEARCH_HIDDEN_PATHS = new Set(['/help', '/settings', '/analytics', '/notifications']);
const NOTIFICATION_DROPDOWN_LIMIT = 5;

export default function Header({
  searchQuery,
  onSearchQueryChange,
  user,
  authLoading,
  onLoginRequest,
  onLogout,
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const showSearch = !SEARCH_HIDDEN_PATHS.has(location.pathname);
  const isAuthenticated = Boolean(user) && !authLoading;
  const notificationRef = useRef(null);
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationsError, setNotificationsError] = useState('');
  const [notificationFilter, setNotificationFilter] = useState('all');
  const [unreadCount, setUnreadCount] = useState(0);
  const [markAllLoading, setMarkAllLoading] = useState(false);

  useEffect(() => {
    if (!isAuthenticated) {
      setUnreadCount(0);
      setNotifications([]);
      setNotificationsOpen(false);
      return undefined;
    }

    let cancelled = false;
    const loadCount = async () => {
      try {
        const data = await fetchNotificationUnreadCount();
        if (!cancelled) {
          setUnreadCount(Number(data?.unread_count || 0));
        }
      } catch (error) {
        if (!cancelled) {
          setUnreadCount(0);
        }
      }
    };

    loadCount();
    const handleNotificationsChanged = () => {
      loadCount();
    };
    window.addEventListener(NOTIFICATIONS_CHANGED_EVENT, handleNotificationsChanged);
    const intervalId = window.setInterval(loadCount, 60000);
    return () => {
      cancelled = true;
      window.removeEventListener(NOTIFICATIONS_CHANGED_EVENT, handleNotificationsChanged);
      window.clearInterval(intervalId);
    };
  }, [isAuthenticated, user?.id]);

  useEffect(() => {
    if (!notificationsOpen || !isAuthenticated) return undefined;

    let cancelled = false;
    setNotificationsLoading(true);
    setNotificationsError('');
    fetchNotifications({
      limit: NOTIFICATION_DROPDOWN_LIMIT,
      unreadOnly: notificationFilter === 'unread',
    })
      .then((data) => {
        if (!cancelled) setNotifications(notificationResults(data));
      })
      .catch((error) => {
        if (!cancelled) setNotificationsError(error?.message || 'Failed to load notifications');
      })
      .finally(() => {
        if (!cancelled) setNotificationsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [notificationsOpen, isAuthenticated, notificationFilter, user?.id]);

  useEffect(() => {
    setNotificationsOpen(false);
  }, [location.pathname, location.search]);

  useEffect(() => {
    if (!notificationsOpen) return undefined;
    const handlePointerDown = (event) => {
      if (notificationRef.current && !notificationRef.current.contains(event.target)) {
        setNotificationsOpen(false);
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [notificationsOpen]);

  const handleNotificationClick = async (notification) => {
    const wasUnread = !notification.is_read;
    try {
      await markNotificationRead(notification.id);
      setNotifications((items) => (
        notificationFilter === 'unread'
          ? items.filter((item) => item.id !== notification.id)
          : items.map((item) => (item.id === notification.id ? { ...item, is_read: true } : item))
      ));
      if (wasUnread) {
        setUnreadCount((count) => Math.max(0, count - 1));
        notifyNotificationsChanged();
      }
    } catch (error) {
      setNotificationsError(error?.message || 'Failed to update notification');
      return;
    }

    const actionUrl = String(notification.action_url || '').trim();
    if (isSafeNotificationActionUrl(actionUrl)) {
      setNotificationsOpen(false);
      navigate(actionUrl);
    }
  };

  const handleMarkAllRead = async () => {
    setMarkAllLoading(true);
    setNotificationsError('');
    try {
      await markAllNotificationsRead();
      setUnreadCount(0);
      setNotifications((items) => (
        notificationFilter === 'unread'
          ? []
          : items.map((item) => ({ ...item, is_read: true }))
      ));
      notifyNotificationsChanged();
    } catch (error) {
      setNotificationsError(error?.message || 'Failed to update notifications');
    } finally {
      setMarkAllLoading(false);
    }
  };

  return (
    <>
      <header className="fixed top-0 z-50 w-full overflow-visible">
        <div className="relative flex h-16 w-full items-center bg-[color:rgba(255,255,255,0.82)] px-3 backdrop-blur-3xl dark:bg-[color:rgba(17,19,23,0.8)] sm:px-5">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <Link
              to="/"
              className="focus-ring inline-flex shrink-0 items-center"
              aria-label="VISUS VidLab home"
            >
              <span className="font-['Manrope'] text-[1.3rem] font-extrabold tracking-[-0.045em] text-[var(--text-primary)] sm:text-[1.45rem]">
                VISUS VidLab
              </span>
            </Link>

            {showSearch && (
              <label className="focus-ring hidden h-10 min-w-0 flex-1 items-center gap-2 rounded-full border border-[color:var(--border-subtle)] bg-[var(--surface-container-low)] px-3 md:flex md:max-w-2xl">
                <Search size={16} className="text-[var(--outline)]" />
                <input
                  value={searchQuery}
                  onChange={(event) => onSearchQueryChange(event.target.value)}
                  type="search"
                  placeholder="Search lessons, teachers, and topics"
                  className="h-full w-full border-0 bg-transparent text-sm text-[var(--text-primary)] placeholder:text-[var(--outline)] focus:outline-none"
                  aria-label="Global search"
                />
              </label>
            )}
          </div>

          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            {isAuthenticated && (
              <div ref={notificationRef} className="relative hidden md:block">
                <button
                  type="button"
                  className="focus-ring relative inline-flex h-10 w-10 items-center justify-center rounded-full text-[#9ca3af] transition hover:bg-[color:var(--hover-accent-soft)] hover:text-[var(--text-primary)]"
                  aria-label="Notifications"
                  aria-expanded={notificationsOpen}
                  onClick={() => setNotificationsOpen((open) => !open)}
                >
                  <Bell size={16} />
                  {unreadCount > 0 && (
                    <span className="absolute right-1.5 top-1.5 flex min-w-[1.1rem] items-center justify-center rounded-full bg-[var(--accent-primary)] px-1 text-[0.65rem] font-bold leading-[1.1rem] text-white">
                      {unreadCount > 99 ? '99+' : unreadCount}
                    </span>
                  )}
                </button>

                {notificationsOpen && (
                  <div className="absolute right-0 top-12 z-[60] w-[22rem] overflow-hidden rounded-lg border border-[color:var(--border-subtle)] bg-[var(--surface-container-high)] text-[var(--text-primary)] shadow-2xl">
                    <div className="flex items-center justify-between border-b border-[color:var(--border-subtle)] px-4 py-3">
                      <div>
                        <p className="text-sm font-semibold">Notifications</p>
                        <p className="text-xs text-[var(--outline)]">{unreadCount} unread</p>
                      </div>
                      <button
                        type="button"
                        className="focus-ring inline-flex h-8 items-center gap-1.5 rounded-full px-2 text-xs font-semibold text-[var(--text-secondary)] transition hover:bg-[color:var(--hover-accent-soft)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
                        onClick={handleMarkAllRead}
                        disabled={markAllLoading || unreadCount === 0}
                      >
                        <CheckCheck size={14} />
                        Mark all read
                      </button>
                    </div>

                    <div className="flex items-center gap-1 border-b border-[color:var(--border-subtle)] px-3 py-2">
                      {['all', 'unread'].map((filter) => {
                        const selected = notificationFilter === filter;
                        return (
                          <button
                            key={filter}
                            type="button"
                            onClick={() => setNotificationFilter(filter)}
                            className={`focus-ring h-8 rounded-full px-3 text-xs font-semibold capitalize transition ${
                              selected
                                ? 'bg-[color:rgba(107,56,212,0.12)] text-[var(--text-primary)] dark:bg-[color:rgba(208,188,255,0.2)]'
                                : 'text-[var(--text-secondary)] hover:bg-[color:var(--hover-accent-soft)] hover:text-[var(--text-primary)]'
                            }`}
                          >
                            {filter}
                          </button>
                        );
                      })}
                    </div>

                    <div className="max-h-[26rem] overflow-y-auto">
                      {notificationsLoading && (
                        <div className="px-4 py-6 text-sm text-[var(--text-secondary)]">
                          <p className="font-semibold text-[var(--text-primary)]">Loading notifications</p>
                          <p className="mt-1 text-xs">Checking the latest activity.</p>
                        </div>
                      )}

                      {!notificationsLoading && notificationsError && (
                        <div className="px-4 py-6 text-sm text-red-600">
                          <p className="font-semibold">Unable to load notifications</p>
                          <p className="mt-1 text-xs">{notificationsError}</p>
                        </div>
                      )}

                      {!notificationsLoading && !notificationsError && notifications.length === 0 && (
                        <div className="px-4 py-7 text-sm text-[var(--text-secondary)]">
                          <p className="font-semibold text-[var(--text-primary)]">
                            {notificationFilter === 'unread' ? 'No unread notifications' : 'No notifications yet'}
                          </p>
                          <p className="mt-1 text-xs">
                            {notificationFilter === 'unread'
                              ? 'Everything in this view has been read.'
                              : 'Comments, followed publisher posts, and render updates will appear here.'}
                          </p>
                        </div>
                      )}

                      {!notificationsLoading && !notificationsError && notifications.map((notification) => {
                        const unread = !notification.is_read;
                        return (
                          <button
                            key={notification.id}
                            type="button"
                            className={`focus-ring block w-full border-b border-[color:var(--border-subtle)] px-4 py-3 text-left transition last:border-b-0 hover:bg-[color:var(--hover-accent-soft)] ${unread ? 'bg-[color:var(--surface-container-low)]' : ''}`}
                            onClick={() => handleNotificationClick(notification)}
                          >
                            <div className="flex items-start gap-2">
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-sm font-semibold text-[var(--text-primary)]">
                                  {notification.title}
                                </p>
                                {notification.body && (
                                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--text-secondary)]">
                                    {notification.body}
                                  </p>
                                )}
                                <div className="mt-2 flex items-center gap-2 text-[0.7rem] font-medium text-[var(--outline)]">
                                  <span>{formatNotificationTime(notification.created_at)}</span>
                                  {notification.action_url && <span>Open</span>}
                                </div>
                              </div>
                              {unread && <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-[var(--accent-primary)]" />}
                            </div>
                          </button>
                        );
                      })}
                    </div>

                    <div className="border-t border-[color:var(--border-subtle)] p-2">
                      <Link
                        to="/notifications"
                        onClick={() => setNotificationsOpen(false)}
                        className="focus-ring flex h-9 items-center justify-center rounded-full text-sm font-semibold text-[var(--text-primary)] transition hover:bg-[color:var(--hover-accent-soft)]"
                      >
                        View all notifications
                      </Link>
                    </div>
                  </div>
                )}
              </div>
            )}

            <ProfileMenu
              user={user}
              authLoading={authLoading}
              onLoginRequest={onLoginRequest}
              onLogout={onLogout}
            />
          </div>
        </div>
      </header>

      {/* Spacer */}
      <div className="h-16 w-full" />
    </>
  );
}
