import {
  BarChart3,
  BookOpenText,
  CircleHelp,
  LayoutDashboard,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Plus,
} from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';
import {
  canAccessAnalytics,
  canAccessModeration,
  canAccessStudio,
  isSignedIn,
} from '../../lib/auth';
import { useNavigationState } from '../../app/navigationState';

const PRIMARY_ITEMS = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, section: 'dashboard', end: true, resetAlways: true },
  { to: '/library', label: 'Library', icon: BookOpenText, section: 'library', signedInOnly: true },
  { to: '/studio', label: 'Studio', icon: SlidersHorizontal, section: 'studio', studioOnly: true },
  { to: '/analytics', label: 'Analytics', icon: BarChart3, section: 'analytics', analyticsOnly: true },
  { to: '/moderation', label: 'Moderation', icon: ShieldCheck, section: 'moderation', moderationOnly: true },
];

function railItemClass(isActive, expanded) {
  const tone = isActive
    ? expanded
      ? 'bg-[color:rgba(107,56,212,0.1)] text-[var(--accent-primary)] font-semibold shadow-[inset_0_0_0_2px_rgba(107,56,212,0.5)] dark:bg-[color:rgba(208,188,255,0.1)] dark:shadow-[inset_0_0_0_2px_rgba(208,188,255,0.62)]'
      : 'bg-[color:rgba(107,56,212,0.1)] text-[var(--accent-primary)] shadow-[inset_0_0_0_1.5px_rgba(107,56,212,0.55)] dark:bg-[color:rgba(208,188,255,0.1)] dark:shadow-[inset_0_0_0_1.5px_rgba(208,188,255,0.65)]'
    : 'text-[#9ca3af] hover:bg-[color:var(--hover-surface)] hover:text-[var(--text-primary)]';
  const layout = expanded
    ? 'mx-4 justify-start rounded-full px-4 py-3'
    : 'mx-auto h-12 w-12 justify-center rounded-full border border-[color:var(--border-subtle)] p-0';

  return `group focus-ring relative flex items-center gap-3 transition-all duration-200 ${tone} ${layout}`;
}

function RailTooltip({ label, rightOffset = true }) {
  return (
    <span
      className={`pointer-events-none absolute top-1/2 hidden -translate-y-1/2 whitespace-nowrap rounded-full border border-[var(--border-subtle)] bg-[var(--surface-elevated)] px-2.5 py-1 text-xs font-medium text-[var(--text-primary)] opacity-0 transition group-hover:opacity-100 group-focus-visible:opacity-100 md:block ${
        rightOffset ? 'left-[calc(100%+0.55rem)]' : 'right-[calc(100%+0.55rem)]'
      }`}
    >
      {label}
    </span>
  );
}

function RailNavItem({
  to,
  label,
  icon: Icon,
  section = null,
  resetAlways = false,
  end = false,
  expanded,
  currentSection,
  navigateToSection,
}) {
  const handleClick = (event) => {
    if (!section || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    navigateToSection(section, { reset: resetAlways || currentSection === section });
  };

  return (
    <NavLink to={to} end={end} title={label} aria-label={label} onClick={handleClick} className={({ isActive }) => railItemClass(isActive, expanded)}>
      {({ isActive }) => (
        <>
          <span
            className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
              isActive ? 'bg-transparent text-[var(--accent-primary)]' : 'bg-transparent text-current'
            }`}
          >
            <Icon size={17} strokeWidth={2} />
          </span>
          <span className={`hidden text-sm font-semibold ${expanded ? 'md:inline' : 'md:hidden'}`}>{label}</span>
          <span className="sr-only">{label}</span>
          {!expanded ? <RailTooltip label={label} /> : null}
        </>
      )}
    </NavLink>
  );
}

function RailHelpItem({ to, label, icon: Icon, expanded }) {
  return (
    <NavLink to={to} title={label} aria-label={label} className={({ isActive }) => railItemClass(isActive, expanded)}>
      {({ isActive }) => (
        <>
          <span
            className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
              isActive ? 'bg-transparent text-[var(--accent-primary)]' : 'bg-transparent text-current'
            }`}
          >
            <Icon size={17} strokeWidth={2} />
          </span>
          <span className={`hidden text-sm font-semibold ${expanded ? 'md:inline' : 'md:hidden'}`}>{label}</span>
          <span className="sr-only">{label}</span>
          {!expanded ? <RailTooltip label={label} /> : null}
        </>
      )}
    </NavLink>
  );
}

export default function SideRail({
  user,
  collapsed,
  expanded,
  onToggleCollapse,
}) {
  const location = useLocation();
  const { currentSection, navigateToSection } = useNavigationState();
  const signedIn = isSignedIn(user);
  const studioAllowed = canAccessStudio(user);
  const analyticsAllowed = canAccessAnalytics(user);
  const moderationAllowed = canAccessModeration(user);
  const isStudioRoute = location.pathname === '/studio';
  const isAnalyticsRoute = location.pathname === '/analytics';
  const showCreateLessonAction = studioAllowed && (isStudioRoute || isAnalyticsRoute);
  const primaryItems = PRIMARY_ITEMS.filter((item) => {
    if (item.signedInOnly) return signedIn;
    if (item.studioOnly) return studioAllowed;
    if (item.analyticsOnly) return analyticsAllowed;
    if (item.moderationOnly) return moderationAllowed;
    return true;
  });

  const handleCreateLessonRequest = () => {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('visus:create-lesson-request'));
    }
  };

  return (
    <aside
      className={`fixed left-0 top-16 z-40 hidden h-[calc(100vh-4rem)] transition-[width] duration-300 md:block ${expanded ? 'md:w-[16rem] xl:w-[18rem]' : 'md:w-[5rem]'}`}
    >
      <div className="flex h-full flex-col border-r border-[color:var(--border-subtle)] bg-[var(--surface)] py-4">
        <div className="px-3 pb-4 lg:px-5">
          <div className={`flex items-center gap-2 ${expanded ? 'justify-between' : 'justify-center'}`}>
            {expanded ? (
              <div className="hidden md:block">
                <p className="text-[0.7rem] font-semibold uppercase tracking-[0.19em] text-[var(--text-secondary)]">VISUS Workspace</p>
                <div className="mt-1 inline-flex items-center gap-1.5 text-sm font-medium text-[var(--text-primary)]">
                  <span className="material-symbols-outlined text-base leading-none text-[var(--accent-primary)]">auto_awesome</span>
                  <span>AI-Powered Learning</span>
                </div>
              </div>
            ) : null}

            <button
              type="button"
              onClick={onToggleCollapse}
              className="focus-ring inline-flex h-9 w-9 items-center justify-center rounded-xl bg-[var(--surface-container-high)] text-[#9ca3af] transition hover:bg-[var(--surface-container-highest)] hover:text-[var(--text-primary)]"
              title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              <span className="material-symbols-outlined text-[20px] leading-none">{collapsed ? 'left_panel_open' : 'left_panel_close'}</span>
            </button>
          </div>
        </div>

        <nav className="flex h-full w-full flex-col justify-between" aria-label="Primary sidebar navigation">
          <div className="space-y-1">
            {primaryItems.map((item) => (
              <RailNavItem
                key={item.to}
                to={item.to}
                label={item.label}
                icon={item.icon}
                section={item.section}
                resetAlways={item.resetAlways}
                end={item.end}
                expanded={expanded}
                currentSection={currentSection}
                navigateToSection={navigateToSection}
              />
            ))}
          </div>

          <div className="space-y-1 pb-2">
            {showCreateLessonAction ? (
              <button
                type="button"
                onClick={handleCreateLessonRequest}
                className={`focus-ring relative mx-4 inline-flex h-11 w-[calc(100%-2rem)] items-center gap-2 transition ${expanded ? 'justify-start px-3' : 'justify-center'} ${
                  isAnalyticsRoute
                    ? 'rounded-xl bg-[image:var(--accent-gradient)] text-white font-bold hover:scale-105 active:scale-95'
                    : 'rounded-xl border border-dashed border-[color:var(--outline-variant)] text-[var(--outline)] hover:border-[var(--accent-primary)] hover:text-[var(--accent-primary)]'
                }`}
              >
                <Plus size={16} strokeWidth={2} />
                <span className={`hidden text-[0.68rem] font-bold uppercase tracking-[0.12em] ${expanded ? 'md:inline' : 'md:hidden'}`}>Create New Lesson</span>
                {!expanded ? <RailTooltip label="Create New Lesson" /> : null}
              </button>
            ) : null}

            <RailNavItem to="/settings" label="Settings" icon={Settings} expanded={expanded} currentSection={currentSection} navigateToSection={navigateToSection} />
            <RailHelpItem to="/help" label="Help" icon={CircleHelp} expanded={expanded} />
          </div>
        </nav>
      </div>
    </aside>
  );
}
