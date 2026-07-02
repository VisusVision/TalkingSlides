import { useState } from 'react';
import Header from './Header';
import SideRail from './SideRail';
import MobileBottomNav from './MobileBottomNav';
import { useI18n } from '../../i18n/I18nProvider';

export default function AppShell({
  searchQuery,
  onSearchQueryChange,
  user,
  authLoading,
  onLoginRequest,
  onLogout,
  children,
}) {
  const [railCollapsed, setRailCollapsed] = useState(true);
  const railExpanded = !railCollapsed;
  const { direction, isRtl } = useI18n();
  const contentOffset = railExpanded
    ? (isRtl ? 'md:pr-[16rem] xl:pr-[18rem]' : 'md:pl-[16rem] xl:pl-[18rem]')
    : (isRtl ? 'md:pr-[5rem]' : 'md:pl-[5rem]');

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[var(--bg)] pb-24 md:pb-14" dir={direction}>

      <Header
        searchQuery={searchQuery}
        onSearchQueryChange={onSearchQueryChange}
        user={user}
        authLoading={authLoading}
        onLoginRequest={onLoginRequest}
        onLogout={onLogout}
      />

      <SideRail
        user={user}
        collapsed={railCollapsed}
        expanded={railExpanded}
        onToggleCollapse={() => setRailCollapsed((prev) => !prev)}
      />

      <MobileBottomNav user={user} />

      <div className={`relative transition-[padding] duration-300 ${contentOffset}`}>
        <main className="mx-auto max-w-[1700px] px-3 pb-20 sm:px-6 md:pb-16 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
