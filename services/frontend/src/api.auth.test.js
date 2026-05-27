import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  fetchCurrentUser,
  getGoogleAuthProvider,
  getStoredAuthUser,
  getToken,
  logout,
  setGoogleAuthProvider,
  setStoredAuthUser,
  setToken,
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
