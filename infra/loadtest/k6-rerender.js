import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_BASE = `${BASE_URL}/api/v1`;
const TOKEN = __ENV.TOKEN || '';
const PROJECT_IDS = (__ENV.PROJECT_IDS || '')
  .split(',')
  .map((s) => Number(String(s).trim()))
  .filter((v) => Number.isFinite(v) && v > 0);
const PROFILE = __ENV.RENDER_PROFILE || 'fast';

if (!TOKEN) {
  throw new Error('TOKEN env is required.');
}
if (!PROJECT_IDS.length) {
  throw new Error('PROJECT_IDS env is required, e.g. PROJECT_IDS=12,13,14');
}

export const options = {
  scenarios: {
    burst_rerender: {
      executor: 'ramping-vus',
      startVUs: 5,
      stages: [
        { duration: '30s', target: 50 },
        { duration: '1m', target: 200 },
        { duration: '1m', target: 400 },
        { duration: '30s', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.10'],
    http_req_duration: ['p(95)<2000'],
  },
};

function pickProjectId() {
  const idx = Math.floor(Math.random() * PROJECT_IDS.length);
  return PROJECT_IDS[idx];
}

export default function () {
  const projectId = pickProjectId();
  const requestId = `k6_${Date.now()}_${__VU}_${__ITER}`;
  const body = JSON.stringify({
    render_profile: PROFILE,
    request_id: requestId,
    avatar_enabled: '0',
  });

  const res = http.post(`${API_BASE}/projects/${projectId}/rerender/`, body, {
    headers: {
      Authorization: `Token ${TOKEN}`,
      'Content-Type': 'application/json',
    },
  });

  check(res, {
    'status is 202 or 429': (r) => r.status === 202 || r.status === 429,
  });

  sleep(0.2);
}
