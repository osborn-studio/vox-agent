import React, { act } from "react";
import { createRoot } from "test-renderer";
import { describe, expect, it, vi } from "vitest";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("react-native", () => ({
  StyleSheet: { create: <T,>(styles: T) => styles },
  Text: "Text",
  View: "View",
}));

import {
  ConnectionBanner,
  type ConnectionStatus,
} from "./ConnectionBanner";

describe("ConnectionBanner", () => {
  it.each([
    ["unchecked", "Connection not checked"],
    ["loading", "Connecting"],
    ["live", "Live"],
    ["demo", "Demo data"],
    ["offline", "Offline"],
    ["unavailable", "Unavailable"],
  ] satisfies Array<[ConnectionStatus, string]>)(
    "renders a textual %s state instead of relying on color",
    async (status, expectedLabel) => {
      const root = createRoot();
      await act(async () => {
        root.render(
          <ConnectionBanner status={status} detail="Release boundary detail." />
        );
      });

      const output = JSON.stringify(root.container.toJSON());
      expect(output).toContain(expectedLabel);
      expect(output).toContain("Release boundary detail.");
    }
  );
});
