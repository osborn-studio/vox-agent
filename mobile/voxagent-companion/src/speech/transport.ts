import {
  cancelSpeechStream,
  completeSpeechStream,
  sendSpeechAudioChunk,
  startSpeechStream,
  type SpeechAudioChunkAck,
  type SpeechStreamCreated,
  type SpeechTerminalEvent,
  type WriteResult,
} from "../api/client";

const DEFAULT_MAX_CHUNK_BYTES = 256 * 1024;
const DEFAULT_MAX_TOTAL_BYTES = 6 * 1024 * 1024;
const DEFAULT_MAX_DURATION_MS = 60_000;
const DEFAULT_MAX_SEND_ATTEMPTS = 2;
const DEFAULT_RETRY_DELAY_MS = 100;

export interface NativePcmBuffer {
  data: ArrayBuffer;
  sampleRate: number;
  channels: number;
  timestamp: number;
}

export interface SpeechPcmApi {
  start(input: {
    sessionId: string;
    languageHints: string[];
  }): Promise<WriteResult<SpeechStreamCreated>>;
  sendChunk(input: {
    streamId: string;
    resumeToken: string;
    chunkIndex: number;
    sampleRate: number;
    channels: 1;
    audio: ArrayBuffer;
    signal: AbortSignal;
  }): Promise<WriteResult<SpeechAudioChunkAck>>;
  complete(
    streamId: string,
    resumeToken: string
  ): Promise<WriteResult<SpeechTerminalEvent>>;
  cancel(
    streamId: string,
    resumeToken: string
  ): Promise<WriteResult<SpeechTerminalEvent>>;
}

export interface SpeechPcmTransportOptions {
  maxChunkBytes?: number;
  maxTotalBytes?: number;
  maxDurationMs?: number;
  maxSendAttempts?: number;
  retryDelayMs?: number;
}

export class SpeechPcmTransportError extends Error {}

const mobileSpeechApi: SpeechPcmApi = {
  start: startSpeechStream,
  sendChunk: sendSpeechAudioChunk,
  complete: completeSpeechStream,
  cancel: cancelSpeechStream,
};

export class SpeechPcmTransport {
  private readonly maxChunkBytes: number;
  private readonly maxTotalBytes: number;
  private readonly maxDurationMs: number;
  private readonly maxSendAttempts: number;
  private readonly retryDelayMs: number;
  private stream: SpeechStreamCreated | null = null;
  private accepting = false;
  private cancelled = false;
  private nextChunkIndex = 0;
  private totalBytes = 0;
  private reservedBytes = 0;
  private sampleRate: number | null = null;
  private queue: Promise<void> = Promise.resolve();
  private failure: SpeechPcmTransportError | null = null;
  private currentRequest: AbortController | null = null;

  constructor(
    private readonly api: SpeechPcmApi = mobileSpeechApi,
    options: SpeechPcmTransportOptions = {}
  ) {
    this.maxChunkBytes = boundedPositiveEvenInteger(
      options.maxChunkBytes ?? DEFAULT_MAX_CHUNK_BYTES,
      "maxChunkBytes",
      DEFAULT_MAX_CHUNK_BYTES
    );
    this.maxTotalBytes = boundedPositiveInteger(
      options.maxTotalBytes ?? DEFAULT_MAX_TOTAL_BYTES,
      "maxTotalBytes",
      DEFAULT_MAX_TOTAL_BYTES
    );
    this.maxDurationMs = boundedPositiveInteger(
      options.maxDurationMs ?? DEFAULT_MAX_DURATION_MS,
      "maxDurationMs",
      DEFAULT_MAX_DURATION_MS
    );
    this.maxSendAttempts = boundedPositiveInteger(
      options.maxSendAttempts ?? DEFAULT_MAX_SEND_ATTEMPTS,
      "maxSendAttempts",
      3
    );
    this.retryDelayMs = boundedNonNegativeInteger(
      options.retryDelayMs ?? DEFAULT_RETRY_DELAY_MS,
      "retryDelayMs",
      5_000
    );
  }

  async start(input: {
    sessionId: string;
    languageHints: string[];
  }): Promise<SpeechStreamCreated> {
    if (this.stream || this.accepting) {
      throw new SpeechPcmTransportError("speech transport is already started");
    }
    this.nextChunkIndex = 0;
    this.totalBytes = 0;
    this.reservedBytes = 0;
    this.sampleRate = null;
    this.failure = null;
    this.queue = Promise.resolve();
    const result = await this.api.start(input);
    if (!result.ok) {
      throw new SpeechPcmTransportError(
        `speech stream start failed: ${result.code}`
      );
    }
    this.stream = result.data;
    this.accepting = true;
    this.cancelled = false;
    return result.data;
  }

  accept(buffer: NativePcmBuffer): Promise<void> {
    if (this.failure) return Promise.reject(this.failure);
    if (!this.accepting || !this.stream) {
      return Promise.reject(
        new SpeechPcmTransportError("speech transport is not accepting audio")
      );
    }
    if (
      !Number.isSafeInteger(buffer.sampleRate) ||
      buffer.sampleRate < 8_000 ||
      buffer.sampleRate > 48_000 ||
      buffer.channels !== 1 ||
      buffer.data.byteLength < 2 ||
      buffer.data.byteLength % 2 !== 0
    ) {
      return Promise.reject(
        new SpeechPcmTransportError("invalid native PCM format")
      );
    }
    if (this.sampleRate !== null && this.sampleRate !== buffer.sampleRate) {
      return Promise.reject(
        new SpeechPcmTransportError(
          "native PCM format changed within a speech stream"
        )
      );
    }
    const nextReservedBytes = this.reservedBytes + buffer.data.byteLength;
    const reservedDurationMs =
      (nextReservedBytes * 1_000) /
      (buffer.sampleRate * buffer.channels * 2);
    if (
      nextReservedBytes > this.maxTotalBytes ||
      reservedDurationMs > this.maxDurationMs
    ) {
      return Promise.reject(
        new SpeechPcmTransportError("speech audio client bound exceeded")
      );
    }
    this.sampleRate ??= buffer.sampleRate;
    this.reservedBytes = nextReservedBytes;
    const copied: NativePcmBuffer = {
      ...buffer,
      data: buffer.data.slice(0),
    };
    const operation = this.queue.then(() => this.sendBuffer(copied));
    this.queue = operation.catch((error: unknown) => {
      this.failure =
        error instanceof SpeechPcmTransportError
          ? error
          : new SpeechPcmTransportError("speech audio transport failed");
    });
    return operation;
  }

  async complete(): Promise<void> {
    this.requireStarted();
    this.accepting = false;
    await this.queue;
    if (this.failure) throw this.failure;
    const stream = this.requireStarted();
    const result = await this.api.complete(stream.streamId, stream.resumeToken);
    if (!result.ok) {
      throw new SpeechPcmTransportError(
        `speech stream completion failed: ${result.code}`
      );
    }
    this.clear();
  }

  async cancel(): Promise<void> {
    const stream = this.stream;
    if (!stream) return;
    this.accepting = false;
    this.cancelled = true;
    this.currentRequest?.abort();
    await this.queue;
    const result = await this.api.cancel(stream.streamId, stream.resumeToken);
    this.clear();
    if (!result.ok) {
      throw new SpeechPcmTransportError(
        `speech stream cancellation failed: ${result.code}`
      );
    }
  }

  private async sendBuffer(buffer: NativePcmBuffer): Promise<void> {
    const stream = this.requireStarted();
    if (!this.accepting || this.cancelled) {
      throw new SpeechPcmTransportError("speech audio transport was cancelled");
    }

    for (
      let offset = 0;
      offset < buffer.data.byteLength;
      offset += this.maxChunkBytes
    ) {
      if (this.cancelled) {
        throw new SpeechPcmTransportError(
          "speech audio transport was cancelled"
        );
      }
      const end = Math.min(
        buffer.data.byteLength,
        offset + this.maxChunkBytes
      );
      const audio = buffer.data.slice(offset, end);
      await this.sendWithRetry({
        streamId: stream.streamId,
        resumeToken: stream.resumeToken,
        chunkIndex: this.nextChunkIndex,
        sampleRate: buffer.sampleRate,
        channels: 1,
        audio,
      });
      this.nextChunkIndex += 1;
      this.totalBytes += audio.byteLength;
    }
  }

  private async sendWithRetry(
    input: Omit<Parameters<SpeechPcmApi["sendChunk"]>[0], "signal">
  ): Promise<void> {
    for (let attempt = 1; attempt <= this.maxSendAttempts; attempt += 1) {
      if (this.cancelled) {
        throw new SpeechPcmTransportError(
          "speech audio transport was cancelled"
        );
      }
      const controller = new AbortController();
      this.currentRequest = controller;
      const result = await this.api.sendChunk({
        ...input,
        signal: controller.signal,
      });
      if (this.currentRequest === controller) this.currentRequest = null;
      if (result.ok) return;
      if (this.cancelled || controller.signal.aborted) {
        throw new SpeechPcmTransportError(
          "speech audio transport was cancelled"
        );
      }
      const retriable =
        result.offline ||
        result.status === 429 ||
        (result.status !== undefined && result.status >= 500);
      if (!retriable || attempt === this.maxSendAttempts) {
        throw new SpeechPcmTransportError(
          `speech audio chunk failed: ${result.code}`
        );
      }
      await delay(this.retryDelayMs);
    }
  }

  private requireStarted(): SpeechStreamCreated {
    if (!this.stream) {
      throw new SpeechPcmTransportError("speech transport is not started");
    }
    return this.stream;
  }

  private clear(): void {
    this.stream = null;
    this.accepting = false;
    this.cancelled = false;
    this.currentRequest = null;
    this.reservedBytes = 0;
  }
}

export interface NativeAudioStream {
  start(): Promise<void>;
  stop(): void;
}

export class SpeechAudioStreamSession {
  private started = false;
  private cancelRequested = false;
  private startInFlight: Promise<SpeechStreamCreated> | null = null;

  constructor(
    private readonly transport: SpeechPcmTransport,
    private readonly nativeStream: NativeAudioStream,
    private readonly requestPermission: () => Promise<{ granted: boolean }>
  ) {}

  start(input: {
    sessionId: string;
    languageHints: string[];
  }): Promise<SpeechStreamCreated> {
    if (this.startInFlight || this.started) {
      return Promise.reject(
        new SpeechPcmTransportError("speech audio session is already started")
      );
    }
    const operation = this.startInternal(input);
    this.startInFlight = operation;
    const clearStart = () => {
      if (this.startInFlight === operation) this.startInFlight = null;
    };
    void operation.then(clearStart, clearStart);
    return operation;
  }

  private async startInternal(input: {
    sessionId: string;
    languageHints: string[];
  }): Promise<SpeechStreamCreated> {
    const permission = await this.requestPermission();
    if (!permission.granted) {
      throw new SpeechPcmTransportError(
        "microphone permission is required for speech"
      );
    }
    if (this.cancelRequested) {
      throw new SpeechPcmTransportError("speech audio session was cancelled");
    }
    const created = await this.transport.start(input);
    if (this.cancelRequested) {
      await this.transport.cancel();
      throw new SpeechPcmTransportError("speech audio session was cancelled");
    }
    try {
      await this.nativeStream.start();
      if (this.cancelRequested) {
        this.nativeStream.stop();
        await this.transport.cancel();
        throw new SpeechPcmTransportError("speech audio session was cancelled");
      }
      this.started = true;
      return created;
    } catch (error) {
      await this.transport.cancel();
      throw error;
    }
  }

  accept(buffer: NativePcmBuffer): Promise<void> {
    return this.transport.accept(buffer);
  }

  async stop(): Promise<void> {
    if (this.started) this.nativeStream.stop();
    this.started = false;
    try {
      await this.transport.complete();
    } catch (error) {
      await this.transport.cancel();
      throw error;
    }
  }

  async cancel(): Promise<void> {
    this.cancelRequested = true;
    if (this.started) this.nativeStream.stop();
    this.started = false;
    const starting = this.startInFlight;
    if (starting) {
      try {
        await starting;
      } catch {
        // startInternal performs the authenticated transport cancellation.
      }
      return;
    }
    await this.transport.cancel();
  }
}

function boundedPositiveInteger(
  value: number,
  name: string,
  maximum: number
): number {
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
    throw new SpeechPcmTransportError(`${name} is outside the supported range`);
  }
  return value;
}

function boundedPositiveEvenInteger(
  value: number,
  name: string,
  maximum: number
): number {
  const result = boundedPositiveInteger(value, name, maximum);
  if (result % 2 !== 0) {
    throw new SpeechPcmTransportError(`${name} must preserve PCM frames`);
  }
  return result;
}

function boundedNonNegativeInteger(
  value: number,
  name: string,
  maximum: number
): number {
  if (!Number.isSafeInteger(value) || value < 0 || value > maximum) {
    throw new SpeechPcmTransportError(`${name} is outside the supported range`);
  }
  return value;
}

function delay(milliseconds: number): Promise<void> {
  if (milliseconds === 0) return Promise.resolve();
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}
