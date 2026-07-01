import { BarChart3, BookOpenText, LayoutDashboard, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  canAccessAnalytics,
  canAccessModeration,
  canAccessStudio,
  isSignedIn,
} from '../../lib/auth';
import { requestRouteReset, routeIdForPath } from '../../utils/routeSession';
import { useI18n } from '../../i18n/I18nProvider';

const MOBILE_ITEMS = [
  { to: '/', labelKey: 'navigation.home', icon: LayoutDashboard, end: true },
  { to: '/library', labelKey: 'navigation.library', icon: BookOpenText, signedInOnly: true },
  { to: '/studio', labelKey: 'navigation.studio', icon: SlidersHorizontal, studioOnly: true },
  { to: '/analytics', labelKey: 'navigation.insights', icon: BarChart3, analyticsOnly: true },
  { to: '/moderation', labelKey: 'navigation.review', icon: ShieldCheck, moderationOnly: true },
];

function mobileItemClass(isActive) {
  return `focus-ring flex min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-full px-1 py-2 text-[10px] font-semibold uppercase tracking-[0.1em] transition-all duration-200 ${
    isActive
      ? 'scale-105 bg-[color:rgba(107,56,212,0.1)] text-[var(--accent-primary)] dark:bg-[color:rgba(208,188,255,0.18)]'
      : 'text-[var(--outline)] hover:text-[var(--accent-primary)]'
  }`;
}

export default function MobileBottomNav({ user }) {
  const location = useLocation();
  const { t } = useI18n();
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
      aria-label={t('navigation.mobilePrimary')}
    >
      {mobileItems.map((item) => {
        const Icon = item.icon;
        const label = t(item.labelKey);
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
            aria-label={label}
          >
            <Icon size={18} strokeWidth={2} />
            <span>{label}</span>
          </NavLink>
        );
      })}
    </nav>
  );
}
