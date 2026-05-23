import { describe, expect, it } from "vitest";
import { buildRetryErrorMessage, buildRetrySuccessMessage, isRetryVisibleForStatus } from "./retryUi";

describe("retry UI visibility", () => {
  it("shows retry for failed", () => {
    expect(isRetryVisibleForStatus("failed")).toBe(true);
  });

  it("shows retry for cancelled", () => {
    expect(isRetryVisibleForStatus("cancelled")).toBe(true);
  });

  it("hides retry for running", () => {
    expect(isRetryVisibleForStatus("running")).toBe(false);
  });

  it("hides retry for done", () => {
    expect(isRetryVisibleForStatus("done")).toBe(false);
  });
});

describe("retry UI feedback", () => {
  it("builds retry success feedback", () => {
    const message = buildRetrySuccessMessage({ id: 99, retried_from_job_id: 41, queue: "render_fast" }, 41);
    expect(message).toContain("Retry queued on render_fast");
    expect(message).toContain("New job #99");
    expect(message).toContain("from #41");
  });

  it("builds retry error feedback", () => {
    const message = buildRetryErrorMessage(new Error("queue overloaded"));
    expect(message).toBe("Retry error: queue overloaded");
  });
});
