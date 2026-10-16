import Constants from "expo-constants";
import { Platform } from "react-native";

import {
  getRequestDiagnostics,
  type DiagnosticEndpointClass,
  type DiagnosticNetworkState,
  type RequestDiagnostic,
} from "./runtime";

export interface MobileDiagnosticBundle {
  schema: "voxagent.mobile.diagnostics.v1";
  generatedAt: string;
  app: {
    version: string;
    buildVersion: string;
    executionEnvironment: string;
  };
  device: {
    os: string;
    osVersion: string;
    deviceClass: "phone" | "tablet" | "desktop" | "tv" | "unknown";
  };
  latest: {
    networkState: DiagnosticNetworkState | "unknown";
    endpointClass: DiagnosticEndpointClass | "unknown";
    errorCode: string;
    correlationId: string;
  };
  recentRequests: RequestDiagnostic[];
  crashReporting: "not-configured-manual-diagnostics-only";
}

const SAFE_TEXT = /^[A-Za-z0-9._+-]{1,64}$/;
const SAFE_TIMESTAMP =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/;
const NETWORK_STATES = new Set(["reachable", "offline"]);
const ENDPOINT_CLASSES = new Set([
  "loopback",
  "private-lan",
  "remote-https",
  "remote-http",
  "invalid",
]);
const DEVICE_CLASSES = new Set([
  "phone",
  "tablet",
  "desktop",
  "tv",
  "unknown",
]);

function hasExactKeys(value: unknown, keys: readonly string[]): boolean {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const actual = Object.keys(value);
  return (
    actual.length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}

function hasValidBundleShape(bundle: MobileDiagnosticBundle): boolean {
  return (
    hasExactKeys(bundle, [
      "schema",
      "generatedAt",
      "app",
      "device",
      "latest",
      "recentRequests",
      "crashReporting",
    ]) &&
    bundle.schema === "voxagent.mobile.diagnostics.v1" &&
    SAFE_TIMESTAMP.test(bundle.generatedAt) &&
    bundle.crashReporting === "not-configured-manual-diagnostics-only" &&
    hasExactKeys(bundle.app, [
      "version",
      "buildVersion",
      "executionEnvironment",
    ]) &&
    Object.values(bundle.app).every((value) => SAFE_TEXT.test(value)) &&
    hasExactKeys(bundle.device, ["os", "osVersion", "deviceClass"]) &&
    SAFE_TEXT.test(bundle.device.os) &&
    SAFE_TEXT.test(bundle.device.osVersion) &&
    DEVICE_CLASSES.has(bundle.device.deviceClass) &&
    hasExactKeys(bundle.latest, [
      "networkState",
      "endpointClass",
      "errorCode",
      "correlationId",
    ]) &&
    (NETWORK_STATES.has(bundle.latest.networkState) ||
      bundle.latest.networkState === "unknown") &&
    (ENDPOINT_CLASSES.has(bundle.latest.endpointClass) ||
      bundle.latest.endpointClass === "unknown") &&
    (bundle.latest.errorCode === "NO_REQUEST" ||
      /^[A-Z][A-Z0-9_]{0,63}$/.test(bundle.latest.errorCode)) &&
    (bundle.latest.correlationId === "none" ||
      /^[A-Za-z0-9_-]{8,64}$/.test(bundle.latest.correlationId)) &&
    Array.isArray(bundle.recentRequests) &&
    bundle.recentRequests.every(
      (event) =>
        hasExactKeys(event, [
          "occurredAt",
          "correlationId",
          "endpointClass",
          "networkState",
          "errorCode",
        ]) &&
        SAFE_TIMESTAMP.test(event.occurredAt) &&
        ENDPOINT_CLASSES.has(event.endpointClass) &&
        NETWORK_STATES.has(event.networkState)
    )
  );
}

function boundedText(value: unknown, fallback: string): string {
  return typeof value === "string" && SAFE_TEXT.test(value)
    ? value
    : fallback;
}

function deviceClass(): MobileDiagnosticBundle["device"]["deviceClass"] {
  if (Platform.OS === "ios") {
    if (Platform.isTV) return "tv";
    return Platform.isPad ? "tablet" : "phone";
  }
  if (Platform.OS === "android") {
    const mode = Platform.constants.uiMode;
    if (mode === "tv") return "tv";
    return "phone";
  }
  if (Platform.OS === "web" || Platform.OS === "macos" || Platform.OS === "windows") {
    return "desktop";
  }
  return "unknown";
}

function appBuildVersion(): string {
  if (Platform.OS === "ios") {
    return boundedText(
      Constants.platform?.ios?.buildNumber ??
        Constants.expoConfig?.ios?.buildNumber,
      "unknown"
    );
  }
  if (Platform.OS === "android") {
    return boundedText(
      String(
        Constants.platform?.android?.versionCode ??
          Constants.expoConfig?.android?.versionCode ??
          ""
      ),
      "unknown"
    );
  }
  return "not-applicable";
}

export function createMobileDiagnosticBundle(
  now: Date = new Date()
): MobileDiagnosticBundle {
  const recentRequests = getRequestDiagnostics();
  const latest = recentRequests[0];
  return {
    schema: "voxagent.mobile.diagnostics.v1",
    generatedAt: now.toISOString(),
    app: {
      version: boundedText(Constants.expoConfig?.version, "unknown"),
      buildVersion: appBuildVersion(),
      executionEnvironment: boundedText(
        Constants.executionEnvironment,
        "unknown"
      ),
    },
    device: {
      os: boundedText(Platform.OS, "unknown"),
      osVersion: boundedText(String(Platform.Version), "unknown"),
      deviceClass: deviceClass(),
    },
    latest: {
      networkState: latest?.networkState ?? "unknown",
      endpointClass: latest?.endpointClass ?? "unknown",
      errorCode: latest?.errorCode ?? "NO_REQUEST",
      correlationId: latest?.correlationId ?? "none",
    },
    recentRequests,
    crashReporting: "not-configured-manual-diagnostics-only",
  };
}

export function serializeMobileDiagnosticBundle(
  bundle: MobileDiagnosticBundle
): string {
  return JSON.stringify(bundle, null, 2);
}

const SENSITIVE_CONTENT =
  /(authorization\s*:|bearer\s+[A-Za-z0-9._~+/=-]+|https?:\/\/[^/\s:@]+:[^@\s/]+@|(?:api[_-]?key|password|secret)\s*(?:[:=]\s*|\s+)\S+)/i;

export function scanMobileDiagnosticBundle(
  bundle: MobileDiagnosticBundle
): string[] {
  const findings: string[] = [];
  const serialized = serializeMobileDiagnosticBundle(bundle);
  if (!hasValidBundleShape(bundle)) findings.push("invalid_bundle_shape");
  if (serialized.length > 32_768) findings.push("bundle_too_large");
  if (bundle.recentRequests.length > 20) findings.push("too_many_requests");
  if (SENSITIVE_CONTENT.test(serialized)) {
    findings.push("sensitive_content_pattern");
  }
  for (const event of bundle.recentRequests) {
    if (!/^[A-Za-z0-9_-]{8,64}$/.test(event.correlationId)) {
      findings.push("invalid_correlation_id");
    }
    if (!/^[A-Z][A-Z0-9_]{0,63}$/.test(event.errorCode)) {
      findings.push("invalid_error_code");
    }
  }
  return [...new Set(findings)];
}
