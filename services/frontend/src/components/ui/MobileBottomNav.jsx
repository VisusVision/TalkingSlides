import { BarChart3, BookOpenText, LayoutDashboard, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import {
  canAccessAnalytics,
  canAccessModeration,
  canAccessStudio,
  isSignedIn,
} from '../../lib/auth';
import { useNavigationState } from '../../app/navigationState';

const MOBILE_ITEMS = [
  { to: '/', label: 'Home', icon: LayoutDashboard, section: 'dashboard', end: true, resetAlways: true },
  { to: '/library', label: 'Library', icon: BookOpenText, section: 'library', signedInOnly: true },
  { to: '/studio', label: 'Studio', icon: SlidersHorizontal, section: 'studio', studioOnly: true },
  { to: '/analytics', label: 'Insights', icon: BarChart3, section: 'analytics', analyticsOnly: true },
  { to: '/moderation', label: 'Review', icon: ShieldCheck, section: 'moderation', moderationOnly: true },
];

function mobileItemClass(isActive) {
  return `focus-ring flex min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-full px-1 py-2 text-[10px] font-semibold uppercase tracking-[0.1em] transition-all duration-200 ${
    isActive
      ? 'scale-105 bg-[color:rgba(107,56,212,0.1)] text-[var(--accent-primary)] dark:bg-[color:rgba(208,188,255,0.18)]'
      : 'text-[var(--outline)] hover:text-[var(--accent-primary)]'
  }`;
}

export default function MobileBottomNav({ user }) {
  const { currentSection, navigateToSection } = useNavigationState();
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
        const handleClick = (event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
          event.preventDefault();
          navigateToSection(item.section, { reset: item.resetAlways || currentSection === item.section });
        };

        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={handleClick}
            className={({ isActive }) => mobileItemClass(isActive)}
            aria-label={item.label}
          >
            <Icon size={18} strokeWidth={2} />
            <span>{item.label}</span>
          </NavLink>
        );
      })}
    </nav>
  );
}
