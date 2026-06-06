import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://api:8000";
const API_BASE = `${BASE_URL}/api/v1`;
const TOKEN = __ENV.TOKEN || "";
const PROJECT_IDS = (__ENV.PROJECT_IDS || "")
  .split(",")
  .map((v) => Number(String(v).trim()))
  .filter((v) => Number.isFinite(v) && v > 0);
const METRICS_TOKEN = __ENV.METRICS_TOKEN || "";

const PROFILE = (__ENV.LOAD_PROFILE || "smoke").trim().toLowerCase();
const HEAVY = PROFILE === "heavy";

const HAS_REQUIRED_CONFIG = Boolean(TOKEN) && PROJECT_IDS.length > 0;

export const options = {
  scenarios: {
    playback_token_flow: {
      executor: "constant-vus",
      vus: HEAVY ? 40 : 5,
      duration: HEAVY ? "5m" : "45s",
      exec: "playbackTokenFlow",
    },
    subtitle_request_flow: {
      executor: "constant-vus",
      vus: HEAVY ? 25 : 3,
      duration: HEAVY ? "5m" : "45s",
      exec: "subtitleRequestFlow",
    },
    render_enqueue_flow: {
      executor: "constant-vus",
      vus: HEAVY ? 35 : 4,
      duration: HEAVY ? "5m" : "45s",
      exec: "renderEnqueueFlow",
    },
    studio_open_load_flow: {
      executor: "constant-vus",
      vus: HEAVY ? 20 : 3,
      duration: HEAVY ? "5m" : "45s",
      exec: "studioOpenLoadFlow",
    },
    metrics_endpoint_flow: {
      executor: "constant-vus",
      vus: 1,
      duration: HEAVY ? "5m" : "30s",
      exec: "metricsEndpointFlow",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.12"],
    http_req_duration: ["p(95)<3000"],
  },
};

function authHeaders() {
  return {
    Authorization: `Token ${TOKEN}`,
    "Content-Type": "application/json",
  };
}

function pickProjectId() {
  return PROJECT_IDS[Math.floor(Math.random() * PROJECT_IDS.length)];
}

export function playbackTokenFlow() {
  if (!HAS_REQUIRED_CONFIG) {
    sleep(0.2);
    return;
  }
  const id = pickProjectId();
  const res = http.get(`${API_BASE}/projects/${id}/playback-token/`);
  check(res, { "playback token status": (r) => r.status === 200 || r.status === 404 || r.status === 403 });
  sleep(0.15);
}

export function subtitleRequestFlow() {
  if (!HAS_REQUIRED_CONFIG) {
    sleep(0.2);
    return;
  }
  const id = pickProjectId();
  const res = http.get(`${API_BASE}/projects/${id}/subtitle-tracks/`, { headers: authHeaders() });
  check(res, { "subtitle flow status": (r) => r.status === 200 || r.status === 404 || r.status === 403 });
  sleep(0.2);
}

export function renderEnqueueFlow() {
  if (!HAS_REQUIRED_CONFIG) {
    sleep(0.2);
    return;
  }
  const id = pickProjectId();
  const payload = JSON.stringify({
    render_profile: "fast",
    request_id: `k6-${Date.now()}-${__VU}-${__ITER}`,
    avatar_enabled: "0",
  });
  const res = http.post(`${API_BASE}/projects/${id}/rerender/`, payload, { headers: authHeaders() });
  check(res, { "rerender status": (r) => r.status === 202 || r.status === 429 || r.status === 403 });
  sleep(0.2);
}

export function studioOpenLoadFlow() {
  if (!HAS_REQUIRED_CONFIG) {
    sleep(0.2);
    return;
  }
  const id = pickProjectId();
  const res = http.get(`${API_BASE}/projects/${id}/transcript/`, { headers: authHeaders() });
  check(res, { "studio open status": (r) => r.status === 200 || r.status === 404 || r.status === 403 });
  sleep(0.2);
}

export function metricsEndpointFlow() {
  const headers = METRICS_TOKEN ? { "X-Metrics-Token": METRICS_TOKEN } : {};
  const res = http.get(`${API_BASE}/system/metrics/prometheus/`, { headers });
  check(res, { "metrics endpoint status": (r) => r.status === 200 || r.status === 401 });
  sleep(0.3);
}
