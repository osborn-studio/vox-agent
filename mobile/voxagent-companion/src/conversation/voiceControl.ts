import type {
  ConversationParticipantRole,
  SpeechStreamCreated,
} from "../api/client";
import {
  createConversationSpeechEventAdapter,
  type ConversationSpeechEventAdapter,
  type ConversationSpeechEventAdapterConfig,
  type ConversationSpeechSyncResult,
} from "./speechEvents";
import {
  canCancelConversationVoice,
  canRetryConversationVoice,
  canStartConversationVoice,
  canStopConversationVoice,
  conversationVoiceReducer,
  initialConversationVoiceState,
  type ConversationVoiceAction,
  type ConversationVoiceRetryPhase,
  type ConversationVoiceState,
} from "./voiceState";

const FINAL_SYNC_ATTEMPTS = 3;

export interface ConversationVoiceStartInput {
  sessionId: string;
  participantRole: ConversationParticipantRole;
  participantAccessToken: string;
  sourceLanguage: string;
  targetLanguage: string;
  allowRemoteTranslation: boolean;
}

export interface ConversationVoiceAudio {
  start(input: {
    sessionId: string;
    languageHints: string[];
  }): Promise<SpeechStreamCreated>;
  stop(): Promise<void>;
  cancel(): Promise<void>;
}

export interface ConversationVoiceController {
  getState(): ConversationVoiceState;
  start(input: ConversationVoiceStartInput): Promise<void>;
  poll(): Promise<void>;
  stop(): Promise<void>;
  cancel(): Promise<void>;
  retry(): Promise<void>;
  reconcile(task: () => Promise<void>): Promise<void>;
  transportFailed(): void;
  reset(): Promise<void>;
}

export interface ConversationVoiceControllerOptions {
  audio: ConversationVoiceAudio;
  createAdapter?: (
    config: ConversationSpeechEventAdapterConfig
  ) => ConversationSpeechEventAdapter;
  onConversationChanged?: () => void | Promise<void>;
  onStateChange?: (state: ConversationVoiceState) => void;
}

export function createConversationVoiceController(
  options: ConversationVoiceControllerOptions
): ConversationVoiceController {
  let state = initialConversationVoiceState;
  let adapter: ConversationSpeechEventAdapter | null = null;
  let lastInput: ConversationVoiceStartInput | null = null;
  let retryAction: (() => Promise<void>) | null = null;
  let operation: Promise<void> | null = null;
  let pollInFlight: Promise<ConversationSpeechSyncResult> | null = null;
  const createAdapter =
    options.createAdapter ?? createConversationSpeechEventAdapter;

  const dispatch = (action: ConversationVoiceAction): void => {
    const next = conversationVoiceReducer(state, action);
    if (next === state) return;
    state = next;
    options.onStateChange?.(state);
  };

  const trackOperation = (task: Promise<void>): Promise<void> => {
    let tracked: Promise<void>;
    tracked = task.finally(() => {
      if (operation === tracked) operation = null;
    });
    operation = tracked;
    return tracked;
  };

  const exclusive = (task: () => Promise<void>): Promise<void> => {
    if (operation) return operation;
    return trackOperation(task());
  };

  const readAdapter = (): Promise<ConversationSpeechSyncResult> => {
    if (!adapter) {
      return Promise.resolve({
        ok: false,
        cursor: 0,
        stage: "speech",
        code: "SPEECH_NOT_STARTED",
        retriable: false,
        offline: false,
      });
    }
    if (pollInFlight) return pollInFlight;
    pollInFlight = adapter.poll().finally(() => {
      pollInFlight = null;
    });
    return pollInFlight;
  };

  const notifyIfChanged = async (
    result: Extract<ConversationSpeechSyncResult, { ok: true }>
  ): Promise<void> => {
    if (result.publishedDrafts > 0 || result.publishedTurns > 0) {
      await options.onConversationChanged?.();
    }
  };

  const fail = (
    error: string,
    retriable: boolean,
    retryPhase: ConversationVoiceRetryPhase,
    retry: () => Promise<void>
  ): void => {
    dispatch({ type: "FAIL", error, retriable, retryPhase });
    retryAction = state.retriable ? retry : null;
  };

  const startInternal = async (): Promise<void> => {
    const input = lastInput;
    if (!input) {
      fail(
        "Speech configuration is unavailable.",
        false,
        "starting",
        startInternal
      );
      return;
    }
    try {
      const created = await options.audio.start({
        sessionId: input.sessionId,
        languageHints: [input.sourceLanguage, input.targetLanguage],
      });
      adapter = createAdapter({
        sessionId: input.sessionId,
        participantRole: input.participantRole,
        conversationAccessToken: input.participantAccessToken,
        streamId: created.streamId,
        streamResumeToken: created.resumeToken,
        sourceLanguage: input.sourceLanguage,
        targetLanguage: input.targetLanguage,
        allowRemoteTranslation: input.allowRemoteTranslation,
        initialAfterSequence: 0,
      });
      retryAction = null;
      dispatch({ type: "STARTED" });
    } catch {
      adapter = null;
      await options.audio.cancel().catch(() => undefined);
      fail(
        "Microphone could not start. Check permission and connection.",
        true,
        "starting",
        startInternal
      );
    }
  };

  const finalizeInternal = async (): Promise<void> => {
    for (let attempt = 0; attempt < FINAL_SYNC_ATTEMPTS; attempt += 1) {
      const result = await readAdapter();
      if (!result.ok) {
        fail(
          result.offline
            ? "Final speech is waiting for the engine connection."
            : "Translation could not finish.",
          result.retriable,
          "finalizing",
          finalizeInternal
        );
        return;
      }
      await notifyIfChanged(result);
      if (result.terminal !== null) {
        adapter = null;
        retryAction = null;
        dispatch({ type: "COMPLETE" });
        return;
      }
    }
    fail(
      "Final speech is still processing.",
      true,
      "finalizing",
      finalizeInternal
    );
  };

  const controller: ConversationVoiceController = {
    getState() {
      return state;
    },
    start(input) {
      if (!canStartConversationVoice(state)) return Promise.resolve();
      lastInput = { ...input };
      dispatch({ type: "BEGIN" });
      return exclusive(startInternal);
    },
    async poll() {
      if (!["listening", "reconnecting"].includes(state.phase) || !adapter) {
        return;
      }
      const result = await readAdapter();
      if (result.ok) {
        await notifyIfChanged(result);
        if (state.phase === "reconnecting") dispatch({ type: "RESUME" });
        return;
      }
      if (result.retriable) {
        dispatch({ type: "RECONNECT" });
        return;
      }
      await options.audio.cancel().catch(() => undefined);
      adapter = null;
      fail(
        "Live speech could not continue.",
        true,
        "starting",
        startInternal
      );
    },
    stop() {
      if (!canStopConversationVoice(state)) return Promise.resolve();
      dispatch({ type: "STOP" });
      return exclusive(async () => {
        try {
          await options.audio.stop();
          dispatch({ type: "FINALIZE" });
          await finalizeInternal();
        } catch {
          await options.audio.cancel().catch(() => undefined);
          adapter = null;
          fail(
            "Speech could not be finalized.",
            true,
            "starting",
            startInternal
          );
        }
      });
    },
    cancel() {
      if (!canCancelConversationVoice(state)) return Promise.resolve();
      const pending = operation;
      dispatch({ type: "CANCEL" });
      return trackOperation((async () => {
        await options.audio.cancel().catch(() => undefined);
        await pending?.catch(() => undefined);
        adapter = null;
        retryAction = null;
        dispatch({ type: "COMPLETE" });
      })());
    },
    retry() {
      if (!canRetryConversationVoice(state) || !retryAction) {
        return Promise.resolve();
      }
      const action = retryAction;
      retryAction = null;
      dispatch({ type: "RETRY" });
      return exclusive(action);
    },
    reconcile(task) {
      if (operation) {
        const pending = operation;
        return pending.then(
          () => controller.reconcile(task),
          () => controller.reconcile(task)
        );
      }
      if (!canStartConversationVoice(state)) return Promise.resolve();
      dispatch({ type: "RECONCILE" });
      return exclusive(async () => {
        try {
          await task();
        } finally {
          dispatch({ type: "COMPLETE" });
        }
      });
    },
    transportFailed() {
      if (!["starting", "listening", "reconnecting"].includes(state.phase)) {
        return;
      }
      adapter = null;
      fail(
        "Speech transport was interrupted.",
        true,
        "starting",
        startInternal
      );
    },
    reset() {
      return (async () => {
        if (canCancelConversationVoice(state)) {
          await controller.cancel();
        } else {
          await operation?.catch(() => undefined);
        }
        adapter = null;
        lastInput = null;
        retryAction = null;
        dispatch({ type: "RESET" });
      })();
    },
  };
  return controller;
}
