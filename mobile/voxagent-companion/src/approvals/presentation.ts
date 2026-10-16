import type { PermissionRequest } from "../api/client";
import type {
  ApprovalItem,
  ApprovalSync,
} from "./state";
import { canResolveApproval } from "./state";

const RESOURCE_KEYS = [
  "resource",
  "file_path",
  "path",
  "url",
  "target",
  "working_directory",
] as const;

const REMOTE_SCOPE_KEYS = new Set([
  "action",
  "command",
  "file_path",
  "path",
  "resource",
  "target",
  "url",
  "working_directory",
]);

const CONTROL_MARKERS = ["[redacted", "[truncated]"] as const;
const SENSITIVE_VALUE =
  /(authorization\s*:|bearer\s+[A-Za-z0-9._~+/=-]+|[A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password)[A-Za-z0-9_-]*\s*(?:[:=]\s*|\s+)\S+|https?:\/\/[^/\s:@]+:[^@\s/]+@)/i;
const MAX_SCOPE_VALUE_LENGTH = 512;
const PROTECTED_VALUE = "[protected value withheld]";

export interface ApprovalPresentation {
  tool: string;
  action: string;
  resource: string;
  duration: string;
  requestId: string;
  taskId: string;
  agentId: string;
  scopeEntries: Array<[string, string]>;
  hasCompleteScope: boolean;
  hasCompleteIdentity: boolean;
  scopeNotice: string | null;
}

function safeDate(value: string): number | null {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function hasControlMarker(value: string): boolean {
  const normalized = value.toLocaleLowerCase();
  return CONTROL_MARKERS.some((marker) => normalized.includes(marker));
}

function shouldProtectValue(value: string): boolean {
  return (
    !value.trim() ||
    value.length > MAX_SCOPE_VALUE_LENGTH ||
    hasControlMarker(value) ||
    SENSITIVE_VALUE.test(value)
  );
}

function exactAction(
  displayScope: Record<string, string>,
  displayTool: string
): string {
  return displayScope.action || displayScope.command || displayTool;
}

function exactResource(
  displayScope: Record<string, string>
): string {
  for (const key of RESOURCE_KEYS) {
    const value = displayScope[key];
    if (value) return value;
  }
  return "";
}

export function presentApproval(
  request: PermissionRequest
): ApprovalPresentation {
  const rawScopeEntries = Object.entries(request.scope);
  const scopeEntries = rawScopeEntries
    .filter(([key]) => REMOTE_SCOPE_KEYS.has(key))
    .map(
      ([key, value]) =>
        [key, shouldProtectValue(value) ? PROTECTED_VALUE : value] as [
          string,
          string,
        ]
    )
    .sort(([left], [right]) => left.localeCompare(right));
  const displayScope = Object.fromEntries(scopeEntries);
  const requestedAt = safeDate(request.requested_at);
  const expiresAt = safeDate(request.expires_at);
  const durationSeconds =
    requestedAt === null || expiresAt === null
      ? null
      : Math.max(0, Math.round((expiresAt - requestedAt) / 1000));
  const containsProtectedValue = scopeEntries.some(
    ([, value]) => value === PROTECTED_VALUE
  );
  const containsUnexpectedKey = rawScopeEntries.some(
    ([key]) => !REMOTE_SCOPE_KEYS.has(key)
  );
  const displayIdentifier = (value: string): string =>
    shouldProtectValue(value) ? PROTECTED_VALUE : value;
  const tool = displayIdentifier(request.tool);
  const action = exactAction(displayScope, tool);
  const resource = exactResource(displayScope);
  const requestId = displayIdentifier(request.approval_id);
  const taskId = displayIdentifier(request.task_id);
  const agentId = displayIdentifier(request.agent_id);
  const hasCompleteIdentity = [tool, requestId, taskId, agentId].every(
    (value) => value !== PROTECTED_VALUE
  );
  const hasCompleteScope =
    scopeEntries.length > 0 &&
    Boolean(action.trim()) &&
    Boolean(resource.trim()) &&
    durationSeconds !== null &&
    durationSeconds > 0 &&
    !containsProtectedValue &&
    !containsUnexpectedKey;

  return {
    tool,
    action,
    resource: resource || "Not provided",
    duration:
      durationSeconds === null
        ? "Invalid"
        : `${durationSeconds} second${durationSeconds === 1 ? "" : "s"}`,
    requestId,
    taskId,
    agentId,
    scopeEntries,
    hasCompleteScope,
    hasCompleteIdentity,
    scopeNotice: hasCompleteScope && hasCompleteIdentity
      ? null
      : "Approval is disabled because the complete exact scope or request identity is unavailable. You may still deny this request.",
  };
}

export function approvalActions(
  item: ApprovalItem,
  sync: ApprovalSync,
  now: string
): { canApprove: boolean; canDeny: boolean } {
  const canResolve = canResolveApproval(item, sync, now);
  const presentation = presentApproval(item.request);
  return {
    canApprove:
      canResolve &&
      presentation.hasCompleteScope &&
      presentation.hasCompleteIdentity,
    canDeny: canResolve,
  };
}

export function approvalPhaseLabel(item: ApprovalItem): string {
  switch (item.phase) {
    case "pending":
      return "○ Pending review";
    case "resolving":
      return "… Recording decision";
    case "approved":
      return "✓ Approved";
    case "denied":
      return "× Denied";
    case "expired":
      return "! Expired";
    case "closed":
      return "◇ Resolved elsewhere";
  }
}
