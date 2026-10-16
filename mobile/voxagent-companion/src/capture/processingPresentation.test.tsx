import React, { act, useState } from "react";
import { createRoot } from "test-renderer";
import { describe, expect, it, vi } from "vitest";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("react-native", () => ({
  ActivityIndicator: "ActivityIndicator",
  ScrollView: "ScrollView",
  StyleSheet: { create: <T,>(styles: T) => styles },
  Text: "Text",
  TextInput: "TextInput",
  TouchableOpacity: "TouchableOpacity",
  View: "View",
}));

vi.mock("expo-audio", () => ({
  RecordingPresets: { HIGH_QUALITY: {} },
  requestRecordingPermissionsAsync: vi.fn(),
  useAudioRecorder: vi.fn(),
}));

import type {
  AudioUploadResult,
  CaptureSession,
  MemoryNode,
} from "../api/client";
import * as api from "../api/client";
import {
  requestRecordingPermissionsAsync,
  useAudioRecorder,
} from "expo-audio";
import type { CaptureProcessingState } from "./processingLifecycle";
import { PersistentTabPanel } from "../components/PersistentTabPanel";
import CaptureScreen, {
  CaptureProcessingPanel,
} from "../screens/CaptureScreen";
import {
  CaptureSummaryPanel,
  type CaptureSummaryPresentationState,
} from "../screens/MemoryScreen";

const upload: AudioUploadResult = {
  sessionId: "capture-1",
  audioPath: "/private/secret/recording.m4a",
  bytes: 4096,
};

const session: CaptureSession = {
  id: upload.sessionId,
  workspaceId: "workspace-1",
  ownerUserId: "user-1",
  edition: "PERSONAL",
  source: "MOBILE",
  mode: "MEETING",
  title: null,
  startedAt: "2026-07-26T12:00:00Z",
  endedAt: "2026-07-26T12:01:00Z",
  languageHints: ["en"],
  detectedLanguages: ["en"],
  privacyLevel: "PERSONAL",
  consentState: "GRANTED",
  deviceIds: [],
  localOnlyUntilSync: false,
  status: "READY",
};

const summaryNode: MemoryNode = {
  id: "summary-1",
  workspaceId: session.workspaceId,
  ownerUserId: session.ownerUserId,
  sourceSessionId: session.id,
  type: "SUMMARY",
  title: "Launch summary",
  body: "The team approved the private beta.",
  language: "en",
  importance: "HIGH",
  visibility: "PRIVATE",
  tags: ["launch"],
  metadata: {},
  confidence: 0.98,
  createdAt: "2026-07-26T12:02:00Z",
  updatedAt: "2026-07-26T12:02:00Z",
};

async function renderJson(element: React.ReactElement): Promise<string> {
  const root = createRoot();
  await act(async () => {
    root.render(element);
  });
  return JSON.stringify(root.container.toJSON());
}

function findByAccessibilityLabel(
  node: unknown,
  label: string
): { props: { onPress: () => Promise<void> | void } } {
  if (node && typeof node === "object") {
    const candidate = node as {
      props?: { accessibilityLabel?: string; onPress?: () => Promise<void> | void };
      children?: unknown[];
    };
    if (
      candidate.props?.accessibilityLabel === label &&
      candidate.props.onPress
    ) {
      return candidate as { props: { onPress: () => Promise<void> | void } };
    }
    for (const child of candidate.children ?? []) {
      try {
        return findByAccessibilityLabel(child, label);
      } catch {
        // Keep walking the rendered host tree.
      }
    }
  }
  throw new Error(`Could not find accessibility label: ${label}`);
}

describe("PersistentTabPanel", () => {
  it("keeps children mounted while hiding an inactive tab", async () => {
    let mounts = 0;
    function Probe() {
      const [mountId] = useState(() => ++mounts);
      return <React.Fragment>{`probe-${mountId}`}</React.Fragment>;
    }

    const root = createRoot();
    await act(async () => {
      root.render(
        <PersistentTabPanel active>
          <Probe />
        </PersistentTabPanel>
      );
    });
    await act(async () => {
      root.render(
        <PersistentTabPanel active={false}>
          <Probe />
        </PersistentTabPanel>
      );
    });

    const output = JSON.stringify(root.container.toJSON());
    expect(mounts).toBe(1);
    expect(output).toContain("probe-1");
    expect(output).toContain("no-hide-descendants");
    expect(output).toContain('"display":"none"');
  });
});

describe("CaptureScreen processing orchestration", () => {
  it("starts processing exactly once after a successful upload", async () => {
    const recorder = {
      prepareToRecordAsync: vi.fn().mockResolvedValue(undefined),
      record: vi.fn(),
      stop: vi.fn().mockResolvedValue(undefined),
      uri: "file:///recording.m4a",
    };
    vi.mocked(useAudioRecorder).mockReturnValue(
      recorder as unknown as ReturnType<typeof useAudioRecorder>
    );
    vi.mocked(requestRecordingPermissionsAsync).mockResolvedValue({
      granted: true,
    } as Awaited<ReturnType<typeof requestRecordingPermissionsAsync>>);
    vi.spyOn(api, "createCaptureSession").mockResolvedValue({
      ok: true,
      offline: false,
      source: "live",
      data: { ...session, status: "RECORDING" },
    });
    vi.spyOn(api, "uploadAudio").mockResolvedValue({
      ok: true,
      offline: false,
      source: "live",
      data: upload,
    });
    const process = vi.spyOn(api, "processCaptureSession").mockResolvedValue({
      ok: true,
      offline: false,
      source: "live",
      data: session,
    });

    const root = createRoot();
    await act(async () => {
      root.render(<CaptureScreen onOpenMemory={vi.fn()} />);
    });
    await act(async () => {
      await findByAccessibilityLabel(
        root.container.toJSON(),
        "Record audio"
      ).props.onPress();
    });
    await act(async () => {
      await findByAccessibilityLabel(
        root.container.toJSON(),
        "Stop & upload"
      ).props.onPress();
    });

    const output = JSON.stringify(root.container.toJSON());
    expect(process).toHaveBeenCalledTimes(1);
    expect(process).toHaveBeenCalledWith(session.id);
    expect(output).toContain("Memory ready");
    expect(output).not.toContain(upload.audioPath);
  });
});

describe("CaptureProcessingPanel", () => {
  it.each([
    [
      { phase: "uploaded", upload, attempts: 0 },
      ["Upload complete", "capture-1"],
    ],
    [
      { phase: "processing", upload, attempts: 1 },
      ["Processing recording", "Attempt 1 of 3"],
    ],
    [
      {
        phase: "failed",
        upload,
        attempts: 1,
        code: "ENGINE_OFFLINE",
      },
      ["Processing failed", "2 attempts remaining"],
    ],
    [
      {
        phase: "failed",
        upload,
        attempts: 3,
        code: "REQUEST_REJECTED",
      },
      ["Processing failed", "Retry limit reached"],
    ],
    [
      { phase: "ready", upload, attempts: 1, session },
      ["Memory ready", "Open memory"],
    ],
  ] satisfies Array<[CaptureProcessingState, string[]]>)(
    "renders the %s lifecycle state with text and no private audio path",
    async (state, expected) => {
      const output = await renderJson(
        <CaptureProcessingPanel
          state={state}
          onRetry={vi.fn()}
          onOpenMemory={vi.fn()}
        />
      );

      for (const text of expected) expect(output).toContain(text);
      expect(output).not.toContain(upload.audioPath);
    }
  );
});

describe("CaptureSummaryPanel", () => {
  it.each([
    [
      { phase: "loading", sessionId: session.id },
      ["Loading memory", session.id],
    ],
    [
      { phase: "failed", sessionId: session.id, kind: "offline" },
      ["Memory unavailable", "No replacement data is shown", "Try again"],
    ],
    [
      {
        phase: "ready",
        summary: {
          sessionId: session.id,
          status: "READY",
          summaryNodes: [summaryNode],
          decisions: [],
          actionItems: [],
        },
      },
      ["Processed memory", session.id, summaryNode.title, summaryNode.body],
    ],
  ] satisfies Array<[CaptureSummaryPresentationState, string[]]>)(
    "renders a same-session %s result state",
    async (state, expected) => {
      const output = await renderJson(
        <CaptureSummaryPanel state={state} onRetry={vi.fn()} />
      );

      for (const text of expected) expect(output).toContain(text);
    }
  );
});
