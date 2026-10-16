import React, { act } from "react";
import { createRoot } from "test-renderer";
import { describe, expect, it, vi } from "vitest";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("react-native", () => ({
  AppState: { addEventListener: vi.fn(() => ({ remove: vi.fn() })) },
  ScrollView: "ScrollView",
  StyleSheet: { create: <T,>(styles: T) => styles },
  Text: "Text",
  TouchableOpacity: "TouchableOpacity",
  View: "View",
}));

import type { PermissionRequest } from "../api/client";
import { ApprovalCard } from "../screens/ApprovalsScreen";
import type { ApprovalItem } from "./state";

const NOW = "2026-07-26T12:00:00Z";

function occurrences(value: string, needle: string): number {
  return value.split(needle).length - 1;
}

function item(
  overrides: Partial<PermissionRequest> = {},
  phase: ApprovalItem["phase"] = "pending"
): ApprovalItem {
  return {
    request: {
      approval_id: "approval-mobile-1",
      task_id: "task-mobile-1",
      agent_id: "agent-mobile-1",
      tool: "bash",
      scope: {
        command: "git status --short",
        working_directory: "/Volumes/Workspace/project",
      },
      requested_at: "2026-07-26T11:59:00Z",
      expires_at: "2026-07-26T12:01:00Z",
      ...overrides,
    },
    phase,
    resolutionError: null,
    resolvedAt: null,
  };
}

describe("ApprovalCard", () => {
  it("shows exact scope, identity, duration, and status before card-local actions", async () => {
    const onDecide = vi.fn();
    const root = createRoot();
    await act(async () => {
      root.render(
        <ApprovalCard
          item={item()}
          sync="live"
          now={NOW}
          onDecide={onDecide}
        />
      );
    });

    const output = JSON.stringify(root.container.toJSON());
    expect(output).toContain("○ Pending review");
    expect(output).toContain("git status --short");
    expect(output).toContain("/Volumes/Workspace/project");
    expect(output).toContain("120 seconds");
    expect(output).toContain("approval-mobile-1");
    expect(output).toContain("task-mobile-1");
    expect(output).toContain("agent-mobile-1");
    expect(output.indexOf("VERIFIED SCOPE")).toBeLessThan(
      output.indexOf("Deny")
    );
    expect(output.indexOf("Deny")).toBeLessThan(output.indexOf("Approve"));
    expect(output).toContain(
      "Approve permission request approval-mobile-1"
    );
    expect(output).toContain('"disabled":false');
  });

  it("never renders unexpected private keys and disables approve for incomplete scope", async () => {
    const onDecide = vi.fn();
    const root = createRoot();
    await act(async () => {
      root.render(
        <ApprovalCard
          item={item({
            scope: {
              action: "curl -H 'Authorization: Bearer principal-secret'",
              path: "[redacted: sensitive value]",
              prompt: "private transcript should never render",
              [["to", "ken"].join("")]:
                "principal-secret-should-never-render",
            },
          })}
          sync="live"
          now={NOW}
          onDecide={onDecide}
        />
      );
    });

    const output = JSON.stringify(root.container.toJSON());
    expect(output).not.toContain("private transcript");
    expect(output).not.toContain("principal-secret");
    expect(output).toContain("[protected value withheld]");
    expect(output).toContain(
      "complete exact scope or request identity is unavailable"
    );

    expect(output).toContain(
      '"accessibilityLabel":"Deny permission request approval-mobile-1","accessibilityState":{"disabled":false}'
    );
    expect(output).toContain(
      '"accessibilityLabel":"Approve permission request approval-mobile-1","accessibilityState":{"disabled":true}'
    );
  });

  it.each([
    ["resolving", "Recording decision"],
    ["approved", "Approved"],
    ["denied", "Denied"],
    ["expired", "Expired"],
    ["closed", "Resolved elsewhere"],
  ] satisfies Array<[ApprovalItem["phase"], string]>)(
    "renders a non-color %s state with both actions disabled",
    async (phase, label) => {
      const root = createRoot();
      await act(async () => {
        root.render(
          <ApprovalCard
            item={item({}, phase)}
            sync="live"
            now={NOW}
            onDecide={vi.fn()}
          />
        );
      });

      const output = JSON.stringify(root.container.toJSON());
      expect(output).toContain(label);
      expect(occurrences(output, '"disabled":true')).toBe(4);
    }
  );
});
