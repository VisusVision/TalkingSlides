import { useState } from 'react';
import Header from './Header';
import SideRail from './SideRail';
import MobileBottomNav from './MobileBottomNav';

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

  return (
    <div className="relative min-h-screen overflow-x-hidden bg-[var(--bg)] pb-24 md:pb-14">

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

      <div className={`relative pl-0 transition-[padding] duration-300 ${railExpanded ? 'md:pl-[16rem] xl:pl-[18rem]' : 'md:pl-[5rem]'}`}>
        <main className="mx-auto max-w-[1700px] px-3 pb-20 sm:px-6 md:pb-16 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
