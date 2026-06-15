import React from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fetchCurrentUser: vi.fn(),
  getStoredAuthUser: vi.fn(),
  logout: vi.fn(),
  setGoogleAuthProvider: vi.fn(),
  setToken: vi.fn(),
  refreshCapabilities: vi.fn(),
}));

vi.mock("../api", () => ({
  fetchCurrentUser: mocks.fetchCurrentUser,
  getStoredAuthUser: mocks.getStoredAuthUser,
  logout: mocks.logout,
  setGoogleAuthProvider: mocks.setGoogleAuthProvider,
  setToken: mocks.setToken,
}));

vi.mock("../lib/capabilities", () => ({
  CapabilitiesProvider: ({ children }) => <>{children}</>,
  useCapabilities: () => ({ refreshCapabilities: mocks.refreshCapabilities }),
}));

vi.mock("../components/ui/AppShell", () => ({
  default: ({ children, searchQuery, onSearchQueryChange }) => (
    <div data-testid="app-shell">
      <input
        data-testid="route-search"
        value={searchQuery}
        onInput={(event) => onSearchQueryChange(event.currentTarget.value)}
        onChange={(event) => onSearchQueryChange(event.currentTarget.value)}
      />
      {children}
    </div>
  ),
}));

vi.mock("../components/ui/AuthModal", () => ({
  default: () => null,
}));

vi.mock("../components/ui/ThemeProvider", () => ({
  ThemeProvider: ({ children }) => <>{children}</>,
}));

vi.mock("../components/ui/SurfaceCard", () => ({
  default: ({ children }) => <div>{children}</div>,
}));

vi.mock("./router", () => ({
  default: ({ searchQuery }) => <main data-testid="router" data-search-query={searchQuery} />,
}));

import App, { searchScopeForPathname } from "./App";

async function renderApp() {
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(<App />);
  });
  await act(async () => {});
  return { host, root };
}

describe("redirect hash auth regressions", () => {
  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    vi.clearAllMocks();
    mocks.getStoredAuthUser.mockReturnValue(null);
    mocks.fetchCurrentUser.mockResolvedValue(null);
    mocks.logout.mockResolvedValue(undefined);
    mocks.refreshCapabilities.mockResolvedValue(undefined);
    window.sessionStorage.clear();
    window.history.replaceState({}, "", "/");
  });

  it("keeps header search state scoped to the current route", async () => {
    mocks.getStoredAuthUser.mockReturnValue({ id: 1, username: "teacher" });
    mocks.fetchCurrentUser.mockResolvedValue({ id: 1, username: "teacher" });
    window.history.replaceState({}, "", "/moderation");

    const { root, host } = await renderApp();
    const input = host.querySelector('[data-testid="route-search"]');
    const router = host.querySelector('[data-testid="router"]');

    await act(async () => {
      input.value = "flagged";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(router.getAttribute("data-search-query")).toBe("flagged");

    await act(async () => {
      window.history.pushState({}, "", "/studio");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(input.value).toBe("");
    expect(router.getAttribute("data-search-query")).toBe("");

    await act(async () => {
      input.value = "lesson";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(router.getAttribute("data-search-query")).toBe("lesson");

    await act(async () => {
      window.history.pushState({}, "", "/moderation");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(input.value).toBe("flagged");
    expect(router.getAttribute("data-search-query")).toBe("flagged");

    await act(async () => root.unmount());
    host.remove();
  });

  it("namespaces route search scopes", () => {
    expect(searchScopeForPathname("/moderation")).toBe("moderation");
    expect(searchScopeForPathname("/studio")).toBe("studio");
    expect(searchScopeForPathname("/browse")).toBe("browse");
    expect(searchScopeForPathname("/watch/42")).toBe("watch");
    expect(searchScopeForPathname("/channel/42")).toBe("channel:/channel/42");
  });

  it("parses auth_token from the redirect hash and removes the hash from history", async () => {
    const replaceState = vi.spyOn(window.history, "replaceState");
    window.history.replaceState({}, "", "/?redirect=%2Fstudio#auth_token=hash-token&provider=google");

    const { root, host } = await renderApp();

    expect(mocks.setToken).toHaveBeenCalledWith("hash-token");
    expect(mocks.setGoogleAuthProvider).toHaveBeenCalledWith("google");
    expect(replaceState).toHaveBeenCalledWith({}, document.title, "/?redirect=%2Fstudio");
    expect(window.location.hash).toBe("");

    await act(async () => root.unmount());
    host.remove();
  });

  it("defaults redirect hash provider to google when provider is missing", async () => {
    window.history.replaceState({}, "", "/#auth_token=hash-token");

    const { root, host } = await renderApp();

    expect(mocks.setToken).toHaveBeenCalledWith("hash-token");
    expect(mocks.setGoogleAuthProvider).toHaveBeenCalledWith("google");

    await act(async () => root.unmount());
    host.remove();
  });

  it("ignores missing or blank auth_token hash values", async () => {
    window.history.replaceState({}, "", "/#provider=google&auth_token=%20%20");
    const replaceState = vi.spyOn(window.history, "replaceState");
    replaceState.mockClear();

    const { root, host } = await renderApp();

    expect(mocks.setToken).not.toHaveBeenCalled();
    expect(mocks.setGoogleAuthProvider).not.toHaveBeenCalled();
    expect(replaceState).not.toHaveBeenCalledWith({}, document.title, "/");
    expect(window.location.hash).toBe("#provider=google&auth_token=%20%20");

    await act(async () => root.unmount());
    host.remove();
  });
});
