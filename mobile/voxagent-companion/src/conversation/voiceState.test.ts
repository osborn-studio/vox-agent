import { describe, expect, it } from "vitest";

import {
  canCancelConversationVoice,
  canRetryConversationVoice,
  canStartConversationVoice,
  canStopConversationVoice,
  conversationVoiceReducer,
  initialConversationVoiceState,
  presentConversationVoice,
} from "./voiceState";

describe("conversation voice state", () => {
  it("moves through listen, stop, finalization, and completion", () => {
    const starting = conversationVoiceReducer(initialConversationVoiceState, {
      type: "BEGIN",
    });
    const listening = conversationVoiceReducer(starting, { type: "STARTED" });
    const stopping = conversationVoiceReducer(listening, { type: "STOP" });
    const finalizing = conversationVoiceReducer(stopping, {
      type: "FINALIZE",
    });
    const complete = conversationVoiceReducer(finalizing, {
      type: "COMPLETE",
    });

    expect(starting.phase).toBe("starting");
    expect(listening.phase).toBe("listening");
    expect(stopping.phase).toBe("stopping");
    expect(finalizing.phase).toBe("finalizing");
    expect(complete).toEqual(initialConversationVoiceState);
  });

  it("prevents overlapping starts and exposes only valid controls", () => {
    const starting = conversationVoiceReducer(initialConversationVoiceState, {
      type: "BEGIN",
    });
    const duplicate = conversationVoiceReducer(starting, { type: "BEGIN" });
    const listening = conversationVoiceReducer(starting, { type: "STARTED" });

    expect(duplicate).toBe(starting);
    expect(canStartConversationVoice(starting)).toBe(false);
    expect(canStopConversationVoice(starting)).toBe(false);
    expect(canStopConversationVoice(listening)).toBe(true);
    expect(canCancelConversationVoice(listening)).toBe(true);
  });

  it("allows one bounded retry at the failed stage", () => {
    const starting = conversationVoiceReducer(initialConversationVoiceState, {
      type: "BEGIN",
    });
    const failed = conversationVoiceReducer(starting, {
      type: "FAIL",
      error: "Microphone could not start.",
      retriable: true,
      retryPhase: "starting",
    });
    const retrying = conversationVoiceReducer(failed, { type: "RETRY" });
    const failedAgain = conversationVoiceReducer(retrying, {
      type: "FAIL",
      error: "Microphone could not start.",
      retriable: true,
      retryPhase: "starting",
    });

    expect(canRetryConversationVoice(failed)).toBe(true);
    expect(retrying).toMatchObject({ phase: "starting", retriesUsed: 1 });
    expect(failedAgain).toMatchObject({
      phase: "failed",
      retriable: false,
      retriesUsed: 1,
    });
    expect(canRetryConversationVoice(failedAgain)).toBe(false);
    expect(conversationVoiceReducer(failedAgain, { type: "RETRY" })).toBe(
      failedAgain
    );
  });

  it("represents reconnect and cancel with text, not color alone", () => {
    const listening = conversationVoiceReducer(
      conversationVoiceReducer(initialConversationVoiceState, {
        type: "BEGIN",
      }),
      { type: "STARTED" }
    );
    const reconnecting = conversationVoiceReducer(listening, {
      type: "RECONNECT",
    });
    const resumed = conversationVoiceReducer(reconnecting, { type: "RESUME" });
    const cancelling = conversationVoiceReducer(resumed, { type: "CANCEL" });

    expect(presentConversationVoice(reconnecting)).toMatchObject({
      symbol: "↻",
      status: "Reconnecting speech",
    });
    expect(presentConversationVoice(cancelling)).toMatchObject({
      symbol: "×",
      status: "Cancelling speech",
    });
    expect(
      conversationVoiceReducer(initialConversationVoiceState, {
        type: "RECONCILE",
      }).phase
    ).toBe("reconciling");
  });
});
