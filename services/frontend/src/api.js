export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api/v1").replace(/\/+$/, "");
const API_ORIGIN = API_BASE_URL.replace(/\/api\/v1\/?$/, "");
const AUTH_USER_STORAGE_KEY = "auth_user";

function randomRequestId(prefix = "req") {
  const cryptoApi = globalThis?.crypto;
  if (cryptoApi?.randomUUID) {
    return `${prefix}_${cryptoApi.randomUUID()}`;
  }
  if (cryptoApi?.getRandomValues) {
    const bytes = new Uint8Array(16);
    cryptoApi.getRandomValues(bytes);
    const hex = Array.from(bytes, (part) => part.toString(16).padStart(2, "0")).join("");
    return `${prefix}_${hex}`;
  }
  const salt = Math.random().toString(36).slice(2, 10);
  return `${prefix}_${Date.now()}_${salt}`;
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

export async function fetchAuthenticatedObjectUrl(url) {
  const absoluteUrl = toAbsoluteApiUrl(String(url || "").trim());
  if (!absoluteUrl) return "";
  const res = await fetch(absoluteUrl, { headers: authHeaders() });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || `Failed to fetch file (${res.status})`);
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

export async function fetchProjects() {
  const res = await fetch(`${API_BASE_URL}/projects/`, { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to fetch projects");
  return res.json();
}

export async function createProject(formData) {
  if (formData instanceof FormData && !formData.get("request_id")) {
    formData.append("request_id", randomRequestId("project_create"));
  }
  const res = await fetch(`${API_BASE_URL}/projects/`, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const message = data.error || data.detail || "Upload failed";
    const error = new Error(message);
    error.payload = data;
    error.status = res.status;
    throw error;
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
  body.request_id = options.requestId || randomRequestId("project_rerender");
  if (Object.prototype.hasOwnProperty.call(options, "avatarEnabled")) {
    body.avatar_enabled = options.avatarEnabled ? "1" : "0";
  }
  if (options.renderProfile) {
    body.render_profile = String(options.renderProfile);
  }
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/rerender/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    const message = data.error || data.detail || "Failed to rerender project";
    const error = new Error(message);
    error.payload = data;
    error.status = res.status;
    throw error;
  }
  return res.json();
}

export async function fetchRenderCapacity() {
  const res = await fetch(`${API_BASE_URL}/system/render-capacity/`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error("Failed to fetch render capacity");
  return res.json();
}

export async function updateProjectTtsSettings(projectId, ttsSettings) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ tts_settings: ttsSettings }),
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

export async function updateProjectPublished(projectId, isPublished) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/`, {
    method: "PATCH",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ is_published: Boolean(isPublished) }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to update project publication state");
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export async function fetchJobStatus(projectId, jobId, options = {}) {
  const params = new URLSearchParams();
  params.set('response_schema', String(options.responseSchema || 'light_v1'));
  if (options.includeTranscriptPages) params.set('include_transcript_pages', '1');
  if (options.includeLanguageDetection) params.set('include_language_detection', '1');
  const qs = params.toString();
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/jobs/${jobId}/${qs ? `?${qs}` : ''}`, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    if (res.status === 404) return { notfound: true };
    throw new Error("Failed to fetch job status");
  }
  return res.json();
}

export async function cancelJob(projectId, jobId, reason = "") {
  const body = reason ? { reason: String(reason) } : {};
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/jobs/${jobId}/cancel/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = data.error || "Failed to cancel job";
    const error = new Error(message);
    error.status = res.status;
    error.payload = data;
    throw error;
  }
  return data;
}

export async function retryJob(projectId, jobId, options = {}) {
  const body = {
    request_id: options.requestId || randomRequestId("job_retry"),
  };
  if (options.renderProfile) body.render_profile = String(options.renderProfile);
  if (options.langHint) body.lang_hint = String(options.langHint);
  if (Number.isFinite(Number(options.pauseSec))) body.pause_sec = Number(options.pauseSec);
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/jobs/${jobId}/retry/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = data.error || "Failed to retry job";
    const error = new Error(message);
    error.status = res.status;
    error.payload = data;
    throw error;
  }
  return data;
}

export async function createJobEventsTicket(projectId, jobId) {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/jobs/${jobId}/events/ticket/`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = data.error || "Failed to create SSE stream ticket";
    const error = new Error(message);
    error.status = res.status;
    error.payload = data;
    throw error;
  }
  return data;
}

export function subscribeJobStatusEvents(projectId, jobId, handlers = {}) {
  let reconnectAttempts = 0;
  let closedByCaller = false;
  let reconnectTimer = null;
  let stream = null;
  let lastEventId = '';
  let streamTicket = '';
  let ticketExpiresAtMs = 0;

  const onStatus = typeof handlers.onStatus === "function" ? handlers.onStatus : null;
  const onError = typeof handlers.onError === "function" ? handlers.onError : null;
  const onClose = typeof handlers.onClose === "function" ? handlers.onClose : null;
  const maxReconnectAttempts = Number.isFinite(Number(handlers.maxReconnectAttempts))
    ? Math.max(0, Number(handlers.maxReconnectAttempts))
    : 5;
  const reconnectBaseMs = Number.isFinite(Number(handlers.reconnectBaseMs))
    ? Math.max(500, Number(handlers.reconnectBaseMs))
    : 1500;

  const buildUrl = () => {
    const params = new URLSearchParams();
    if (streamTicket) params.set("stream_ticket", streamTicket);
    if (lastEventId) params.set("last_event_id", lastEventId);
    return `${API_BASE_URL}/projects/${projectId}/jobs/${jobId}/events/${params.toString() ? `?${params.toString()}` : ""}`;
  };

  const ensureTicket = async () => {
    const now = Date.now();
    if (streamTicket && now < ticketExpiresAtMs - 5000) {
      return streamTicket;
    }
    const payload = await createJobEventsTicket(projectId, jobId);
    streamTicket = String(payload?.stream_ticket || '').trim();
    const ttl = Number(payload?.expires_in || 0);
    ticketExpiresAtMs = now + Math.max(10, ttl) * 1000;
    return streamTicket;
  };

  const closeActiveStream = () => {
    if (stream) {
      stream.close();
      stream = null;
    }
  };

  const scheduleReconnect = () => {
    if (closedByCaller) return;
    if (reconnectAttempts >= maxReconnectAttempts) {
      if (onError) onError();
      return;
    }
    reconnectAttempts += 1;
    const delayMs = reconnectBaseMs * reconnectAttempts;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      openStream();
    }, delayMs);
  };

  const openStream = async () => {
    closeActiveStream();
    try {
      await ensureTicket();
    } catch {
      scheduleReconnect();
      return;
    }
    stream = new EventSource(buildUrl(), { withCredentials: true });

    stream.addEventListener("job_status", (event) => {
      lastEventId = event?.lastEventId || lastEventId;
      reconnectAttempts = 0;
      if (!onStatus) return;
      try {
        const payload = JSON.parse(event.data || "{}");
        onStatus(payload);
      } catch {
        // ignore malformed chunks
      }
    });

    stream.addEventListener("heartbeat", (event) => {
      lastEventId = event?.lastEventId || lastEventId;
      reconnectAttempts = 0;
    });

    stream.addEventListener("job_deleted", () => {
      if (onClose) onClose();
      closeActiveStream();
    });

    stream.onerror = () => {
      closeActiveStream();
      scheduleReconnect();
    };
  };

  void openStream();

  return () => {
    closedByCaller = true;
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    closeActiveStream();
  };
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
  if (!userId) {
    const resCompat = await fetch(`${API_BASE_URL}/avatar/profile`, {
      headers: authHeaders(),
    });
    if (!resCompat.ok) {
      const data = await resCompat.json().catch(() => ({}));
      throw new Error(data.error || "Failed to fetch avatar profile");
    }
    return resCompat.json();
  }
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

  const targetUrl = userId ? `${API_BASE_URL}/users/${userId}/avatar/` : `${API_BASE_URL}/avatar/upload`;
  const res = await fetch(targetUrl, {
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

  const targetUrl = userId ? `${API_BASE_URL}/users/${userId}/avatar/` : `${API_BASE_URL}/avatar/upload`;
  const res = await fetch(targetUrl, {
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
  const targetUrl = userId ? `${API_BASE_URL}/users/${userId}/avatar/` : `${API_BASE_URL}/avatar/profile`;
  const res = await fetch(targetUrl, {
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
  const targetUrl = userId ? `${API_BASE_URL}/users/${userId}/avatar/preview/` : `${API_BASE_URL}/avatar/preview`;
  const res = await fetch(targetUrl, {
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
  const targetUrl = userId ? `${API_BASE_URL}/users/${userId}/avatar/prepare/` : `${API_BASE_URL}/avatar/profile`;
  const method = userId ? "POST" : "POST";
  const res = await fetch(targetUrl, {
    method,
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
  const targetUrl = userId
    ? `${API_BASE_URL}/users/${userId}/avatar/preview/status/${jobId}/`
    : `${API_BASE_URL}/avatar/preview/${jobId}`;
  const res = await fetch(targetUrl, {
    headers: authHeaders(),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error || "Failed to fetch avatar preview status");
  }
  return res.json();
}

export async function deleteAvatarPreview(userId) {
  const targetUrl = userId ? `${API_BASE_URL}/users/${userId}/avatar/preview/delete/` : `${API_BASE_URL}/avatar/profile`;
  const method = userId ? "DELETE" : "DELETE";
  const res = await fetch(targetUrl, {
    method,
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

export async function heartbeatPlaybackSession(projectId, visibility = 'visible') {
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/playback-session/heartbeat/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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

export async function updateProjectTranscript(projectId, pages, options = {}) {
  const body = {
    request_id: options.requestId || randomRequestId("transcript_update"),
    pages,
    trigger_rerender: Boolean(options.triggerRerender),
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
    throw new Error(payload.error || 'Failed to save transcript edits');
  }
  return res.json();
}

export async function transcriptPageAction(projectId, payload = {}) {
  const bodyPayload = {
    ...payload,
    request_id: payload?.request_id || randomRequestId("transcript_action"),
  };
  const res = await fetch(`${API_BASE_URL}/projects/${projectId}/transcript/actions/`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(bodyPayload),
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
