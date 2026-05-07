import { Link } from 'react-router-dom';
import { Search, Bell } from 'lucide-react';
import ProfileMenu from './ProfileMenu';

export default function Header({
  searchQuery,
  onSearchQueryChange,
  user,
  authLoading,
  onLoginRequest,
  onLogout,
}) {
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
          </div>

          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <button
              type="button"
              className="focus-ring hidden h-10 w-10 items-center justify-center rounded-full text-[#9ca3af] transition hover:bg-[color:var(--hover-accent-soft)] hover:text-[var(--text-primary)] md:inline-flex"
              aria-label="Notifications"
              title="Notifications coming soon"
            >
              <Bell size={16} />
            </button>

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