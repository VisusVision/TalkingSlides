import { useEffect, useRef, useState } from 'react';
import { CircleHelp, LayoutDashboard, LogIn, LogOut, Settings as SettingsIcon, UserCircle2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { API_BASE_URL, fetchAuthenticatedMediaBlobUrl } from '../../api';
import Button from './Button';

function displayNameFromUser(user) {
  const firstName = String(user?.first_name || '').trim();
  const lastName = String(user?.last_name || '').trim();
  const fullName = [firstName, lastName].filter(Boolean).join(' ').trim();
  if (fullName) return fullName;

  const username = String(user?.username || '').trim();
  if (username) return username;

  return 'VISUS User';
}

function initialsFromUser(user) {
  const name = displayNameFromUser(user);
  if (!name) return 'VV';
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || '')
    .join('');
}

function toAbsoluteMediaLikeUrl(url) {
  if (!url) return '';
  if (/^(https?:|data:|blob:)/i.test(url)) return url;
  const origin = API_BASE_URL.replace(/\/api\/v1\/?$/, '');
  return `${origin}${url.startsWith('/') ? url : `/${url}`}`;
}

function userRoleLabel(user) {
  const roleRaw = String(user?.profile?.role || user?.role || '').trim().toLowerCase();
  if (!roleRaw) return 'Student';
  if (roleRaw === 'teacher' || roleRaw === 'publisher') return 'Publisher';
  if (roleRaw === 'student') return 'Student';
  if (roleRaw === 'admin') return 'Admin';
  return roleRaw.charAt(0).toUpperCase() + roleRaw.slice(1);
}

export default function ProfileMenu({ user, authLoading, onLoginRequest, onLogout }) {
  const [open, setOpen] = useState(false);
  const [uploadedAvatarUrl, setUploadedAvatarUrl] = useState('');
  const [avatarLoadFailed, setAvatarLoadFailed] = useState(false);
  const menuRef = useRef(null);
  const uploadedAvatarPath =
    String(user?.profile?.avatar_image_processed || '').trim() ||
    String(user?.profile?.avatar_image_original || '').trim();

  useEffect(() => {
    if (!uploadedAvatarPath) {
      setUploadedAvatarUrl('');
      return undefined;
    }

    let active = true;
    let objectUrl = '';

    fetchAuthenticatedMediaBlobUrl(uploadedAvatarPath)
      .then((blobUrl) => {
        objectUrl = blobUrl;
        if (!active) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        setUploadedAvatarUrl(blobUrl);
      })
      .catch(() => {
        if (active) {
          setUploadedAvatarUrl('');
        }
      });

    return () => {
      active = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [uploadedAvatarPath]);

  const providerAvatarRaw =
    user?.auth_picture_url ||
    user?.provider_picture ||
    user?.picture ||
    user?.photo_url ||
    user?.avatar_url ||
    user?.image_url ||
    user?.profile?.provider_avatar_url ||
    '';
  const avatarSrc = uploadedAvatarUrl || toAbsoluteMediaLikeUrl(providerAvatarRaw);

  useEffect(() => {
    setAvatarLoadFailed(false);
  }, [avatarSrc]);

  useEffect(() => {
    if (!open) return undefined;

    const handleClickOutside = (event) => {
      if (!menuRef.current?.contains(event.target)) {
        setOpen(false);
      }
    };

    window.addEventListener('mousedown', handleClickOutside);
    return () => window.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  if (authLoading) {
    return (
      <Button variant="secondary" size="sm" disabled>
        <span>Syncing...</span>
      </Button>
    );
  }

  if (!user) {
    return (
      <Button variant="secondary" size="sm" onClick={onLoginRequest}>
        <LogIn size={16} />
        <span>Sign In</span>
      </Button>
    );
  }

  const displayName = displayNameFromUser(user);
  const roleLabel = userRoleLabel(user);
  const visibleAvatarSrc = avatarLoadFailed ? '' : avatarSrc;

  return (
    <div className="relative" ref={menuRef}>
      <div className="flex items-center gap-2 px-1 py-1">
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          className="focus-ring hidden max-w-[11rem] text-right sm:flex sm:flex-col sm:items-end"
          aria-label="Account menu"
          aria-expanded={open}
          aria-haspopup="menu"
        >
          <span className="truncate text-sm font-medium text-[var(--text-primary)]">{displayName}</span>
          <span className="text-[0.68rem] tracking-[0.12em] text-[var(--accent-primary)]">{roleLabel}</span>
        </button>

        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          className="focus-ring inline-flex h-10 w-10 items-center justify-center overflow-hidden rounded-full"
          aria-label="Open account menu"
          aria-expanded={open}
          aria-haspopup="menu"
        >
          {visibleAvatarSrc ? (
            <img
              src={visibleAvatarSrc}
              alt={`${displayName} profile`}
              className="h-full w-full object-cover"
              onError={() => setAvatarLoadFailed(true)}
            />
          ) : (
            <span
              className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-[image:var(--accent-gradient)] text-xs font-semibold text-[var(--accent-inverse)]"
              aria-hidden="true"
            >
              {initialsFromUser(user)}
            </span>
          )}
        </button>
      </div>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-[70] mt-3 w-56 rounded-2xl border border-[color:rgba(73,68,84,0.15)] bg-[var(--surface-container)] p-3"
        >
          <div className="mb-2 rounded-xl bg-[var(--surface-container-high)] px-3 py-2">
            <p className="label-sm">Account</p>
            <p className="mt-1 text-sm font-medium text-[var(--text-primary)]">{displayName}</p>
            <p className="mt-0.5 text-xs text-[var(--text-secondary)]">{roleLabel}</p>
          </div>

          <Link
            to="/"
            className="focus-ring mb-1 flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-[#9ca3af] hover:bg-[color:var(--hover-surface)] hover:text-[var(--text-primary)]"
            onClick={() => setOpen(false)}
          >
            <LayoutDashboard size={15} />
            <span>Dashboard</span>
          </Link>

          <Link
            to="/library"
            className="focus-ring mb-1 flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-[#9ca3af] hover:bg-[color:var(--hover-surface)] hover:text-[var(--text-primary)]"
            onClick={() => setOpen(false)}
          >
            <UserCircle2 size={15} />
            <span>Open Library</span>
          </Link>

          <Link
            to="/settings"
            className="focus-ring mb-1 flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-[#9ca3af] hover:bg-[color:var(--hover-surface)] hover:text-[var(--text-primary)]"
            onClick={() => setOpen(false)}
          >
            <SettingsIcon size={15} />
            <span>Settings</span>
          </Link>

          <Link
            to="/help"
            className="focus-ring mb-1 flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-[#9ca3af] hover:bg-[color:var(--hover-surface)] hover:text-[var(--text-primary)]"
            onClick={() => setOpen(false)}
          >
            <CircleHelp size={15} />
            <span>Help</span>
          </Link>

          <button
            type="button"
            className="focus-ring flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-[#9ca3af] hover:bg-[color:var(--hover-surface)] hover:text-[var(--text-primary)]"
            onClick={async () => {
              setOpen(false);
              await onLogout();
            }}
          >
            <LogOut size={15} />
            <span>Sign Out</span>
          </button>
        </div>
      )}
    </div>
  );
}
