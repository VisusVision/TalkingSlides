// ---------------------------------------------------------------------------
// API Base URL normalization
// ---------------------------------------------------------------------------
// Ensures API_BASE_URL always ends with /api/v1 exactly once, supporting
// both bare origin (http://localhost:8000) and versioned styles
// (http://localhost:8000/api/v1).

const DEFAULT_API_BASE_URL = "http://localhost:8000/api/v1";

/**
 * Normalize API base URL to always end with /api/v1.
 * @param {string} value - Raw API base from env or default
 * @returns {string} Normalized API base ending in /api/v1
 *
 * Examples:
 *   normalizeApiBaseUrl("http://localhost:8000") -> "http://localhost:8000/api/v1"
 *   normalizeApiBaseUrl("http://localhost:8000/api/v1") -> "http://localhost:8000/api/v1"
 *   normalizeApiBaseUrl("https://api.example.com") -> "https://api.example.com/api/v1"
 */
export function normalizeApiBaseUrl(value) {
  const raw = String(value || DEFAULT_API_BASE_URL).trim().replace(/\/+$/, "");
  if (!raw) return DEFAULT_API_BASE_URL;
  // Case-insensitive check for existing /api/v1 suffix
  if (/\/api\/v1$/i.test(raw)) return raw;
  return `${raw}/api/v1`;
}

export const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL);
const API_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/i, "");
const AUTH_USER_STORAGE_KEY = "auth_user";
let capabilitiesPromise = null;

function capabilitiesUrl({ cacheBust = false } = {}) {
  const baseUrl = `${API_BASE_URL}/capabilities/`;
  if (!cacheBust) return baseUrl;
  const separator = baseUrl.includes("?") ? "&" : "?";
  return `${baseUrl}${separator}_capabilities_ts=${Date.now()}`;
}

function toAbsoluteApiUrl(url) {
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  return `${API_ORIGIN}${url.startsWith("/") ? url : `/${url}`}`;
}

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

export function getToken() {
  return localStorage.getItem("auth_token") || "";
}

export function setToken(token) {
  if (token) {
    localStorage.setItem("auth_token", token);
  } else {
    localStorage.removeItem("auth_token");
  }
  clearCapabilitiesCache();
}

export function getStoredAuthUser() {
  const raw = localStorage.getItem(AUTH_USER_STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    localStorage.removeItem(AUTH_USER_STORAGE_KEY);
    return null;
  }
}

export function setStoredAuthUser(user) {
  if (!user || typeof user !== "object") {
    localStorage.removeItem(AUTH_USER_STORAGE_KEY);
    return;
  }
  try {
    localStorage.setItem(AUTH_USER_STORAGE_KEY, JSON.stringify(user));
  } catch {
    localStorage.removeItem(AUTH_USER_STORAGE_KEY);
  }
}

export function getGoogleAuthProvider() {
  return localStorage.getItem("auth_provider") || "";
}

export function setGoogleAuthProvider(provider) {
  if (provider) {
    localStorage.setItem("auth_provider", provider);
  } else {
    localStorage.removeItem("auth_provider");
  }
}

function clearLocalAuthState() {
  setToken(null);
  setGoogleAuthProvider("");
  setStoredAuthUser(null);
}

function authHeaders(extra = {}) {
  const token = getToken();
  return token ? { Authorization: `Token ${token}`, ...extra } : { ...extra };
}

function apiErrorMessage(data, fallback) {
  const detail = data?.error || data?.detail || data?.message || data?.details;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return fallback;
}

function apiError(data, fallback) {
  const error = new Error(apiErrorMessage(data, fallback));
  error.details = data;
  return error;
}

export function clearCapabilitiesCache() {
  capabilitiesPromise = null;
}

export async function fetchCapabilities({ force = false } = {}) {
  if (force) {
    clearCapabilitiesCache();
  }
  if (!force && capabilitiesPromise) {
    return capabilitiesPromise;
  }
  const request = fetch(capabilitiesUrl({ cacheBust: force || import.meta.env.DEV }), {
    cache: "no-store",
  })
    .then(async (res) => {
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw apiError(data, "Failed to fetch deployment capabilities");
      }
      return data;
    })
    .finally(() => {
      if (capabilitiesPromise === request) {
        capabilitiesPromise = null;
      }
    });
  capabilitiesPromise = request;
  return capabilitiesPromise;
}

export async function fetchAuthenticatedMediaBlobUrl(relPath) {
  const safeRel = String(relPath || "").replace(/^\/+/, "").trim();
  if (!safeRel) return "";
  const url = `${API_BASE_URL.replace(/\/api\/v1\/?$/, "")}/api/v1/media/${safeRel}`;
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to fetch media (${res.status})`);
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

export async function fetchAuthenticatedAssetBlobUrl(url) {
  const absolute = toAbsoluteApiUrl(url);
  if (!absolute) return "";
  const separator = absolute.includes("?") ? "&" : "?";
  const cacheBustedUrl = `${absolute}${separator}t=${Date.now()}`;
  const res = await fetch(cacheBustedUrl, {
    headers: authHeaders(),
    cache: "no-store",
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const error = new Error(data.error || `Failed to fetch asset (${res.status})`);
    error.status = res.status;
    error.url = absolute;
    throw error;
  }
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function login(username, password) {
  const res = await fetch(`${API_BASE_URL}/auth/login/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Login failed");
  }
  const data = await res.json();
  setToken(data.token);
  setGoogleAuthProvider(data?.user?.auth_provider || "password");
  setStoredAuthUser(data?.user || null);
  return data; // { token, user }
}

export async function logout() {
  await fetch(`${API_BASE_URL}/auth/logout/`, {
    method: "POST",
    headers: authHeaders(),
  });
  clearLocalAuthState();
}

export async function fetchCurrentUser() {
  const token = getToken();
  if (!token) {
    setStoredAuthUser(null);
    return null;
  }

  const cachedUser = getStoredAuthUser();
  let res;

  try {
    res = await fetch(`${API_BASE_URL}/auth/me/`, { headers: authHeaders() });
  } catch (error) {
    console.warn("Auth check failed due to network error; using cached user.", error);
    return cachedUser;
  }

  if (res.status === 401 || res.status === 403) {
    clearLocalAuthState();
    return null;
  }

  if (!res.ok) {
    console.warn(`Auth check failed (${res.status}); using cached user.`);
    return cachedUser;
  }

  const data = await res.json();
  setStoredAuthUser(data || null);
  return data;
}

export async function fetchHelpContent() {
  const res = await fetch(`${API_BASE_URL}/help/`);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to fetch help content"));
  }
  return data;
}

export async function fetchMyProfile() {
  const res = await fetch(`${API_BASE_URL}/me/profile/`, { headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to fetch profile"));
  }
  return data;
}

export async function updateMyProfile(payload = {}) {
  const res = await fetch(`${API_BASE_URL}/me/profile/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      first_name: payload.first_name ?? "",
      last_name: payload.last_name ?? "",
      bio: payload.bio ?? "",
      display_name: payload.display_name ?? "",
      website_url: payload.website_url ?? "",
      contact_email: payload.contact_email ?? "",
      social_links: payload.social_links && typeof payload.social_links === "object" ? payload.social_links : {},
      is_public_profile: Boolean(payload.is_public_profile),
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw apiError(data, "Failed to update profile");
  }
  return data;
}

export async function uploadProfileAssets({ bannerFile, logoFile } = {}) {
  const formData = new FormData();
  if (bannerFile) formData.append("banner_file", bannerFile);
  if (logoFile) formData.append("logo_file", logoFile);
  const res = await fetch(`${API_BASE_URL}/me/profile-assets/`, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw apiError(data, "Failed to upload profile images");
  }
  return data;
}

export async function fetchNotifications({ limit = 20, offset = 0, unreadOnly = false } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(limit || 20));
  if (offset) params.set("offset", String(offset));
  if (unreadOnly) params.set("unread_only", "1");
  const res = await fetch(`${API_BASE_URL}/me/notifications/?${params.toString()}`, {
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to fetch notifications"));
  }
  return data;
}

export async function fetchNotificationUnreadCount() {
  const res = await fetch(`${API_BASE_URL}/me/notifications/unread-count/`, {
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to fetch notification count"));
  }
  return data;
}

export async function markNotificationRead(id) {
  const res = await fetch(`${API_BASE_URL}/me/notifications/${id}/read/`, {
    method: "POST",
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to mark notification read"));
  }
  return data;
}

export async function markAllNotificationsRead() {
  const res = await fetch(`${API_BASE_URL}/me/notifications/mark-all-read/`, {
    method: "POST",
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to mark notifications read"));
  }
  return data;
}

export async function fetchAuthProviders() {
  const res = await fetch(`${API_BASE_URL}/auth/providers/`);
  if (!res.ok) throw new Error("Failed to fetch auth providers");
  return res.json();
}

export async function loginWithGoogle(credential) {
  const res = await fetch(`${API_BASE_URL}/auth/google/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credential }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Google sign-in failed");
  }
  const data = await res.json();
  setToken(data.token);
  setGoogleAuthProvider(data?.provider || data?.user?.auth_provider || "google");
  setStoredAuthUser(data?.user || null);
  return data;
}

export async function startGoogleRedirectFlow() {
  const res = await fetch(`${API_BASE_URL}/auth/google/redirect/start/`);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Google redirect sign-in is unavailable");
  }
  return res.json();
}

export async function fetchUser(userId) {
  const res = await fetch(`${API_BASE_URL}/users/${userId}/`, { headers: authHeaders() });
  if (!res.ok) return null;
  return res.json();
}

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------

export async function fetchProjects(options = {}) {
  const params = new URLSearchParams();
  if (options.limit !== undefined && options.limit !== null) params.set("limit", String(options.limit));
  if (options.offset !== undefined && options.offset !== null) params.set("offset", String(options.offset));
  const query = String(options.q ?? options.search ?? "").trim();
  if (query) params.set("q", query);
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(`${API_BASE_URL}/projects/${suffix}`, { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to fetch projects");
  return res.json();
}

export async function fetchProject(projectId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, { headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to fetch project"));
  }
  return data;
}

export async function createProject(formData) {
  const res = await fetch(`${API_BASE_URL}/projects/`, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Upload failed: ${text}`);
  }
  return res.json();
}

export async function deleteProject(projectId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("Failed to delete project");
  return true;
}

export async function rerenderProject(projectId, options = {}) {
  const body = {};
  if (Object.prototype.hasOwnProperty.call(options, "avatarEnabled")) {
    body.avatar_enabled = options.avatarEnabled ? "1" : "0";
  }
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/rerender/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw apiError(data, "Failed to rerender project");
  return data;
}

export async function rerenderProjectAvatar(projectId, options = {}) {
  const body = {};
  if (Object.prototype.hasOwnProperty.call(options, "force")) {
    body.force = Boolean(options.force);
  }
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/avatar/rerender/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || data.message || "Failed to rerender avatar");
  }
  return data;
}

export async function updateProjectTtsSettings(projectId, ttsSettings, options = {}) {
  const draftOnly = options.draftOnly !== false;
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ tts_settings: ttsSettings, draft_only: draftOnly }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const details = data.details || data.detail;
    const detailText = typeof details === "string" ? details : details ? JSON.stringify(details) : "";
    const message = data.error || detailText || "Failed to update project TTS settings";
    throw new Error(message);
  }
  return data;
}

export async function updateProjectCategory(projectId, categoryId = null, categoryName = "") {
  const trimmedName = String(categoryName || "").trim();
  const body = trimmedName
    ? { category_name: trimmedName }
    : { category_id: categoryId === null || categoryId === "" ? null : Number(categoryId) };
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to update project category");
  }
  return res.json();
}

export async function updateProjectAvatarEnabled(projectId, avatarEnabled) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ avatar_enabled: avatarEnabled }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to update project avatar setting");
  }
  return res.json();
}

export async function updateProjectAvatarVisible(projectId, avatarVisible) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ avatar_visible: Boolean(avatarVisible) }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to update avatar visibility");
  }
  return res.json();
}

export async function updateProjectAvatarRuntimeSettings(projectId, avatarRuntimeSettings) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ avatar_runtime_settings: avatarRuntimeSettings || {} }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to update avatar runtime settings");
  }
  return res.json();
}

export async function updateProjectPublished(projectId, isPublished) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ is_published: Boolean(isPublished) }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to update project publication state"));
  }
  return res.json();
}

export async function getProjectModeration(projectId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/moderation/`, {
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to fetch moderation status"));
  }
  return data;
}

export async function rescanProjectModeration(projectId, phase = "manual_rescan") {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/moderation/rescan/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ phase }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to resubmit moderation scan"));
  }
  return data;
}

export async function requestProjectAdminReview(projectId, message = "") {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/moderation/request-admin-review/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ message }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = apiError(data, "Failed to request admin review");
    error.status = res.status;
    throw error;
  }
  return data;
}

export async function reportLesson(projectId, { category, message = "" } = {}) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/report/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ category, message }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to submit lesson report"));
  }
  return data;
}

export async function listModerationReports(status = "open") {
  const params = new URLSearchParams();
  if (status) params.set("status", String(status));
  const query = params.toString();
  const url = query
    ? `${API_BASE_URL}/moderation/reports/?${query}`
    : `${API_BASE_URL}/moderation/reports/`;
  const res = await fetch(url, {
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to fetch moderation reports"));
  }
  return data;
}

export async function runModerationReportAction(reportId, action, reason = "") {
  const res = await fetch(`${API_BASE_URL}/admin/moderation/reports/${reportId}/action/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ action, reason }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to update moderation report"));
  }
  return data;
}

export async function listModerationReviewRequests(status = "open") {
  const params = new URLSearchParams();
  if (status && typeof status === "object") {
    Object.entries(status).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
    });
  } else if (status) {
    params.set("status", String(status));
  }
  const query = params.toString();
  const url = query
    ? `${API_BASE_URL}/admin/moderation/review-requests/?${query}`
    : `${API_BASE_URL}/admin/moderation/review-requests/`;
  const res = await fetch(url, {
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to fetch moderation review requests"));
  }
  return data;
}

export async function getModerationReviewRequest(id) {
  const res = await fetch(`${API_BASE_URL}/admin/moderation/review-requests/${id}/`, {
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to fetch moderation review request"));
  }
  return data;
}

export async function approveModerationReviewRequest(id, adminResponse = "") {
  const res = await fetch(`${API_BASE_URL}/admin/moderation/review-requests/${id}/approve/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ admin_response: adminResponse }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to approve moderation review request"));
  }
  return data;
}

export async function rejectModerationReviewRequest(id, adminResponse = "") {
  const res = await fetch(`${API_BASE_URL}/admin/moderation/review-requests/${id}/reject/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ admin_response: adminResponse }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to reject moderation review request"));
  }
  return data;
}

export async function sendModerationReviewResponse(id, adminResponse = "") {
  const res = await fetch(`${API_BASE_URL}/admin/moderation/review-requests/${id}/response/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ admin_response: adminResponse }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to send moderation review response"));
  }
  return data;
}

export async function adminBlockLesson(projectId, reason = "") {
  const res = await fetch(`${API_BASE_URL}/moderation/projects/${projectId}/block/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ reason }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to block lesson"));
  }
  return data;
}

export async function adminApproveLesson(projectId, reason = "") {
  const res = await fetch(`${API_BASE_URL}/moderation/projects/${projectId}/approve/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ reason }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to approve lesson"));
  }
  return data;
}

export async function adminRequestLessonChanges(projectId, { reason = "", unpublish = true } = {}) {
  const res = await fetch(`${API_BASE_URL}/moderation/projects/${projectId}/request-changes/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ reason, unpublish }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to request lesson changes"));
  }
  return data;
}

export async function runAdminProjectModerationAction(
  projectId,
  action,
  reason = "",
  phase = "manual_admin_rescan",
  options = {},
) {
  const res = await fetch(`${API_BASE_URL}/admin/moderation/projects/${projectId}/action/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ action, reason, phase, ...options }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to update moderation state"));
  }
  return data;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export async function fetchJobStatus(projectId, jobId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/jobs/${jobId}/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    if (res.status === 404) return { notfound: true };
    throw new Error("Failed to fetch job status");
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Voice
// ---------------------------------------------------------------------------

export async function previewTtsNormalization(payload) {
  const res = await fetch(`${API_BASE_URL}/tts/preview/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || "Failed to preview TTS normalization");
  }
  return data;
}

export async function previewTtsAudio(payload) {
  const res = await fetch(`${API_BASE_URL}/tts/preview-audio/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const details = data.details || data.detail;
    throw new Error(data.error || details || "Failed to synthesize preview audio");
  }
  return data;
}

export async function fetchTtsPronunciationSuggestions(payload) {
  const res = await fetch(`${API_BASE_URL}/tts/pronunciation-suggestions/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const details = data.details || data.detail;
    const detailText = typeof details === "string" ? details : details ? JSON.stringify(details) : "";
    throw new Error(data.error || detailText || "Failed to fetch pronunciation suggestions");
  }
  return data;
}

export async function uploadVoiceSample(userId, file) {
  const formData = new FormData();
  formData.append("voice_file", file);
  const res = await fetch(`${API_BASE_URL}/users/${userId}/voice/`, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  if (!res.ok) throw new Error("Failed to upload voice sample");
  return res.json();
}

export async function fetchAvatarProfile(userId) {
  const res = await fetch(`${API_BASE_URL}/users/${userId}/avatar/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to fetch avatar profile");
  }
  return res.json();
}

export async function uploadAvatarImage(userId, file, settings = {}) {
  const formData = new FormData();
  formData.append("avatar_file", file);
  formData.append("avatar_consent_confirmed", settings.avatar_consent_confirmed ? "1" : "0");
  if (settings.avatar_motion_preset) formData.append("avatar_motion_preset", settings.avatar_motion_preset);
  if (settings.avatar_lipsync_engine) formData.append("avatar_lipsync_engine", settings.avatar_lipsync_engine);
  if (settings.avatar_quality_preset) formData.append("avatar_quality_preset", settings.avatar_quality_preset);
  if (typeof settings.composite_fallback_allowed === "boolean") {
    formData.append("composite_fallback_allowed", settings.composite_fallback_allowed ? "1" : "0");
  }

  const res = await fetch(`${API_BASE_URL}/users/${userId}/avatar/`, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to upload avatar image");
  }
  return res.json();
}

export async function uploadAvatarVideo(userId, file, settings = {}) {
  const formData = new FormData();
  formData.append("avatar_video_file", file);
  formData.append("avatar_consent_confirmed", settings.avatar_consent_confirmed ? "1" : "0");
  if (settings.avatar_motion_preset) formData.append("avatar_motion_preset", settings.avatar_motion_preset);
  if (settings.avatar_lipsync_engine) formData.append("avatar_lipsync_engine", settings.avatar_lipsync_engine);
  if (settings.avatar_quality_preset) formData.append("avatar_quality_preset", settings.avatar_quality_preset);
  if (typeof settings.composite_fallback_allowed === "boolean") {
    formData.append("composite_fallback_allowed", settings.composite_fallback_allowed ? "1" : "0");
  }

  const res = await fetch(`${API_BASE_URL}/users/${userId}/avatar/`, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to upload avatar video");
  }
  return res.json();
}

export async function updateAvatarProfile(userId, payload) {
  const res = await fetch(`${API_BASE_URL}/users/${userId}/avatar/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to update avatar settings");
  }
  return res.json();
}

export async function regenerateAvatarPreview(userId) {
  const res = await fetch(`${API_BASE_URL}/users/${userId}/avatar/preview/`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const err = new Error(data.error || "Failed to regenerate avatar preview");
    err.code = data.error_code || "";
    err.missingRequirements = Array.isArray(data.missing_requirements) ? data.missing_requirements : [];
    err.readiness = data.readiness || null;
    throw err;
  }
  return res.json();
}

export async function prepareAvatarProfile(userId, payload = {}) {
  const res = await fetch(`${API_BASE_URL}/users/${userId}/avatar/prepare/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || "Failed to prepare avatar");
    err.code = data.error_code || "setup_not_prepared";
    err.missingRequirements = Array.isArray(data.missing_requirements) ? data.missing_requirements : [];
    err.readiness = data.readiness || null;
    throw err;
  }
  return data;
}

export async function fetchAvatarPreviewStatus(userId, jobId) {
  const res = await fetch(`${API_BASE_URL}/users/${userId}/avatar/preview/status/${jobId}/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to fetch avatar preview status");
  }
  return res.json();
}

export async function deleteAvatarPreview(userId) {
  const res = await fetch(`${API_BASE_URL}/users/${userId}/avatar/preview/delete/`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to delete avatar preview");
  }
  return res.json();
}

export async function fetchAvatarOverlayPreference(projectId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/avatar-overlay/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to fetch avatar overlay preference");
  }
  return res.json();
}

export async function saveAvatarOverlayPreference(projectId, payload) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/avatar-overlay/`, {
    method: "PUT",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to save avatar overlay preference");
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Catalog (public — no authentication required)
// ---------------------------------------------------------------------------

export async function fetchCatalog(categorySlug = null) {
  const url = categorySlug
    ? `${API_BASE_URL}/catalog/?category=${encodeURIComponent(categorySlug)}`
    : `${API_BASE_URL}/catalog/`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Failed to fetch catalog");
  return res.json();
}

export async function fetchLesson(projectId) {
  const headers = authHeaders();
  const res = await fetch(
    `${API_BASE_URL}/catalog/${projectId}/`,
    Object.keys(headers).length ? { headers } : undefined,
  );
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    const error = new Error(payload.error || 'Failed to fetch lesson');
    error.status = res.status;
    error.reason = payload.reason || '';
    error.payload = payload;
    throw error;
  }
  const data = await res.json();
  return {
    ...data,
    stream_url: toAbsoluteApiUrl(data.stream_url),
    srt_url: toAbsoluteApiUrl(data.srt_url),
    vtt_url: toAbsoluteApiUrl(data.vtt_url || data.subtitle_vtt_url),
    subtitle_vtt_url: toAbsoluteApiUrl(data.subtitle_vtt_url || data.vtt_url),
    avatar_overlay: data.avatar_overlay
      ? {
          ...data.avatar_overlay,
          stream_url: toAbsoluteApiUrl(data.avatar_overlay.stream_url),
        }
      : null,
  };
}

export async function getPlaylistContext(projectId) {
  const headers = authHeaders();
  const res = await fetch(
    `${API_BASE_URL}/catalog/${projectId}/playlist-context/`,
    Object.keys(headers).length ? { headers } : undefined,
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch playlist context"));
  }
  return res.json();
}

export async function fetchCategories() {
  const res = await fetch(`${API_BASE_URL}/categories/`);
  if (!res.ok) throw new Error("Failed to fetch categories");
  return res.json();
}

export async function fetchCatalogFeed(filters = {}) {
  const params = new URLSearchParams();

  if (filters.query) params.set("q", String(filters.query));
  if (filters.category) params.set("category", String(filters.category));
  if (filters.teacherId) params.set("teacher", String(filters.teacherId));
  if (filters.rankBy) params.set("rank_by", String(filters.rankBy));
  if (typeof filters.watchedOnly === "boolean") {
    params.set("watched", filters.watchedOnly ? "1" : "0");
  }
  if (Array.isArray(filters.interests) && filters.interests.length > 0) {
    params.set("interests", filters.interests.join(","));
  }
  if (filters.limit) params.set("limit", String(filters.limit));

  const queryString = params.toString();
  const url = queryString
    ? `${API_BASE_URL}/catalog/feed/?${queryString}`
    : `${API_BASE_URL}/catalog/feed/`;

  const headers = authHeaders();
  const res = await fetch(url, Object.keys(headers).length ? { headers } : undefined);
  if (!res.ok) throw new Error("Failed to fetch feed");
  return res.json();
}

export async function fetchAdminStats(filters = {}) {
  const params = new URLSearchParams();
  if (filters.range) params.set("range", String(filters.range));
  if (filters.from) params.set("from", String(filters.from));
  if (filters.to) params.set("to", String(filters.to));
  if (filters.category) params.set("category", String(filters.category));
  if (filters.teacherId) params.set("teacher", String(filters.teacherId));
  if (filters.sort) params.set("sort", String(filters.sort));

  const query = params.toString();
  const url = query
    ? `${API_BASE_URL}/admin/stats/?${query}`
    : `${API_BASE_URL}/admin/stats/`;

  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to fetch admin statistics");
  }
  return res.json();
}

export async function fetchMyAnalytics(filters = {}) {
  const params = new URLSearchParams();
  if (filters.range) params.set("range", String(filters.range));
  if (filters.from) params.set("from", String(filters.from));
  if (filters.to) params.set("to", String(filters.to));
  if (filters.category) params.set("category", String(filters.category));
  if (filters.sort) params.set("sort", String(filters.sort));

  const query = params.toString();
  const url = query
    ? `${API_BASE_URL}/me/analytics/?${query}`
    : `${API_BASE_URL}/me/analytics/`;

  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || data.detail || "Failed to fetch creator analytics");
  }
  return res.json();
}

function analyticsQueryString(filters = {}) {
  const params = new URLSearchParams();
  if (filters.range) params.set("range", String(filters.range));
  if (filters.from) params.set("from", String(filters.from));
  if (filters.to) params.set("to", String(filters.to));
  if (filters.category) params.set("category", String(filters.category));
  if (filters.sort) params.set("sort", String(filters.sort));
  if (filters.output_language) params.set("output_language", String(filters.output_language));
  if (filters.outputLanguage) params.set("output_language", String(filters.outputLanguage));
  return params.toString();
}

export async function fetchMyAnalyticsIntelligence(filters = {}) {
  const query = analyticsQueryString(filters);
  const url = query
    ? `${API_BASE_URL}/me/analytics/intelligence/?${query}`
    : `${API_BASE_URL}/me/analytics/intelligence/`;

  const res = await fetch(url, { headers: authHeaders() });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, 'Failed to fetch analytics intelligence'));
  }
  return data;
}

export async function analyzeMyAnalyticsIntelligence(filters = {}, options = {}) {
  const query = analyticsQueryString(filters);
  const url = query
    ? `${API_BASE_URL}/me/analytics/intelligence/analyze/?${query}`
    : `${API_BASE_URL}/me/analytics/intelligence/analyze/`;

  const res = await fetch(url, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      output_language: options.outputLanguage || options.output_language || 'auto',
      force: Boolean(options.force),
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, 'Failed to analyze analytics'));
  }
  return data;
}

/**
 * Fetch short-lived playback token for a project.
 * Returns { video_url, srt_url, vtt_url, expires_in }
 * The video_url is a /api/v1/stream/<token>/ URL — never a raw storage path.
 */
export async function fetchPlaybackToken(projectId) {
  const headers = authHeaders();
  const res = await fetch(
    `${API_BASE_URL}/projects/${projectId}/playback-token/`,
    Object.keys(headers).length ? { headers } : undefined,
  );
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    const error = new Error(payload.error || 'Failed to get playback token');
    error.status = res.status;
    error.reason = payload.reason || '';
    error.payload = payload;
    throw error;
  }
  const data = await res.json();
  const drmSystems = data.drm?.systems
    ? Object.fromEntries(
        Object.entries(data.drm.systems).map(([name, system]) => [
          name,
          {
            ...system,
            license_url: toAbsoluteApiUrl(system.license_url),
            certificate_url: toAbsoluteApiUrl(system.certificate_url),
          },
        ])
      )
    : null;
  const streaming = data.streaming
    ? {
        ...data.streaming,
        fallback: data.streaming.fallback
          ? {
              ...data.streaming.fallback,
              url: toAbsoluteApiUrl(data.streaming.fallback.url),
            }
          : null,
        hls: data.streaming.hls
          ? {
              ...data.streaming.hls,
              manifest_url: toAbsoluteApiUrl(data.streaming.hls.manifest_url),
            }
          : null,
      }
    : null;

  return {
    ...data,
    video_url: toAbsoluteApiUrl(data.video_url),
    srt_url: toAbsoluteApiUrl(data.srt_url),
    vtt_url: toAbsoluteApiUrl(data.vtt_url || data.subtitle_vtt_url),
    subtitle_vtt_url: toAbsoluteApiUrl(data.subtitle_vtt_url || data.vtt_url),
    streaming,
    drm: data.drm
      ? {
          ...data.drm,
          license_url: toAbsoluteApiUrl(data.drm.license_url),
          certificate_url: toAbsoluteApiUrl(data.drm.certificate_url),
          manifest_url: toAbsoluteApiUrl(data.drm.manifest_url),
          systems: drmSystems,
        }
      : null,
    avatar_overlay: data.avatar_overlay
      ? {
          ...data.avatar_overlay,
          stream_url: toAbsoluteApiUrl(data.avatar_overlay.stream_url),
        }
      : null,
  };
}

/**
 * GET /api/v1/projects/<id>/studio-preview-token/
 * Authenticated owner/staff only preview for draft lessons.
 */
export async function fetchStudioPreviewToken(projectId) {
  const res = await fetch(
    `${API_BASE_URL}/projects/${projectId}/studio-preview-token/`,
    { headers: authHeaders() },
  );
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    const error = new Error(payload.error || 'Failed to get studio preview token');
    error.status = res.status;
    error.reason = payload.reason || '';
    error.payload = payload;
    throw error;
  }
  const data = await res.json();
  return {
    ...data,
    video_url: toAbsoluteApiUrl(data.video_url),
    stream_url: toAbsoluteApiUrl(data.video_url),
    srt_url: toAbsoluteApiUrl(data.srt_url),
    vtt_url: toAbsoluteApiUrl(data.vtt_url || data.subtitle_vtt_url),
    subtitle_vtt_url: toAbsoluteApiUrl(data.subtitle_vtt_url || data.vtt_url),
    avatar_token: data.avatar_token,
    avatar_overlay: data.avatar_overlay
      ? {
          ...data.avatar_overlay,
          stream_url: toAbsoluteApiUrl(data.avatar_overlay.stream_url),
        }
      : null,
  };
}

export async function heartbeatPlaybackSession(projectId, visibility = 'visible') {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/playback-session/heartbeat/`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ visibility }),
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(data.error || data.reason || 'Playback session is not active.');
    error.status = res.status;
    error.reason = data.reason || '';
    error.payload = data;
    throw error;
  }
  return data;
}

export async function fetchProjectTranscript(projectId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/transcript/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || 'Failed to fetch project transcript');
  }
  return res.json();
}

export async function fetchProjectLessonIntelligence(projectId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/intelligence/`, {
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, 'Failed to fetch lesson intelligence'));
  }
  return data;
}

export async function analyzeProjectLessonIntelligence(projectId, options = {}) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/intelligence/analyze/`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      output_language: options.outputLanguage || options.output_language || 'auto',
      force: Boolean(options.force),
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, 'Failed to analyze lesson'));
  }
  return data;
}

export async function updateProjectTranscript(projectId, pages, options = {}) {
  const body = {
    pages,
    trigger_rerender: Boolean(options.triggerRerender),
    draft_only: options.draftOnly ?? true,
    pause_sec: options.pauseSec ?? 2.2,
    lang_hint: options.langHint ?? 'auto',
  };
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/transcript/`, {
    method: 'PATCH',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(payload, 'Failed to save transcript edits'));
  }
  return res.json();
}

export async function discardProjectDraft(projectId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/draft/discard/`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || data.detail || 'Failed to discard draft');
  }
  return data;
}

export async function promoteProjectDraft(projectId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/draft/promote/`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw apiError(data, 'Failed to save draft changes');
  }
  return data;
}

export async function updateTranscriptPageScene(projectId, pageId, payload = {}) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/transcript-pages/${pageId}/scene/`, {
    method: 'PATCH',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ ...(payload || {}), draft_only: payload?.draft_only ?? true }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, 'Failed to update scene background settings'));
  }
  return data;
}

export async function previewTranscriptPageHighlight(projectId, pageId, payload = {}) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/transcript-pages/${pageId}/highlight-preview/`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ ...(payload || {}), draft_only: payload?.draft_only ?? true }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, 'Failed to preview highlight'));
  }
  return data;
}

export async function uploadTranscriptPageBackground(projectId, pageId, file, options = {}) {
  const formData = new FormData();
  formData.append('background_file', file);
  formData.append('draft_only', String(options.draftOnly ?? true));
  if (options.backgroundFit) formData.append('background_fit', options.backgroundFit);
  if (options.textScale !== undefined) formData.append('text_scale', String(options.textScale));
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/transcript-pages/${pageId}/background/`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, 'Failed to upload slide background'));
  }
  return data;
}

export async function applyProjectBackgroundToAll(projectId, payload = {}) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/background/apply-all/`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ ...(payload || {}), draft_only: payload?.draft_only ?? true }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, 'Failed to apply background to all slides'));
  }
  return data;
}

export async function uploadProjectCover(projectId, file, options = {}) {
  const formData = new FormData();
  formData.append('cover_file', file);
  formData.append('draft_only', String(options.draftOnly ?? true));
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/cover/`, {
    method: 'POST',
    headers: authHeaders(),
    body: formData,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, 'Failed to update lesson cover'));
  }
  return data;
}

export async function transcriptPageAction(projectId, payload = {}) {
  const draftOnly = payload.draft_only ?? true;
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/transcript/actions/`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ ...(payload || {}), draft_only: draftOnly }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const details = data.details || data.detail;
    const detailText = typeof details === 'string' ? details : details ? JSON.stringify(details) : '';
    throw new Error(data.error || detailText || 'Failed to update transcript page structure');
  }
  return data;
}

// ---------------------------------------------------------------------------
// Student social features (authentication required)
// ---------------------------------------------------------------------------

export async function toggleLike(projectId) {
  const res = await fetch(`${API_BASE_URL}/catalog/${projectId}/like/`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("Failed to toggle like");
  return res.json();
}

export async function saveProgress(projectId, progressPct) {
  const res = await fetch(`${API_BASE_URL}/catalog/${projectId}/progress/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ progress_pct: progressPct }),
  });
  if (!res.ok) throw new Error("Failed to save progress");
  return res.json();
}

export async function fetchUserHistory() {
  const res = await fetch(`${API_BASE_URL}/me/history/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch watch history"));
  }
  return res.json();
}

export async function fetchLikedLessons() {
  const res = await fetch(`${API_BASE_URL}/me/liked-lessons/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch liked lessons"));
  }
  return res.json();
}

export async function getFollowingPublishers() {
  const res = await fetch(`${API_BASE_URL}/me/following/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch followed publishers"));
  }
  return res.json();
}

export async function getPublisherProfile(userId) {
  const res = await fetch(`${API_BASE_URL}/users/${userId}/profile/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch publisher profile"));
  }
  return res.json();
}

export async function getPublisherLessons(userId, params = {}) {
  const query = new URLSearchParams();
  if (params.sort) query.set("sort", params.sort);
  if (params.order) query.set("order", params.order);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const res = await fetch(`${API_BASE_URL}/users/${userId}/lessons/${suffix}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch publisher lessons"));
  }
  return res.json();
}

export async function listPlaylists() {
  const res = await fetch(`${API_BASE_URL}/playlists/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch playlists"));
  }
  return res.json();
}

export async function createPlaylist(payload = {}) {
  const res = await fetch(`${API_BASE_URL}/playlists/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to create playlist"));
  }
  return data;
}

export async function updatePlaylist(id, payload = {}) {
  const res = await fetch(`${API_BASE_URL}/playlists/${id}/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to update playlist"));
  }
  return data;
}

export async function deletePlaylist(id) {
  const res = await fetch(`${API_BASE_URL}/playlists/${id}/`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to delete playlist"));
  }
  return true;
}

export async function addPlaylistItem(id, projectId) {
  const res = await fetch(`${API_BASE_URL}/playlists/${id}/items/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ project_id: projectId }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to add lesson to playlist"));
  }
  return data;
}

export async function removePlaylistItem(id, projectId) {
  const res = await fetch(`${API_BASE_URL}/playlists/${id}/items/${projectId}/`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to remove lesson from playlist"));
  }
  return true;
}

export async function reorderPlaylistItems(id, projectIds = []) {
  const res = await fetch(`${API_BASE_URL}/playlists/${id}/items/reorder/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ project_ids: projectIds }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to reorder playlist"));
  }
  return data;
}

export async function getPublisherPlaylists(userId) {
  const headers = authHeaders();
  const res = await fetch(
    `${API_BASE_URL}/users/${userId}/playlists/`,
    Object.keys(headers).length ? { headers } : undefined,
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch publisher playlists"));
  }
  return res.json();
}

export async function getPlaylist(id) {
  const headers = authHeaders();
  const res = await fetch(
    `${API_BASE_URL}/playlists/${id}/`,
    Object.keys(headers).length ? { headers } : undefined,
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch playlist"));
  }
  return res.json();
}

export async function toggleSavePlaylist(id) {
  const res = await fetch(`${API_BASE_URL}/playlists/${id}/save/`, {
    method: "POST",
    headers: authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(apiErrorMessage(data, "Failed to update saved playlist"));
  }
  return data;
}

export async function getSavedPlaylists() {
  const res = await fetch(`${API_BASE_URL}/me/saved-playlists/`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to fetch saved playlists"));
  }
  return res.json();
}

export async function toggleFollowPublisher(userId) {
  const res = await fetch(`${API_BASE_URL}/users/${userId}/follow/`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(apiErrorMessage(data, "Failed to update follow"));
  }
  return res.json();
}

export async function fetchComments(projectId) {
  const res = await fetch(`${API_BASE_URL}/catalog/${projectId}/comments/`);
  if (!res.ok) throw new Error("Failed to fetch comments");
  return res.json();
}

export async function addComment(projectId, text) {
  const res = await fetch(`${API_BASE_URL}/catalog/${projectId}/comments/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to add comment");
  }
  return res.json();
}

function normalizeSubtitleTrack(track) {
  if (!track || typeof track !== 'object') return null;

  const rawCode = String(track.language_code || track.lang || '').trim().toLowerCase();
  const isOriginal = track.is_original === true
    || track.type === 'original'
    || track.id === 'original'
    || rawCode === 'original';
  const languageCode = isOriginal ? 'original' : rawCode;
  const vttUrl = toAbsoluteApiUrl(track.vtt_url || track.subtitle_vtt_url || '');
  const srtUrl = toAbsoluteApiUrl(track.srt_url || track.subtitle_url || '');
  const status = String(track.status || '').trim().toLowerCase();

  if (!languageCode) return null;

  return {
    ...track,
    language_code: languageCode,
    language_label: isOriginal
      ? 'Original'
      : String(track.language_label || track.label || languageCode.toUpperCase()).trim(),
    source_language_code: String(track.source_language_code || '').trim().toLowerCase(),
    status: status || 'ready',
    is_original: isOriginal,
    vtt_url: vttUrl,
    srt_url: srtUrl,
  };
}

function normalizeSubtitleRequestLanguage(language) {
  if (!language || typeof language !== 'object') return null;
  const code = String(language.language_code || language.code || '').trim().toLowerCase();
  const label = String(language.language_label || language.label || code.toUpperCase()).trim();
  if (!code || !label) return null;
  return { code, label, language_code: code, language_label: label };
}

export async function fetchSubtitleTrackBundle(projectId) {
  const safeProjectId = Number(projectId || 0);
  if (!safeProjectId) {
    return { tracks: [], requestableLanguages: [] };
  }

  const headers = authHeaders();
  const res = await fetch(
    `${API_BASE_URL}/projects/${safeProjectId}/subtitle-tracks/`,
    Object.keys(headers).length ? { headers } : undefined
  );
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || 'Failed to fetch subtitle tracks');
  }
  const data = await res.json();
  const tracks = Array.isArray(data) ? data : (Array.isArray(data.tracks) ? data.tracks : []);
  const requestableLanguages = Array.isArray(data?.requestable_languages)
    ? data.requestable_languages
    : [];
  return {
    tracks: tracks.map(normalizeSubtitleTrack).filter(Boolean),
    requestableLanguages: requestableLanguages.map(normalizeSubtitleRequestLanguage).filter(Boolean),
  };
}

export async function fetchSubtitleTracks(projectId) {
  const bundle = await fetchSubtitleTrackBundle(projectId);
  return bundle.tracks;
}

export async function generateSubtitleTrack(projectId, payload = {}) {
  const safeProjectId = Number(projectId || 0);
  if (!safeProjectId) throw new Error('Project id is required.');

  const res = await fetch(`${API_BASE_URL}/projects/${safeProjectId}/subtitle-tracks/`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(payload || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || 'Failed to generate subtitle track');
  }
  const track = normalizeSubtitleTrack(data.track) || data.track || {};
  return {
    ...track,
    request_status: res.status,
    already_available: Boolean(data.already_available),
    details: data.details || '',
    task_id: data.task_id || '',
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Extract job_id and project_id from the POST /projects/ response.
 * JobSerializer now includes project_id directly.
 */
export function extractJobInfo(data) {
  if (!data) return {};
  if (data.id && data.project_id !== undefined) {
    return { job_id: data.id, project_id: data.project_id };
  }
  if (data.id && (data.status || data.celery_task_id)) {
    return {
      job_id: data.id,
      project_id: data.project?.id ?? data.project ?? data.project_id,
    };
  }
  return { project_id: data.project_id, job_id: data.job_id };
}
