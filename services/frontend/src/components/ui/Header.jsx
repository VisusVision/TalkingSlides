import { useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Bell, CheckCheck, Search } from 'lucide-react';
import {
  fetchNotificationUnreadCount,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from '../../api';
import ProfileMenu from './ProfileMenu';

const SEARCH_HIDDEN_PATHS = new Set(['/help', '/settings', '/analytics']);

function notificationResults(payload) {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload?.results)) return payload.results;
  return [];
}

function formatNotificationTime(value) {
  if (!value) return '';
  const created = new Date(value);
  if (Number.isNaN(created.getTime())) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - created.getTime()) / 1000));
  if (seconds < 60) return 'Just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return created.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

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
    const intervalId = window.setInterval(loadCount, 60000);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [isAuthenticated, user?.id]);

  useEffect(() => {
    if (!notificationsOpen || !isAuthenticated) return undefined;

    let cancelled = false;
    setNotificationsLoading(true);
    setNotificationsError('');
    fetchNotifications({ limit: 20, unreadOnly: false })
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
  }, [notificationsOpen, isAuthenticated, user?.id]);

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
        items.map((item) => (item.id === notification.id ? { ...item, is_read: true } : item))
      ));
      if (wasUnread) {
        setUnreadCount((count) => Math.max(0, count - 1));
      }
    } catch (error) {
      setNotificationsError(error?.message || 'Failed to update notification');
      return;
    }

    const actionUrl = String(notification.action_url || '').trim();
    if (actionUrl.startsWith('/') && !actionUrl.startsWith('//')) {
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
      setNotifications((items) => items.map((item) => ({ ...item, is_read: true })));
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

                    <div className="max-h-[26rem] overflow-y-auto">
                      {notificationsLoading && (
                        <div className="px-4 py-5 text-sm text-[var(--text-secondary)]">
                          Loading notifications...
                        </div>
                      )}

                      {!notificationsLoading && notificationsError && (
                        <div className="px-4 py-5 text-sm text-red-600">
                          {notificationsError}
                        </div>
                      )}

                      {!notificationsLoading && !notificationsError && notifications.length === 0 && (
                        <div className="px-4 py-5 text-sm text-[var(--text-secondary)]">
                          No notifications yet.
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
