import { describe, expect, it, vi } from "vitest";

import type { SpeechStreamCreated } from "../api/client";
import type {
  ConversationSpeechEventAdapter,
  ConversationSpeechSyncResult,
} from "./speechEvents";
import {
  createConversationVoiceController,
  type ConversationVoiceAudio,
} from "./voiceControl";

const created: SpeechStreamCreated = {
  streamId: "speech_voice_1",
  resumeToken: "speech-resume-token-with-enough-entropy",
  schema: "voxagent.speech.event",
  schemaVersion: 1,
  nextSequence: 2,
};
const participantToken = "participant-token-with-enough-entropy";

function success(
  overrides: Partial<Extract<ConversationSpeechSyncResult, { ok: true }>> = {}
): ConversationSpeechSyncResult {
  return {
    ok: true,
    cursor: 1,
    publishedDrafts: 0,
    publishedTurns: 0,
    terminal: null,
    ...overrides,
  };
}

function audioMock(): ConversationVoiceAudio {
  return {
    start: vi.fn(async () => created),
    stop: vi.fn(async () => undefined),
    cancel: vi.fn(async () => undefined),
  };
}

function adapterMock(
  results: ConversationSpeechSyncResult[]
): ConversationSpeechEventAdapter {
  let cursor = 0;
  return {
    poll: vi.fn(async () => {
      const result = results.shift() ?? success();
      cursor = result.cursor;
      return result;
    }),
    getCursor: () => cursor,
  };
}

const startInput = {
  sessionId: "conv_voice_1",
  participantRole: "HOST" as const,
  participantAccessToken: participantToken,
  sourceLanguage: "en",
  targetLanguage: "zh-CN",
  allowRemoteTranslation: true,
};

describe("conversation voice controller", () => {
  it("starts one stream, publishes live changes, and keeps capabilities private", async () => {
    const audio = audioMock();
    const adapter = adapterMock([
      success({ cursor: 2, publishedDrafts: 1 }),
    ]);
    const createAdapter = vi.fn(() => adapter);
    const changed = vi.fn();
    const subject = createConversationVoiceController({
      audio,
      createAdapter,
      onConversationChanged: changed,
    });

    await Promise.all([subject.start(startInput), subject.start(startInput)]);
    await subject.poll();

    expect(audio.start).toHaveBeenCalledTimes(1);
    expect(subject.getState().phase).toBe("listening");
    expect(createAdapter).toHaveBeenCalledWith({
      sessionId: startInput.sessionId,
      participantRole: "HOST",
      conversationAccessToken: participantToken,
      streamId: created.streamId,
      streamResumeToken: created.resumeToken,
      sourceLanguage: "en",
      targetLanguage: "zh-CN",
      allowRemoteTranslation: true,
      initialAfterSequence: 0,
    });
    expect(changed).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(subject)).not.toContain(participantToken);
    expect(JSON.stringify(subject)).not.toContain(created.resumeToken);
  });

  it("stops audio before finalizing one cited turn", async () => {
    const audio = audioMock();
    const adapter = adapterMock([
      success({
        cursor: 4,
        publishedTurns: 1,
        terminal: "stream.completed",
      }),
    ]);
    const phases: string[] = [];
    const subject = createConversationVoiceController({
      audio,
      createAdapter: () => adapter,
      onStateChange: (state) => phases.push(state.phase),
    });

    await subject.start(startInput);
    await subject.stop();

    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(adapter.poll).toHaveBeenCalledTimes(1);
    expect(phases).toEqual([
      "starting",
      "listening",
      "stopping",
      "finalizing",
      "idle",
    ]);
  });

  it("retries final translation once without recording again", async () => {
    const audio = audioMock();
    const adapter = adapterMock([
      {
        ok: false,
        cursor: 1,
        stage: "translation",
        code: "PROVIDER_UNAVAILABLE",
        retriable: true,
        offline: false,
      },
      success({
        cursor: 4,
        publishedTurns: 1,
        terminal: "stream.completed",
      }),
    ]);
    const subject = createConversationVoiceController({
      audio,
      createAdapter: () => adapter,
    });

    await subject.start(startInput);
    await subject.stop();
    expect(subject.getState()).toMatchObject({
      phase: "failed",
      retriable: true,
    });

    await subject.retry();

    expect(subject.getState().phase).toBe("idle");
    expect(audio.start).toHaveBeenCalledTimes(1);
    expect(audio.stop).toHaveBeenCalledTimes(1);
    expect(adapter.poll).toHaveBeenCalledTimes(2);
  });

  it("cancels on background and blocks a new start during reconciliation", async () => {
    const audio = audioMock();
    const subject = createConversationVoiceController({
      audio,
      createAdapter: () => adapterMock([]),
    });
    await subject.start(startInput);
    await subject.cancel();

    expect(audio.cancel).toHaveBeenCalledTimes(1);
    expect(subject.getState().phase).toBe("idle");

    let finishReconcile: (() => void) | undefined;
    const reconciliation = subject.reconcile(
      () =>
        new Promise<void>((resolve) => {
          finishReconcile = resolve;
        })
    );
    const blockedStart = subject.start(startInput);

    expect(subject.getState().phase).toBe("reconciling");
    expect(audio.start).toHaveBeenCalledTimes(1);
    finishReconcile?.();
    await reconciliation;
    await blockedStart;
    expect(subject.getState().phase).toBe("idle");
  });

  it("queues foreground reconciliation behind in-flight background cleanup", async () => {
    let finishCancel: (() => void) | undefined;
    const audio = audioMock();
    vi.mocked(audio.cancel).mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finishCancel = resolve;
        })
    );
    const subject = createConversationVoiceController({
      audio,
      createAdapter: () => adapterMock([]),
    });
    await subject.start(startInput);

    const background = subject.cancel();
    const reconcileTask = vi.fn(async () => undefined);
    const foreground = subject.reconcile(reconcileTask);

    expect(reconcileTask).not.toHaveBeenCalled();
    finishCancel?.();
    await background;
    await foreground;
    expect(reconcileTask).toHaveBeenCalledTimes(1);
    expect(subject.getState().phase).toBe("idle");
  });

  it("preempts an in-flight microphone start when backgrounded", async () => {
    let rejectStart: ((error: Error) => void) | undefined;
    const audio = audioMock();
    vi.mocked(audio.start).mockImplementation(
      () =>
        new Promise<SpeechStreamCreated>((_resolve, reject) => {
          rejectStart = reject;
        })
    );
    vi.mocked(audio.cancel).mockImplementation(async () => {
      rejectStart?.(new Error("cancelled"));
    });
    const createAdapter = vi.fn(() => adapterMock([]));
    const subject = createConversationVoiceController({
      audio,
      createAdapter,
    });

    const starting = subject.start(startInput);
    const cancelling = subject.cancel();
    await starting;
    await cancelling;

    expect(audio.cancel).toHaveBeenCalled();
    expect(createAdapter).not.toHaveBeenCalled();
    expect(subject.getState().phase).toBe("idle");
  });
});
