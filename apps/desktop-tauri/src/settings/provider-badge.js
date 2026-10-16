// Provider qualification badge (T28a, ADR 2026-07-19). Green "qualified"
// needs a passed probe. An installed local runtime or a stored credential is
// only "not verified"; the word "ready" is never shown.

const MISSING = {
  missing_credential: "missing credential",
  missing_runtime: "missing runtime",
};

export function providerBadge(provider) {
  if (provider.probe_status === "passed") return { tone: "live", label: "qualified" };
  if (provider.probe_status === "failed") return { tone: "danger", label: "probe failed" };
  switch (provider.readiness) {
    case "ready":
      return { tone: "warn", label: "installed · not verified" };
    case "configured_unverified":
      return { tone: "warn", label: "configured · not verified" };
    case "not_implemented":
      return { tone: "neutral", label: "not implemented" };
    case "platform_unavailable":
      return { tone: "neutral", label: "platform unavailable" };
    default: {
      const label = MISSING[provider.readiness] ?? "unknown";
      return { tone: provider.active_roles?.length ? "warn" : "danger", label };
    }
  }
}
