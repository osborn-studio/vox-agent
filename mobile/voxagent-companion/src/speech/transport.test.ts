import { describe, expect, it, vi } from "vitest";

import type {
  ApiFailure,
  SpeechAudioChunkAck,
  SpeechStreamCreated,
  SpeechTerminalEvent,
  WriteResult,
} from "../api/client";
import {
  SpeechAudioStreamSession,
  SpeechPcmTransport,
  type SpeechPcmApi,
} from "./transport";

const created: SpeechStreamCreated = {
  streamId: "speech-mobile-1",
  resumeToken: "resume-token-with-enough-entropy",
  schema: "voxagent.speech.event",
  schemaVersion: 1,
  nextSequence: 2,
};

function success<T>(data: T): WriteResult<T> {
  return {
    ok: true,
    offline: false,
    source: "live",
    data,
  };
}

function terminal(type: "stream.completed" | "stream.cancelled"): SpeechTerminalEvent {
  const base = {
    schema: "voxagent.speech.event",
    schemaVersion: 1,
    eventId: `${type}-1`,
    streamId: created.streamId,
    sessionId: "conversation-1",
    sequence: 3,
    type,
    monotonicMs: 1000,
    emittedAt: "2026-07-26T12:00:00Z",
  } as const;
  return type === "stream.completed"
    ? { ...base, type, payload: { reason: "input_finished" } }
    : {
        ...base,
        type,
        payload: { reason: "user_cancelled", cancelledBy: "mobile" },
      };
}

function apiMock(overrides: Partial<SpeechPcmApi> = {}): SpeechPcmApi {
  return {
    start: vi.fn(async () => success(created)),
    sendChunk: vi.fn(async (input) =>
      success<SpeechAudioChunkAck>({
        schema: "voxagent.speech.audio-chunk",
        schemaVersion: 1,
        streamId: created.streamId,
        chunkIndex: input.chunkIndex,
        acceptedBytes: input.audio.byteLength,
        totalBytes: (input.chunkIndex + 1) * input.audio.byteLength,
        durationMs: 0,
        nextChunkIndex: input.chunkIndex + 1,
      })
    ),
    complete: vi.fn(async () => success(terminal("stream.completed"))),
    cancel: vi.fn(async () => success(terminal("stream.cancelled"))),
    ...overrides,
  };
}

describe("SpeechPcmTransport", () => {
  it("splits buffers into ordered chunks and forwards the actual native format", async () => {
    const api = apiMock();
    const transport = new SpeechPcmTransport(api, {
      maxChunkBytes: 4,
      maxTotalBytes: 16,
      maxDurationMs: 1_000,
    });
    const stream = await transport.start({
      sessionId: "conversation-1",
      languageHints: ["en", "zh-CN"],
    });

    await transport.accept({
      data: Uint8Array.from([1, 0, 2, 0, 3, 0]).buffer,
      sampleRate: 16_000,
      channels: 1,
      timestamp: 0,
    });
    await transport.complete();

    const sends = vi.mocked(api.sendChunk).mock.calls.map(([input]) => input);
    expect(sends.map(({ chunkIndex }) => chunkIndex)).toEqual([0, 1]);
    expect(sends.map(({ sampleRate }) => sampleRate)).toEqual([16_000, 16_000]);
    expect(sends.map(({ audio }) => Array.from(new Uint8Array(audio)))).toEqual([
      [1, 0, 2, 0],
      [3, 0],
    ]);
    expect(api.complete).toHaveBeenCalledTimes(1);
    expect(stream).toEqual(created);
  });

  it("retries one idempotent chunk with the same index and bytes", async () => {
    const offline: ApiFailure = {
      ok: false,
      offline: true,
      code: "ENGINE_OFFLINE",
    };
    const sendChunk = vi
      .fn<SpeechPcmApi["sendChunk"]>()
      .mockResolvedValueOnce(offline)
      .mockImplementation(async (input) =>
        success({
          schema: "voxagent.speech.audio-chunk",
          schemaVersion: 1,
          streamId: created.streamId,
          chunkIndex: input.chunkIndex,
          acceptedBytes: input.audio.byteLength,
          totalBytes: input.audio.byteLength,
          durationMs: 0,
          nextChunkIndex: input.chunkIndex + 1,
        })
      );
    const api = apiMock({ sendChunk });
    const transport = new SpeechPcmTransport(api, {
      retryDelayMs: 0,
      maxSendAttempts: 2,
    });
    await transport.start({ sessionId: "conversation-1", languageHints: ["en"] });
    await transport.accept({
      data: Uint8Array.from([1, 0, 2, 0]).buffer,
      sampleRate: 16_000,
      channels: 1,
      timestamp: 0,
    });

    expect(sendChunk).toHaveBeenCalledTimes(2);
    const [first] = sendChunk.mock.calls[0];
    const [second] = sendChunk.mock.calls[1];
    expect(second.chunkIndex).toBe(first.chunkIndex);
    expect(Array.from(new Uint8Array(second.audio))).toEqual(
      Array.from(new Uint8Array(first.audio))
    );
  });

  it("fails closed when format changes or a client bound would be exceeded", async () => {
    const api = apiMock();
    const transport = new SpeechPcmTransport(api, {
      maxTotalBytes: 4,
      maxDurationMs: 1_000,
    });
    await transport.start({ sessionId: "conversation-1", languageHints: ["en"] });
    await transport.accept({
      data: Uint8Array.from([1, 0, 2, 0]).buffer,
      sampleRate: 16_000,
      channels: 1,
      timestamp: 0,
    });

    await expect(
      transport.accept({
        data: Uint8Array.from([3, 0]).buffer,
        sampleRate: 48_000,
        channels: 1,
        timestamp: 0.1,
      })
    ).rejects.toThrow(/format/i);
    expect(api.sendChunk).toHaveBeenCalledTimes(1);
  });

  it("reserves byte bounds before slow queued sends can copy more audio", async () => {
    const api = apiMock();
    const transport = new SpeechPcmTransport(api, {
      maxTotalBytes: 4,
      maxDurationMs: 1_000,
    });
    await transport.start({ sessionId: "conversation-1", languageHints: ["en"] });

    const accepted = transport.accept({
      data: Uint8Array.from([1, 0, 2, 0]).buffer,
      sampleRate: 16_000,
      channels: 1,
      timestamp: 0,
    });
    const rejected = transport.accept({
      data: Uint8Array.from([3, 0]).buffer,
      sampleRate: 16_000,
      channels: 1,
      timestamp: 0.1,
    });

    await expect(rejected).rejects.toThrow(/bound/i);
    await accepted;
    expect(api.sendChunk).toHaveBeenCalledTimes(1);
  });

  it("aborts an in-flight chunk before cancelling the stream", async () => {
    let observedSignal: AbortSignal | undefined;
    const sendChunk = vi.fn<SpeechPcmApi["sendChunk"]>(async (input) => {
      observedSignal = input.signal;
      return await new Promise<WriteResult<SpeechAudioChunkAck>>((resolve) => {
        input.signal.addEventListener(
          "abort",
          () =>
            resolve({
              ok: false,
              offline: true,
              code: "ENGINE_OFFLINE",
            }),
          { once: true }
        );
      });
    });
    const api = apiMock({ sendChunk });
    const transport = new SpeechPcmTransport(api);
    await transport.start({ sessionId: "conversation-1", languageHints: ["en"] });
    const pending = transport.accept({
      data: Uint8Array.from([1, 0, 2, 0]).buffer,
      sampleRate: 16_000,
      channels: 1,
      timestamp: 0,
    });
    await vi.waitFor(() => expect(observedSignal).toBeDefined());

    await transport.cancel();
    await expect(pending).rejects.toThrow(/cancel/i);

    expect(observedSignal?.aborted).toBe(true);
    expect(api.cancel).toHaveBeenCalledTimes(1);
    expect(api.complete).not.toHaveBeenCalled();
  });
});

describe("SpeechAudioStreamSession", () => {
  it("releases the native microphone on stop, cancel, and start failure", async () => {
    const nativeStream = {
      start: vi.fn(async () => undefined),
      stop: vi.fn(),
    };
    const transport = new SpeechPcmTransport(apiMock());
    const session = new SpeechAudioStreamSession(
      transport,
      nativeStream,
      vi.fn(async () => ({ granted: true }))
    );
    await session.start({
      sessionId: "conversation-1",
      languageHints: ["en"],
    });
    await session.stop();
    expect(nativeStream.stop).toHaveBeenCalledTimes(1);

    const secondTransport = new SpeechPcmTransport(apiMock());
    const second = new SpeechAudioStreamSession(
      secondTransport,
      nativeStream,
      vi.fn(async () => ({ granted: true }))
    );
    await second.start({ sessionId: "conversation-2", languageHints: ["en"] });
    await second.cancel();
    expect(nativeStream.stop).toHaveBeenCalledTimes(2);

    const denied = new SpeechAudioStreamSession(
      new SpeechPcmTransport(apiMock()),
      nativeStream,
      vi.fn(async () => ({ granted: false }))
    );
    await expect(
      denied.start({ sessionId: "conversation-3", languageHints: ["en"] })
    ).rejects.toThrow(/permission/i);
    expect(nativeStream.start).toHaveBeenCalledTimes(2);
  });

  it("cancels safely while stream creation is still in flight", async () => {
    let startWasCaptured = false;
    let releaseStart: (value: WriteResult<SpeechStreamCreated>) => void = () => {
      throw new Error("start resolver was not captured");
    };
    const api = apiMock({
      start: vi.fn(
        async () =>
          await new Promise<WriteResult<SpeechStreamCreated>>((resolve) => {
            releaseStart = resolve;
            startWasCaptured = true;
          })
      ),
    });
    const nativeStream = {
      start: vi.fn(async () => undefined),
      stop: vi.fn(),
    };
    const session = new SpeechAudioStreamSession(
      new SpeechPcmTransport(api),
      nativeStream,
      vi.fn(async () => ({ granted: true }))
    );
    const pendingStart = session.start({
      sessionId: "conversation-race",
      languageHints: ["en"],
    });
    await vi.waitFor(() => expect(startWasCaptured).toBe(true));
    const pendingCancel = session.cancel();
    releaseStart(success(created));

    await expect(pendingStart).rejects.toThrow(/cancel/i);
    await pendingCancel;
    expect(nativeStream.start).not.toHaveBeenCalled();
    expect(api.cancel).toHaveBeenCalledTimes(1);
  });
});
