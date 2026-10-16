import { useCallback, useEffect, useRef, useState } from "react";
import {
  requestRecordingPermissionsAsync,
  useAudioStream,
  type AudioStreamBuffer,
} from "expo-audio";

import {
  SpeechAudioStreamSession,
  SpeechPcmTransport,
  type SpeechPcmTransportError,
} from "./transport";

export type SpeechAudioPhase =
  | "idle"
  | "starting"
  | "streaming"
  | "stopping"
  | "failed";

export interface StartSpeechAudioInput {
  sessionId: string;
  languageHints: string[];
}

export function useSpeechAudioStream(options?: {
  onFailure?: (error: SpeechPcmTransportError) => void;
}) {
  const [phase, setPhase] = useState<SpeechAudioPhase>("idle");
  const sessionRef = useRef<SpeechAudioStreamSession | null>(null);
  const mountedRef = useRef(true);

  const onFailure = options?.onFailure;
  const fail = useCallback(
    (error: unknown) => {
      if (!mountedRef.current) return;
      setPhase("failed");
      onFailure?.(
        error instanceof Error
          ? (error as SpeechPcmTransportError)
          : new Error("speech audio transport failed")
      );
    },
    [onFailure]
  );

  const onBuffer = useCallback(
    (buffer: AudioStreamBuffer) => {
      const session = sessionRef.current;
      if (!session) return;
      void session.accept(buffer).catch(async (error: unknown) => {
        if (sessionRef.current === session) sessionRef.current = null;
        try {
          await session.cancel();
        } catch {
          // The original transport error remains the user-facing failure.
        } finally {
          fail(error);
        }
      });
    },
    [fail]
  );

  const { stream, isStreaming } = useAudioStream({
    sampleRate: 16_000,
    channels: 1,
    encoding: "int16",
    onBuffer,
  });

  const start = useCallback(
    async (input: StartSpeechAudioInput) => {
      if (sessionRef.current) {
        throw new Error("speech audio session is already active");
      }
      setPhase("starting");
      const session = new SpeechAudioStreamSession(
        new SpeechPcmTransport(),
        stream,
        requestRecordingPermissionsAsync
      );
      sessionRef.current = session;
      try {
        const created = await session.start(input);
        if (sessionRef.current === session && mountedRef.current) {
          setPhase("streaming");
        }
        return created;
      } catch (error) {
        if (sessionRef.current === session) sessionRef.current = null;
        fail(error);
        throw error;
      }
    },
    [fail, stream]
  );

  const stop = useCallback(async () => {
    const session = sessionRef.current;
    if (!session) return;
    sessionRef.current = null;
    setPhase("stopping");
    try {
      await session.stop();
      if (mountedRef.current) setPhase("idle");
    } catch (error) {
      fail(error);
      throw error;
    }
  }, [fail]);

  const cancel = useCallback(async () => {
    const session = sessionRef.current;
    if (!session) return;
    sessionRef.current = null;
    try {
      await session.cancel();
      if (mountedRef.current) setPhase("idle");
    } catch (error) {
      fail(error);
      throw error;
    }
  }, [fail]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const session = sessionRef.current;
      sessionRef.current = null;
      if (session) void session.cancel().catch(() => undefined);
    };
  }, []);

  return {
    phase,
    isStreaming,
    start,
    stop,
    cancel,
  };
}
