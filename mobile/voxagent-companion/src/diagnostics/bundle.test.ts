import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("expo-constants", () => ({
  default: {
    executionEnvironment: "standalone",
    expoConfig: {
      version: "4.1.0",
      ios: { buildNumber: "410001" },
    },
    platform: {
      ios: { buildNumber: "410001" },
    },
  },
}));

vi.mock("react-native", () => ({
  Platform: {
    OS: "ios",
    Version: "18.5",
    isPad: false,
    isTV: false,
    constants: {},
  },
}));

import {
  createMobileDiagnosticBundle,
  scanMobileDiagnosticBundle,
  serializeMobileDiagnosticBundle,
  type MobileDiagnosticBundle,
} from "./bundle";
import {
  clearRequestDiagnosticsForTests,
  recordRequestDiagnostic,
} from "./runtime";

describe("privacy-safe Mobile diagnostic bundle", () => {
  beforeEach(clearRequestDiagnosticsForTests);

  it("contains only build, device class, endpoint class, outcome, and correlation metadata", () => {
    recordRequestDiagnostic({
      correlationId: "mobile-request_123",
      endpointOrigin: "https://engine.example.test/private/session/path",
      networkState: "reachable",
      error: {
        code: "REQUEST_REJECTED",
        message: "private transcript",
        prompt: "private prompt",
        credential: "private credential",
      },
      now: new Date("2026-07-26T12:00:00Z"),
    });

    const bundle = createMobileDiagnosticBundle(
      new Date("2026-07-26T12:01:00Z")
    );
    const serialized = serializeMobileDiagnosticBundle(bundle);

    expect(bundle).toMatchObject({
      app: {
        version: "4.1.0",
        buildVersion: "410001",
        executionEnvironment: "standalone",
      },
      device: {
        os: "ios",
        osVersion: "18.5",
        deviceClass: "phone",
      },
      latest: {
        networkState: "reachable",
        endpointClass: "remote-https",
        errorCode: "REQUEST_REJECTED",
        correlationId: "mobile-request_123",
      },
      crashReporting: "not-configured-manual-diagnostics-only",
    });
    expect(scanMobileDiagnosticBundle(bundle)).toEqual([]);
    expect(serialized).not.toContain("engine.example.test");
    expect(serialized).not.toContain("private/session");
    expect(serialized).not.toContain("private transcript");
    expect(serialized).not.toContain("private prompt");
    expect(serialized).not.toContain("private credential");
  });

  it("flags a tampered bundle with a secret assignment or unbounded metadata", () => {
    const bundle = createMobileDiagnosticBundle();
    bundle.recentRequests.push({
      occurredAt: "2026-07-26T12:00:00Z",
      correlationId: "bad id with spaces",
      endpointClass: "remote-https",
      networkState: "reachable",
      errorCode: "unsafe code",
    });
    bundle.app.version = "password=private-value";

    expect(scanMobileDiagnosticBundle(bundle)).toEqual([
      "invalid_bundle_shape",
      "sensitive_content_pattern",
      "invalid_correlation_id",
      "invalid_error_code",
    ]);
  });

  it("rejects unexpected fields before opening the share sheet", () => {
    const bundle = createMobileDiagnosticBundle() as MobileDiagnosticBundle & {
      transcript?: string;
    };
    bundle.transcript = "unlabelled private content";

    expect(scanMobileDiagnosticBundle(bundle)).toContain(
      "invalid_bundle_shape"
    );
  });
});
