import React, { act } from "react";
import { createRoot } from "test-renderer";
import { describe, expect, it, vi } from "vitest";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
  .IS_REACT_ACT_ENVIRONMENT = true;

vi.mock("react-native", () => ({
  AccessibilityInfo: { announceForAccessibility: vi.fn() },
  StyleSheet: { create: <T,>(styles: T) => styles },
  Text: "Text",
  TouchableOpacity: "TouchableOpacity",
  View: "View",
}));

vi.mock("expo-camera", () => ({
  CameraView: "CameraView",
  useCameraPermissions: () => [{ granted: true }, vi.fn()],
}));

vi.mock("expo-linking", () => ({ useLinkingURL: () => null }));
vi.mock("react-native-qrcode-svg", () => ({ default: "QRCode" }));
vi.mock("../conversation/useConversationVoiceControl", async () => {
  const { initialConversationVoiceState } = await import("./voiceState");
  return {
    useConversationVoiceControl: () => ({
      state: initialConversationVoiceState,
      nativePhase: "idle",
      isNativeStreaming: false,
      start: vi.fn(async () => undefined),
      stop: vi.fn(async () => undefined),
      cancel: vi.fn(async () => undefined),
      retry: vi.fn(async () => undefined),
      reconcile: vi.fn(async (task: () => Promise<void>) => task()),
      reset: vi.fn(async () => undefined),
    }),
  };
});

import type {
  ConversationLiveDraft,
  ConversationSession,
  ConversationTurn,
} from "../api/client";
import {
  ConversationPanes,
  ConversationVoiceControls,
  retainConversationInvite,
} from "../screens/ConversationScreen";
import {
  conversationVoiceReducer,
  initialConversationVoiceState,
} from "./voiceState";

const session: ConversationSession = {
  schema: "voxagent.conversation.session",
  schemaVersion: 1,
  id: "conversation-1",
  mode: "REMOTE_QR",
  status: "ACTIVE",
  hostLanguage: "en",
  guestLanguage: "zh-CN",
  participantCount: 2,
  createdAt: "2026-07-20T12:00:00Z",
  expiresAt: "2026-07-20T13:00:00Z",
  nextSequence: 2,
};

const guestFirstTurn: ConversationTurn = {
  schema: "voxagent.conversation.turn",
  schemaVersion: 1,
  type: "conversation.turn",
  eventId: "turn-event-1",
  sessionId: session.id,
  sequence: 1,
  participantRole: "GUEST",
  clientEventId: "guest-event-1",
  createdAt: "2026-07-20T12:00:05Z",
  speechEvent: {
    schema: "voxagent.speech.event",
    schemaVersion: 1,
    eventId: "speech-event-1",
    type: "transcript.final",
    streamId: "stream-1",
    sessionId: session.id,
    sequence: 1,
    monotonicMs: 5_000,
    emittedAt: "2026-07-20T12:00:05Z",
    payload: {
      segmentId: "segment-1",
      revision: 1,
      text: "你好",
      language: { code: "zh-CN", confidence: 1 },
      speaker: { id: "guest", label: "Guest" },
      audioStartMs: 0,
      audioEndMs: 900,
      confidence: 1,
    },
  },
  translationEvent: {
    schema: "voxagent.translation.event",
    schemaVersion: 1,
    eventId: "translation-event-1",
    translationId: "translation-1",
    type: "translation.final",
    emittedAt: "2026-07-20T12:00:06Z",
    latencyMs: 600,
    payload: {
      sourceLanguage: "zh-CN",
      targetLanguage: "en",
      text: "Hello",
      confidence: 1,
      provider: "test",
      model: "test",
      sourceSegments: [
        {
          streamId: "stream-1",
          segmentId: "segment-1",
          revision: 1,
          audioStartMs: 0,
          audioEndMs: 900,
        },
      ],
      glossaryHits: [],
    },
  },
};

const hostDraft: ConversationLiveDraft = {
  schema: "voxagent.conversation.live",
  schemaVersion: 1,
  type: "conversation.live",
  sessionId: session.id,
  participantRole: "HOST",
  streamId: "stream-live-1",
  segmentId: "segment-live-1",
  revision: 2,
  sourceLanguage: "en",
  targetLanguage: "zh-CN",
  speechEvent: {
    schema: "voxagent.speech.event",
    schemaVersion: 1,
    eventId: "partial-live-2",
    type: "transcript.partial",
    streamId: "stream-live-1",
    sessionId: session.id,
    sequence: 2,
    monotonicMs: 500,
    emittedAt: "2026-07-20T12:00:04Z",
    payload: {
      segmentId: "segment-live-1",
      revision: 2,
      text: "I am still speaking",
      language: { code: "en", confidence: 0.9 },
      speaker: { id: "host", label: "You" },
      audioStartMs: 0,
      audioEndMs: 500,
      confidence: 0.9,
    },
  },
  updatedAt: "2026-07-20T12:00:04Z",
};

describe("ConversationPanes", () => {
  it("keeps host and guest language labels stable when the guest speaks first", async () => {
    const root = createRoot();
    await act(async () => {
      root.render(
        <ConversationPanes session={session} turns={[guestFirstTurn]} />
      );
    });

    const output = JSON.stringify(root.container.toJSON());
    expect(output).toContain("English");
    expect(output).toContain("中文");
    expect(output.indexOf("English")).toBeLessThan(output.indexOf("中文"));
  });

  it("labels an interim phrase as a draft without animation or color-only meaning", async () => {
    const root = createRoot();
    await act(async () => {
      root.render(
        <ConversationPanes
          session={session}
          turns={[]}
          liveDrafts={{ HOST: hostDraft, GUEST: null }}
        />
      );
    });

    const output = JSON.stringify(root.container.toJSON());
    expect(output).toContain("✎ Draft");
    expect(output).toContain("I am still speaking");
    expect(output).toContain("Draft from You");
    expect(output).toContain('"accessible":true');
    expect(output).not.toContain("Animated");
  });
});

describe("retainConversationInvite", () => {
  it("drops the QR payload as soon as replay reports an active session", () => {
    const invite = {
      qrPayload:
        "voxagent://conversation/join#v=1&token=invite-token-with-enough-entropy",
      expiresAt: "2026-07-20T12:05:00Z",
    };

    expect(retainConversationInvite(invite, "WAITING")).toBe(invite);
    expect(retainConversationInvite(invite, "ACTIVE")).toBeNull();
  });
});

describe("ConversationVoiceControls", () => {
  const callbacks = {
    onAllowRemoteTranslationChange: vi.fn(),
    onStart: vi.fn(),
    onStop: vi.fn(),
    onCancel: vi.fn(),
    onRetry: vi.fn(),
    onDismiss: vi.fn(),
  };

  it("shows explicit consent and a labelled idle action", async () => {
    const root = createRoot();
    await act(async () => {
      root.render(
        <ConversationVoiceControls
          state={initialConversationVoiceState}
          conversationReady={true}
          allowRemoteTranslation={false}
          {...callbacks}
        />
      );
    });

    const output = JSON.stringify(root.container.toJSON());
    expect(output).toContain("Ready to speak");
    expect(output).toContain("Start speaking");
    expect(output).toContain("Allow configured remote translation");
    expect(output).toContain('"checked":false');
    expect(output).toContain('"accessibilityLiveRegion":"polite"');

    await act(async () => {
      root.render(
        <ConversationVoiceControls
          state={initialConversationVoiceState}
          conversationReady={false}
          allowRemoteTranslation={false}
          {...callbacks}
        />
      );
    });
    const reconnectingOutput = JSON.stringify(root.container.toJSON());
    expect(reconnectingOutput).toContain(
      "Waiting for conversation connection"
    );
    expect(reconnectingOutput).not.toContain("Start speaking");
  });

  it("uses textual listening, stop, cancel, and retry states", async () => {
    const root = createRoot();
    const listening = conversationVoiceReducer(
      conversationVoiceReducer(initialConversationVoiceState, {
        type: "BEGIN",
      }),
      { type: "STARTED" }
    );
    await act(async () => {
      root.render(
        <ConversationVoiceControls
          state={listening}
          conversationReady={true}
          allowRemoteTranslation={true}
          {...callbacks}
        />
      );
    });
    const listeningOutput = JSON.stringify(root.container.toJSON());
    expect(listeningOutput).toContain("Listening");
    expect(listeningOutput).toContain("Stop and translate");
    expect(listeningOutput).toContain("Cancel utterance");

    const failed = conversationVoiceReducer(listening, {
      type: "FAIL",
      error: "Speech transport was interrupted.",
      retriable: true,
      retryPhase: "starting",
    });
    await act(async () => {
      root.render(
        <ConversationVoiceControls
          state={failed}
          conversationReady={true}
          allowRemoteTranslation={true}
          {...callbacks}
        />
      );
    });
    const failedOutput = JSON.stringify(root.container.toJSON());
    expect(failedOutput).toContain("Speech transport was interrupted.");
    expect(failedOutput).toContain("Retry once");
    expect(failedOutput).not.toContain("Cancel utterance");

    const blocked = conversationVoiceReducer(listening, {
      type: "FAIL",
      error: "Translation is not configured.",
      retriable: false,
      retryPhase: "finalizing",
    });
    await act(async () => {
      root.render(
        <ConversationVoiceControls
          state={blocked}
          conversationReady={true}
          allowRemoteTranslation={true}
          {...callbacks}
        />
      );
    });
    expect(JSON.stringify(root.container.toJSON())).toContain("Dismiss error");
  });
});
