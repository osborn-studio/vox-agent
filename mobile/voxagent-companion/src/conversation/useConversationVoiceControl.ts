import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useSpeechAudioStream } from "../speech/useSpeechAudioStream";
import {
  createConversationVoiceController,
  type ConversationVoiceController,
  type ConversationVoiceControllerOptions,
  type ConversationVoiceStartInput,
} from "./voiceControl";
import { initialConversationVoiceState } from "./voiceState";

const LIVE_POLL_INTERVAL_MS = 300;

export function useConversationVoiceControl(options: {
  onConversationChanged?: () => void | Promise<void>;
}) {
  const [state, setState] = useState(initialConversationVoiceState);
  const controllerRef = useRef<ConversationVoiceController | null>(null);
  const mountedRef = useRef(true);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const onTransportFailure = useCallback(() => {
    controllerRef.current?.transportFailed();
  }, []);
  const audioOptions = useMemo(
    () => ({ onFailure: onTransportFailure }),
    [onTransportFailure]
  );
  const audio = useSpeechAudioStream(audioOptions);
  const audioRef = useRef(audio);
  audioRef.current = audio;

  if (!controllerRef.current) {
    const controllerOptions: ConversationVoiceControllerOptions = {
      audio: {
        start: (input) => audioRef.current.start(input),
        stop: () => audioRef.current.stop(),
        cancel: () => audioRef.current.cancel(),
      },
      onConversationChanged: () =>
        optionsRef.current.onConversationChanged?.(),
      onStateChange: (nextState) => {
        if (mountedRef.current) setState(nextState);
      },
    };
    controllerRef.current = createConversationVoiceController(
      controllerOptions
    );
  }

  useEffect(() => {
    if (!["listening", "reconnecting"].includes(state.phase)) return;
    const timer = setInterval(
      () => void controllerRef.current?.poll(),
      LIVE_POLL_INTERVAL_MS
    );
    return () => clearInterval(timer);
  }, [state.phase]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      void controllerRef.current?.reset();
    };
  }, []);

  const start = useCallback((input: ConversationVoiceStartInput) => {
    return controllerRef.current?.start(input) ?? Promise.resolve();
  }, []);
  const stop = useCallback(
    () => controllerRef.current?.stop() ?? Promise.resolve(),
    []
  );
  const cancel = useCallback(
    () => controllerRef.current?.cancel() ?? Promise.resolve(),
    []
  );
  const retry = useCallback(
    () => controllerRef.current?.retry() ?? Promise.resolve(),
    []
  );
  const reconcile = useCallback(
    (task: () => Promise<void>) =>
      controllerRef.current?.reconcile(task) ?? Promise.resolve(),
    []
  );
  const reset = useCallback(
    () => controllerRef.current?.reset() ?? Promise.resolve(),
    []
  );

  return {
    state,
    nativePhase: audio.phase,
    isNativeStreaming: audio.isStreaming,
    start,
    stop,
    cancel,
    retry,
    reconcile,
    reset,
  };
}
