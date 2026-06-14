import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchCurrentUser,
  getGoogleAuthProvider,
  getStoredAuthUser,
  getToken,
  logout,
  rerenderProject,
  setGoogleAuthProvider,
  setStoredAuthUser,
  setToken,
  updateProjectTranscript,
} from "./api";

describe("auth token storage regressions", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
    global.fetch = vi.fn();
  });

  it("persists the auth token in localStorage", () => {
    setToken("token-123");

    expect(window.localStorage.getItem("auth_token")).toBe("token-123");
    expect(getToken()).toBe("token-123");
  });

  it("cleans up token, provider, and cached user on logout", async () => {
    setToken("token-123");
    setGoogleAuthProvider("google");
    setStoredAuthUser({ id: 7, username: "learner" });
    global.fetch.mockResolvedValue({ ok: true });

    await logout();

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/auth/logout/"),
      expect.objectContaining({
        method: "POST",
        headers: { Authorization: "Token token-123" },
      }),
    );
    expect(getToken()).toBe("");
    expect(getGoogleAuthProvider()).toBe("");
    expect(getStoredAuthUser()).toBeNull();
    expect(window.localStorage.getItem("auth_token")).toBeNull();
    expect(window.localStorage.getItem("auth_provider")).toBeNull();
    expect(window.localStorage.getItem("auth_user")).toBeNull();
  });

  it("cleans cached auth state when auth/me rejects the stored token", async () => {
    setToken("revoked-token");
    setGoogleAuthProvider("password");
    setStoredAuthUser({ id: 9, username: "cached" });
    global.fetch.mockResolvedValue({
      status: 401,
      ok: false,
      json: async () => ({ detail: "Invalid token." }),
    });

    const user = await fetchCurrentUser();

    expect(user).toBeNull();
    expect(getToken()).toBe("");
    expect(getGoogleAuthProvider()).toBe("");
    expect(getStoredAuthUser()).toBeNull();
  });
});

describe("transcript rerender avatar payload", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ project_id: 42, pages: [] }),
    });
  });

  it("omits avatar flags from save-only transcript updates", async () => {
    await updateProjectTranscript(42, [], { triggerRerender: false });

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/projects/42/transcript/"),
      expect.objectContaining({ method: "PATCH" }),
    );
    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.trigger_rerender).toBe(false);
    expect(body).not.toHaveProperty("avatar_enabled");
    expect(body).not.toHaveProperty("render_with_avatar");
  });

  it("sends render_with_avatar true for rerender updates when checked", async () => {
    await updateProjectTranscript(42, [], { triggerRerender: true, renderWithAvatar: true });

    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.trigger_rerender).toBe(true);
    expect(body.render_with_avatar).toBe(true);
    expect(body.avatar_enabled).toBe("1");
  });

  it("sends render_with_avatar false for rerender updates when unchecked", async () => {
    await updateProjectTranscript(42, [], { triggerRerender: true, renderWithAvatar: false });

    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.trigger_rerender).toBe(true);
    expect(body.render_with_avatar).toBe(false);
    expect(body.avatar_enabled).toBe("0");
  });
});

describe("project rerender avatar payload", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: 99, status: "pending" }),
    });
  });

  it("sends render_with_avatar true for standalone rerender when checked", async () => {
    await rerenderProject(99, { renderWithAvatar: true });

    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.render_with_avatar).toBe(true);
    expect(body.avatar_enabled).toBe("1");
  });

  it("sends render_with_avatar false for standalone rerender when unchecked", async () => {
    await rerenderProject(99, { renderWithAvatar: false });

    const body = JSON.parse(global.fetch.mock.calls[0][1].body);
    expect(body.render_with_avatar).toBe(false);
    expect(body.avatar_enabled).toBe("0");
  });
});
