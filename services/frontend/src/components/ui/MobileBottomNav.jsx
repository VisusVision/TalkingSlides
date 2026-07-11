import { BarChart3, BookOpenText, LayoutDashboard, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  canAccessAnalytics,
  canAccessModeration,
  canAccessStudio,
  isSignedIn,
} from '../../lib/auth';
import { requestRouteReset, routeIdForPath } from '../../utils/routeSession';

const MOBILE_ITEMS = [
  { to: '/', label: 'Home', icon: LayoutDashboard, end: true },
  { to: '/library', label: 'Library', icon: BookOpenText, signedInOnly: true },
  { to: '/studio', label: 'Studio', icon: SlidersHorizontal, studioOnly: true },
  { to: '/analytics', label: 'Insights', icon: BarChart3, analyticsOnly: true },
  { to: '/moderation', label: 'Review', icon: ShieldCheck, moderationOnly: true },
];

function mobileItemClass(isActive) {
  return `focus-ring relative flex min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-full px-1 py-2 text-[10px] font-semibold uppercase tracking-[0.1em] outline-offset-2 transition-all duration-200 ${
    isActive
      ? 'bg-[color:var(--hover-accent-soft)] text-[var(--accent-primary)] shadow-[inset_0_0_0_1px_var(--accent-primary)]'
      : 'text-[var(--text-secondary)] hover:bg-[color:var(--hover-surface)] hover:text-[var(--text-primary)]'
  }`;
}

export default function MobileBottomNav({ user }) {
  const location = useLocation();
  const signedIn = isSignedIn(user);
  const studioAllowed = canAccessStudio(user);
  const analyticsAllowed = canAccessAnalytics(user);
  const moderationAllowed = canAccessModeration(user);
  const mobileItems = MOBILE_ITEMS.filter((item) => {
    if (item.signedInOnly) return signedIn;
    if (item.studioOnly) return studioAllowed;
    if (item.analyticsOnly) return analyticsAllowed;
    if (item.moderationOnly) return moderationAllowed;
    return true;
  });

  return (
    <nav
      className="fixed bottom-0 left-0 z-50 flex w-full items-center justify-around gap-1 rounded-t-[2rem] border-t border-[color:var(--border-subtle)] bg-[color:rgba(255,255,255,0.9)] px-2 pb-5 pt-2 backdrop-blur-2xl dark:bg-[color:rgba(15,17,21,0.9)] md:hidden"
      aria-label="Mobile primary navigation"
    >
      {mobileItems.map((item) => {
        const Icon = item.icon;
        const routeId = routeIdForPath(item.to);
        const activeRouteId = routeIdForPath(location.pathname);
        const activeForReset = routeId && activeRouteId === routeId && (!item.end || location.pathname === item.to);

        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={() => {
              if (activeForReset) {
                requestRouteReset(routeId, user);
              }
            }}
            className={({ isActive }) => mobileItemClass(isActive)}
            aria-label={item.label}
          >
            {({ isActive }) => (
              <>
                {isActive ? <span aria-hidden="true" className="absolute top-1 h-1 w-5 rounded-full bg-[var(--accent-primary)]" /> : null}
                <Icon size={18} strokeWidth={2} />
                <span>{item.label}</span>
              </>
            )}
          </NavLink>
        );
      })}
    </nav>
  );
}
