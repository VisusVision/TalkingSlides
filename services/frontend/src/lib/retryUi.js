export function isRetryVisibleForStatus(status) {
  const normalized = String(status || "").trim().toLowerCase();
  return normalized === "failed" || normalized === "cancelled";
}

export function buildRetrySuccessMessage(payload, fallbackJobId) {
  const queue = String(payload?.queue || "").trim();
  const retriedFrom = Number(payload?.retried_from_job_id || fallbackJobId || 0);
  const retriedJobId = Number(payload?.id || 0);
  return `Retry queued${queue ? ` on ${queue}` : ""}. New job #${retriedJobId || "?"} (from #${retriedFrom || "?"}).`;
}

export function buildRetryErrorMessage(error) {
  return `Retry error: ${error?.message || "Retry failed."}`;
}
