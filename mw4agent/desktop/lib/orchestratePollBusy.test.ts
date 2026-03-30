import { describe, expect, it } from "vitest";
import { busyFromOrchestrateStatus } from "./orchestratePollBusy";

describe("busyFromOrchestrateStatus", () => {
  it("running -> busy", () => {
    expect(busyFromOrchestrateStatus("running")).toBe(true);
    expect(busyFromOrchestrateStatus("  RUNNING  ")).toBe(true);
  });

  it("idle / error / aborted -> not busy", () => {
    expect(busyFromOrchestrateStatus("idle")).toBe(false);
    expect(busyFromOrchestrateStatus("error")).toBe(false);
    expect(busyFromOrchestrateStatus("aborted")).toBe(false);
  });

  it("empty or missing status -> not busy (regression: must clear stuck busy)", () => {
    expect(busyFromOrchestrateStatus("")).toBe(false);
    expect(busyFromOrchestrateStatus("   ")).toBe(false);
    expect(busyFromOrchestrateStatus(null)).toBe(false);
    expect(busyFromOrchestrateStatus(undefined)).toBe(false);
  });

  it("unknown status strings -> not busy", () => {
    expect(busyFromOrchestrateStatus("accepted")).toBe(false);
    expect(busyFromOrchestrateStatus("weird")).toBe(false);
  });
});
