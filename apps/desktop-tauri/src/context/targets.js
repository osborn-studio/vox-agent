// Conversation targets and the one place that decides which IPC channel a
// composer submission uses (T12). The group target records Context through
// the Rust journal; agent targets run through the Agent Engine. A submission
// never reaches both, and an unknown target reaches neither.

export const GROUP_TARGET = Object.freeze({
  kind: "group",
  id: "open-context-flow",
  label: "Open Context Flow",
});

export const COORDINATOR_TARGET = Object.freeze({
  kind: "agent",
  id: null,
  label: "Vox Agent coordinator",
});

export function agentTarget(agent) {
  if (!agent || typeof agent.id !== "string" || !agent.id) {
    throw new TypeError("agent target needs an id");
  }
  return Object.freeze({
    kind: "agent",
    id: agent.id,
    label: `${agent.emoji || ""} ${agent.name || agent.id}`.trim(),
  });
}

export function targetKey(target) {
  return target.kind === "group" ? "group" : `agent:${target.id ?? "coordinator"}`;
}

export function sameTarget(a, b) {
  return Boolean(a && b) && targetKey(a) === targetKey(b);
}

/**
 * The IPC call for one submission, or an error code when it must not be sent.
 * Context records name only a destination the Rust service was configured
 * with; URLs, identities and states are never part of the request.
 */
export function routeSubmission(target, { text, eventId, destination, project } = {}) {
  if (target?.kind === "group") {
    if (!destination) return { error: "no-destination" };
    if (!eventId) return { error: "no-event-id" };
    const input = { eventId, targetId: destination, text };
    if (project) input.project = { id: project.id, repository: project.repository };
    return { command: "context_submit_record", args: { input } };
  }
  if (target?.kind === "agent") {
    return {
      command: "run_engine_command",
      args: { input: { text, agentId: target.id, provider: "claude" } },
    };
  }
  return { error: "unknown-target" };
}
