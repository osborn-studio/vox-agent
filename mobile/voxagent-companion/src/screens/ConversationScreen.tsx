import React, {
  useCallback,
  useEffect,
  useReducer,
  useRef,
  useState,
} from "react";
import {
  AccessibilityInfo,
  AppState,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { useLinkingURL } from "expo-linking";
import QRCode from "react-native-qrcode-svg";

import {
  claimConversationInvite,
  createConversationSession,
  endConversationSession,
  readConversationLive,
  replayConversationTurns,
  type ConversationInvite,
  type ConversationLiveDraft,
  type ConversationMode,
  type ConversationParticipantRole,
  type ConversationSession,
  type ConversationTurn,
} from "../api/client";
import {
  conversationReducer,
  initialConversationState,
  parseConversationInvite,
  remainingConversationSeconds,
} from "../conversation/state";
import { useConversationVoiceControl } from "../conversation/useConversationVoiceControl";
import {
  canCancelConversationVoice,
  canRetryConversationVoice,
  canStartConversationVoice,
  canStopConversationVoice,
  presentConversationVoice,
  type ConversationVoiceState,
} from "../conversation/voiceState";
import { colors, radius, spacing, typography } from "../theme";

const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "zh-CN", label: "中文" },
  { code: "ja", label: "日本語" },
  { code: "es", label: "Español" },
];
const LIVE_REFRESH_INTERVAL_MS = 500;

type VisibleInvite = Pick<ConversationInvite, "qrPayload" | "expiresAt">;

export function retainConversationInvite(
  invite: VisibleInvite | null,
  connection: string
): VisibleInvite | null {
  return connection === "WAITING" || connection === "RECONNECTING"
    ? invite
    : null;
}

export default function ConversationScreen() {
  const [mode, setMode] = useState<ConversationMode>("FACE_TO_FACE");
  const [hostLanguage, setHostLanguage] = useState("en");
  const [guestLanguage, setGuestLanguage] = useState("zh-CN");
  const [state, dispatch] = useReducer(
    conversationReducer,
    initialConversationState
  );
  const [invite, setInvite] = useState<VisibleInvite | null>(null);
  const [scannerOpen, setScannerOpen] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [remainingSeconds, setRemainingSeconds] = useState(0);
  const [inviteSeconds, setInviteSeconds] = useState(0);
  const [allowRemoteTranslation, setAllowRemoteTranslation] = useState(false);
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const linkingUrl = useLinkingURL();

  // Participant capabilities intentionally live only in refs. They never enter
  // reducer state, logs, URLs, AsyncStorage, or rendered diagnostic text.
  const hostAccessToken = useRef<string | null>(null);
  const guestAccessToken = useRef<string | null>(null);
  const handledIncomingInvite = useRef(false);
  const sessionRequestInFlight = useRef(false);
  const refreshInFlight = useRef<Promise<void> | null>(null);
  const liveRefreshInFlight = useRef(false);
  const resetVoiceRef = useRef<() => Promise<void>>(
    () => Promise.resolve()
  );

  const clearCapabilities = useCallback(() => {
    hostAccessToken.current = null;
    guestAccessToken.current = null;
  }, []);

  const reset = useCallback(() => {
    void resetVoiceRef.current();
    clearCapabilities();
    handledIncomingInvite.current = false;
    setInvite(null);
    setScannerOpen(false);
    setScanError(null);
    setAllowRemoteTranslation(false);
    dispatch({ type: "RESET" });
  }, [clearCapabilities]);

  const createSession = useCallback(async () => {
    if (sessionRequestInFlight.current) return;
    sessionRequestInFlight.current = true;
    dispatch({ type: "STARTING" });
    setInvite(null);
    setScanError(null);
    const result = await createConversationSession({
      mode,
      hostLanguage,
      guestLanguage,
    });
    sessionRequestInFlight.current = false;
    if (!result.ok) {
      dispatch({
        type: "FAILED",
        error:
          result.status === 401
            ? "Host sign-in is required by the connected engine."
            : result.offline
              ? "Vox Agent engine is offline."
              : "The engine could not create this conversation.",
      });
      return;
    }
    hostAccessToken.current = result.data.hostAccessToken;
    guestAccessToken.current = result.data.localGuestAccessToken;
    setInvite(
      result.data.invite
        ? {
            qrPayload: result.data.invite.qrPayload,
            expiresAt: result.data.invite.expiresAt,
          }
        : null
    );
    dispatch({ type: "CREATED", session: result.data.session });
  }, [guestLanguage, hostLanguage, mode]);

  const joinInvite = useCallback(async (payload: string) => {
    const inviteToken = parseConversationInvite(payload);
    if (!inviteToken) {
      setScanError("This is not a valid Vox Agent conversation invite.");
      return;
    }
    if (sessionRequestInFlight.current) return;
    sessionRequestInFlight.current = true;
    setScannerOpen(false);
    setScanError(null);
    dispatch({ type: "STARTING" });
    const result = await claimConversationInvite(inviteToken);
    sessionRequestInFlight.current = false;
    if (!result.ok) {
      dispatch({
        type: "FAILED",
        error:
          result.status === 404
            ? "This invite expired or was already used."
            : result.offline
              ? "Vox Agent engine is offline. Try the invite again."
              : "The invite could not be claimed.",
      });
      return;
    }
    handledIncomingInvite.current = true;
    guestAccessToken.current = result.data.accessToken;
    dispatch({ type: "JOINED", session: result.data.session });
  }, []);

  // Expo SDK 57 recommends useLinkingURL for the initial URL and subsequent
  // deep-link events: https://docs.expo.dev/versions/latest/sdk/linking/#uselinkingurl
  useEffect(() => {
    if (linkingUrl && !handledIncomingInvite.current) {
      void joinInvite(linkingUrl);
    }
  }, [joinInvite, linkingUrl]);

  const refresh = useCallback(
    async (showReconnectState: boolean) => {
      if (!state.session) return;
      if (refreshInFlight.current) return refreshInFlight.current;
      const token =
        state.role === "HOST"
          ? hostAccessToken.current
          : guestAccessToken.current;
      if (!token) return;
      let operation: Promise<void>;
      operation = (async () => {
        if (showReconnectState) dispatch({ type: "REPLAYING" });
        const result = await replayConversationTurns(
          state.session!.id,
          token,
          Math.max(0, state.nextSequence - 1)
        );
        if (result.ok) {
          dispatch({
            type: "REPLAYED",
            status: result.data.status,
            turns: result.data.turns,
            nextSequence: result.data.nextSequence,
          });
        } else if (result.status === 401 || result.status === 404) {
          clearCapabilities();
          setInvite(null);
          dispatch({ type: "EXPIRED" });
        } else {
          dispatch({
            type: "REPLAY_FAILED",
            error: result.offline
              ? "Network interrupted. Reconnecting…"
              : "Could not refresh this conversation.",
          });
        }
      })().finally(() => {
        if (refreshInFlight.current === operation) {
          refreshInFlight.current = null;
        }
      });
      refreshInFlight.current = operation;
      return operation;
    },
    [clearCapabilities, state.nextSequence, state.role, state.session]
  );

  const refreshLive = useCallback(async () => {
    if (
      !state.session ||
      state.connection !== "ACTIVE" ||
      liveRefreshInFlight.current
    ) {
      return;
    }
    const token =
      state.role === "HOST"
        ? hostAccessToken.current
        : guestAccessToken.current;
    if (!token) return;
    liveRefreshInFlight.current = true;
    const result = await readConversationLive(state.session.id, token);
    liveRefreshInFlight.current = false;
    if (result.ok) {
      dispatch({ type: "LIVE_RECEIVED", snapshot: result.data });
    } else if (result.status === 401 || result.status === 404) {
      clearCapabilities();
      setInvite(null);
      dispatch({ type: "EXPIRED" });
    }
  }, [
    clearCapabilities,
    state.connection,
    state.role,
    state.session,
  ]);

  const refreshAfterSpeech = useCallback(async () => {
    await refresh(false);
    await refreshLive();
  }, [refresh, refreshLive]);
  const {
    state: voiceState,
    start: startVoice,
    stop: stopVoice,
    cancel: cancelVoice,
    retry: retryVoice,
    reconcile: reconcileVoice,
    reset: resetVoice,
  } = useConversationVoiceControl({
    onConversationChanged: refreshAfterSpeech,
  });
  resetVoiceRef.current = resetVoice;

  useEffect(() => {
    if (!state.session || !["WAITING", "ACTIVE", "RECONNECTING"].includes(state.connection)) {
      return;
    }
    const timer = setInterval(() => void refresh(false), 2_500);
    return () => clearInterval(timer);
  }, [refresh, state.connection, state.session]);

  useEffect(() => {
    if (!state.session || state.connection !== "ACTIVE") return;
    void refreshLive();
    const timer = setInterval(
      () => void refreshLive(),
      LIVE_REFRESH_INTERVAL_MS
    );
    return () => clearInterval(timer);
  }, [refreshLive, state.connection, state.session]);

  useEffect(() => {
    setInvite((current) =>
      retainConversationInvite(current, state.connection)
    );
  }, [state.connection]);

  // React Native returns a removable subscription for AppState changes:
  // https://reactnative.dev/docs/appstate#addeventlistener
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (nextState) => {
      if (nextState === "active") {
        void reconcileVoice(() => refresh(true));
      } else {
        void cancelVoice();
      }
    });
    return () => subscription.remove();
  }, [cancelVoice, reconcileVoice, refresh]);

  useEffect(() => {
    if (state.connection === "EXPIRED" || state.connection === "ENDED") {
      void resetVoice();
    }
  }, [resetVoice, state.connection]);

  useEffect(() => {
    if (!state.session) {
      setRemainingSeconds(0);
      setInviteSeconds(0);
      return;
    }
    const update = () => {
      const sessionRemaining = remainingConversationSeconds(
        state.session!.expiresAt
      );
      setRemainingSeconds(sessionRemaining);
      setInviteSeconds(
        invite ? remainingConversationSeconds(invite.expiresAt) : 0
      );
      if (sessionRemaining === 0 && state.connection !== "EXPIRED") {
        clearCapabilities();
        setInvite(null);
        dispatch({ type: "EXPIRED" });
      }
    };
    update();
    const timer = setInterval(update, 1_000);
    return () => clearInterval(timer);
  }, [clearCapabilities, invite, state.connection, state.session]);

  const openScanner = useCallback(async () => {
    setScanError(null);
    if (cameraPermission?.granted) {
      setScannerOpen(true);
      return;
    }
    const permission = await requestCameraPermission();
    if (permission.granted) setScannerOpen(true);
    else setScanError("Camera permission is required to scan a conversation QR.");
  }, [cameraPermission?.granted, requestCameraPermission]);

  const endSession = useCallback(async () => {
    if (!state.session || !hostAccessToken.current) return;
    await cancelVoice();
    const result = await endConversationSession(
      state.session.id,
      hostAccessToken.current
    );
    if (result.ok || result.status === 404) {
      clearCapabilities();
      setInvite(null);
      dispatch({ type: "ENDED" });
    } else {
      dispatch({
        type: "OPERATION_FAILED",
        error: "Could not end the conversation. Try again.",
      });
    }
  }, [cancelVoice, clearCapabilities, state.session]);

  const startSpeaking = useCallback(async () => {
    if (
      !state.session ||
      !state.role ||
      state.connection !== "ACTIVE"
    ) {
      return;
    }
    const participantAccessToken =
      state.role === "HOST"
        ? hostAccessToken.current
        : guestAccessToken.current;
    if (!participantAccessToken) return;
    const sourceLanguage =
      state.role === "HOST"
        ? state.session.hostLanguage
        : state.session.guestLanguage;
    const targetLanguage =
      state.role === "HOST"
        ? state.session.guestLanguage
        : state.session.hostLanguage;
    await startVoice({
      sessionId: state.session.id,
      participantRole: state.role,
      participantAccessToken,
      sourceLanguage,
      targetLanguage,
      allowRemoteTranslation,
    });
  }, [
    allowRemoteTranslation,
    startVoice,
    state.connection,
    state.role,
    state.session,
  ]);

  if (scannerOpen) {
    return (
      <View style={styles.scannerRoot}>
        {/* Expo documents CameraView + qr-only barcodeScannerSettings and
            recommends unmounting inactive previews:
            https://docs.expo.dev/versions/latest/sdk/camera/#cameraview */}
        <CameraView
          style={styles.camera}
          facing="back"
          barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
          onBarcodeScanned={({ data }) => void joinInvite(data)}
        />
        <View style={styles.scanOverlay}>
          <Text style={styles.scanTitle}>Scan Vox Agent invite</Text>
          <Text style={styles.scanHint}>The QR expires after five minutes.</Text>
          <TouchableOpacity style={styles.lightButton} onPress={() => setScannerOpen(false)}>
            <Text style={styles.lightButtonText}>Cancel</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
    >
      <View style={styles.hero}>
        <Text style={styles.eyebrow}>VOX AGENT · LIVE</Text>
        <Text style={styles.title}>Converse across languages</Text>
        <Text style={styles.subtitle}>
          Two people, two panes, one temporary conversation. Nothing is added to history.
        </Text>
      </View>

      {state.connection === "IDLE" || state.connection === "ERROR" ? (
        <>
          <View style={styles.modeRow}>
            <ModeCard
              title="Face to face"
              detail="Share this screen"
              selected={mode === "FACE_TO_FACE"}
              onPress={() => setMode("FACE_TO_FACE")}
            />
            <ModeCard
              title="Remote QR"
              detail="Invite one guest"
              selected={mode === "REMOTE_QR"}
              onPress={() => setMode("REMOTE_QR")}
            />
          </View>

          <LanguagePicker
            label="Your language"
            value={hostLanguage}
            disabledValue={guestLanguage}
            onChange={setHostLanguage}
          />
          <LanguagePicker
            label="Guest language"
            value={guestLanguage}
            disabledValue={hostLanguage}
            onChange={setGuestLanguage}
          />

          {state.error && <InlineStatus kind="error" text={state.error} />}
          {scanError && <InlineStatus kind="error" text={scanError} />}

          <TouchableOpacity style={styles.primaryButton} onPress={createSession}>
            <Text style={styles.primaryButtonText}>
              {mode === "FACE_TO_FACE" ? "Start face-to-face" : "Create remote invite"}
            </Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.secondaryButton} onPress={openScanner}>
            <Text style={styles.secondaryButtonText}>Scan a remote invite</Text>
          </TouchableOpacity>
        </>
      ) : state.connection === "STARTING" ? (
        <InlineStatus kind="working" text="Opening a private conversation…" />
      ) : (
        <>
          <View style={styles.sessionHeader}>
            <InlineStatus
              kind={state.connection === "RECONNECTING" ? "working" : "active"}
              text={statusText(state.connection)}
            />
            <Text style={styles.expiryText}>
              Session {formatDuration(remainingSeconds)}
            </Text>
          </View>

          {invite && state.connection === "WAITING" && (
            <View style={styles.inviteCard}>
              <View style={styles.qrFrame}>
                <QRCode
                  value={invite.qrPayload}
                  size={190}
                  color={colors.textPrimary}
                  backgroundColor={colors.surface}
                />
              </View>
              <Text style={styles.inviteTitle}>One-time guest invite</Text>
              <Text style={styles.inviteExpiry}>
                Expires in {formatDuration(inviteSeconds)} · claimable once
              </Text>
              <Text style={styles.privacyCopy}>
                The QR contains no transcript, account, device ID, or backend credential.
              </Text>
            </View>
          )}

          {state.session &&
            state.connection !== "WAITING" &&
            state.connection !== "EXPIRED" &&
            state.connection !== "ENDED" && (
              <>
                <ConversationPanes
                  session={state.session}
                  turns={state.turns}
                  liveDrafts={state.liveDrafts}
                />
                <ConversationVoiceControls
                  state={voiceState}
                  conversationReady={state.connection === "ACTIVE"}
                  allowRemoteTranslation={allowRemoteTranslation}
                  onAllowRemoteTranslationChange={
                    setAllowRemoteTranslation
                  }
                  onStart={() => void startSpeaking()}
                  onStop={() => void stopVoice()}
                  onCancel={() => void cancelVoice()}
                  onRetry={() => void retryVoice()}
                  onDismiss={() => void resetVoice()}
                />
              </>
            )}

          {state.error && (
            <InlineStatus
              kind={state.connection === "RECONNECTING" ? "working" : "error"}
              text={state.error}
            />
          )}
          {(state.connection === "EXPIRED" || state.connection === "ENDED") && (
            <View style={styles.endedCard}>
              <Text style={styles.endedTitle}>
                {state.connection === "EXPIRED" ? "Conversation expired" : "Conversation ended"}
              </Text>
              <Text style={styles.privacyCopy}>
                Participant capabilities were cleared from this device.
              </Text>
            </View>
          )}

          {state.role === "HOST" && !["EXPIRED", "ENDED"].includes(state.connection) && (
            <TouchableOpacity style={styles.endButton} onPress={endSession}>
              <Text style={styles.endButtonText}>End for everyone</Text>
            </TouchableOpacity>
          )}
          <TouchableOpacity style={styles.secondaryButton} onPress={reset}>
            <Text style={styles.secondaryButtonText}>New conversation</Text>
          </TouchableOpacity>
        </>
      )}
    </ScrollView>
  );
}

export function ConversationVoiceControls({
  state,
  conversationReady,
  allowRemoteTranslation,
  onAllowRemoteTranslationChange,
  onStart,
  onStop,
  onCancel,
  onRetry,
  onDismiss,
}: {
  state: ConversationVoiceState;
  conversationReady: boolean;
  allowRemoteTranslation: boolean;
  onAllowRemoteTranslationChange: (allowed: boolean) => void;
  onStart: () => void;
  onStop: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const statePresentation = presentConversationVoice(state);
  const presentation =
    state.phase === "idle" && !conversationReady
      ? {
          symbol: "↻",
          status: "Waiting for conversation connection",
          tone: "working" as const,
        }
      : statePresentation;
  const canStart =
    conversationReady && canStartConversationVoice(state);
  const canStop = canStopConversationVoice(state);
  const canRetry = canRetryConversationVoice(state);
  const canCancel = canCancelConversationVoice(state);
  const action = canStart
    ? { label: "Start speaking", onPress: onStart }
    : canStop
      ? { label: "Stop and translate", onPress: onStop }
      : canRetry
        ? { label: "Retry once", onPress: onRetry }
        : state.phase === "failed"
          ? { label: "Dismiss error", onPress: onDismiss }
        : null;

  useEffect(() => {
    AccessibilityInfo.announceForAccessibility(presentation.status);
  }, [presentation.status]);

  return (
    <View style={styles.voiceCard}>
      <View
        style={styles.voiceStatus}
        accessible={true}
        accessibilityRole="text"
        accessibilityLiveRegion="polite"
        accessibilityLabel={`Speech status: ${presentation.status}`}
      >
        <Text
          style={[
            styles.voiceStatusSymbol,
            presentation.tone === "active" && styles.voiceStatusActive,
            presentation.tone === "working" && styles.voiceStatusWorking,
            presentation.tone === "error" && styles.voiceStatusError,
          ]}
        >
          {presentation.symbol}
        </Text>
        <View style={styles.voiceStatusCopy}>
          <Text style={styles.voiceStatusTitle}>{presentation.status}</Text>
          <Text style={styles.voiceStatusDetail}>
            Raw audio stays in the authenticated speech stream.
          </Text>
        </View>
      </View>

      <TouchableOpacity
        style={[
          styles.consentRow,
          !canStart && styles.controlDisabled,
        ]}
        disabled={!canStart}
        onPress={() =>
          onAllowRemoteTranslationChange(!allowRemoteTranslation)
        }
        accessibilityRole="checkbox"
        accessibilityState={{
          checked: allowRemoteTranslation,
          disabled: !canStart,
        }}
        accessibilityLabel="Allow configured remote translation for this conversation"
      >
        <Text style={styles.consentMark}>
          {allowRemoteTranslation ? "☑" : "☐"}
        </Text>
        <Text style={styles.consentText}>
          Allow configured remote translation for this conversation
        </Text>
      </TouchableOpacity>

      <TouchableOpacity
        style={[
          styles.voicePrimaryButton,
          !action && styles.controlDisabled,
        ]}
        disabled={!action}
        onPress={action?.onPress}
        accessibilityRole="button"
        accessibilityState={{ disabled: !action }}
        accessibilityLabel={action?.label ?? presentation.status}
      >
        <Text style={styles.voicePrimaryButtonText}>
          {action?.label ?? presentation.status}
        </Text>
      </TouchableOpacity>

      {canCancel && (
        <TouchableOpacity
          style={styles.voiceCancelButton}
          onPress={onCancel}
          accessibilityRole="button"
          accessibilityLabel="Cancel speaking and discard this utterance"
        >
          <Text style={styles.voiceCancelButtonText}>Cancel utterance</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

export function ConversationPanes({
  session,
  turns,
  liveDrafts = { HOST: null, GUEST: null },
}: {
  session: ConversationSession;
  turns: ConversationTurn[];
  liveDrafts?: Record<
    ConversationParticipantRole,
    ConversationLiveDraft | null
  >;
}) {
  return (
    <View style={styles.panes}>
      <Pane
        role="HOST"
        label="You"
        language={languageLabel(session.hostLanguage)}
        turns={turns}
        draft={liveDrafts.HOST}
      />
      <Pane
        role="GUEST"
        label="Guest"
        language={languageLabel(session.guestLanguage)}
        turns={turns}
        draft={liveDrafts.GUEST}
      />
    </View>
  );
}

function Pane({
  role,
  label,
  language,
  turns,
  draft,
}: {
  role: ConversationParticipantRole;
  label: string;
  language: string;
  turns: ConversationTurn[];
  draft: ConversationLiveDraft | null;
}) {
  return (
    <View style={styles.pane}>
      <View style={styles.paneHeader}>
        <Text style={styles.paneLabel}>{label}</Text>
        <Text style={styles.paneLanguage}>{language}</Text>
      </View>
      {turns.length === 0 && !draft ? (
        <Text style={styles.emptyPane}>Waiting for the first final phrase…</Text>
      ) : (
        turns.map((turn) => (
          <View key={`${role}-${turn.sequence}`} style={styles.turnBubble}>
            <Text style={styles.turnSpeaker}>
              ✓ Final · {turn.participantRole === role ? "Spoken" : "Translated"} · {turn.sequence}
            </Text>
            <Text style={styles.turnText}>{textForPane(turn, role)}</Text>
          </View>
        ))
      )}
      {draft && (
        <View
          style={styles.draftBubble}
          accessible={true}
          accessibilityRole="text"
          accessibilityLabel={`Draft from ${label}: ${draft.speechEvent.payload.text}`}
        >
          <Text style={styles.draftLabel}>✎ Draft</Text>
          <Text style={styles.draftText}>
            {draft.speechEvent.payload.text}
          </Text>
        </View>
      )}
    </View>
  );
}

function languageLabel(code: string): string {
  return LANGUAGES.find((language) => language.code === code)?.label ?? code;
}

export function textForPane(
  turn: ConversationTurn,
  paneRole: ConversationParticipantRole
): string {
  return turn.participantRole === paneRole
    ? turn.speechEvent.payload.text
    : turn.translationEvent.payload.text;
}

function LanguagePicker({
  label,
  value,
  disabledValue,
  onChange,
}: {
  label: string;
  value: string;
  disabledValue: string;
  onChange: (language: string) => void;
}) {
  return (
    <View style={styles.picker}>
      <Text style={styles.pickerLabel}>{label}</Text>
      <View style={styles.chipRow}>
        {LANGUAGES.map((language) => {
          const selected = language.code === value;
          const disabled = language.code === disabledValue;
          return (
            <TouchableOpacity
              key={language.code}
              style={[
                styles.chip,
                selected && styles.chipSelected,
                disabled && styles.chipDisabled,
              ]}
              disabled={disabled}
              onPress={() => onChange(language.code)}
            >
              <Text style={[styles.chipText, selected && styles.chipTextSelected]}>
                {language.label}
              </Text>
            </TouchableOpacity>
          );
        })}
      </View>
    </View>
  );
}

function ModeCard({
  title,
  detail,
  selected,
  onPress,
}: {
  title: string;
  detail: string;
  selected: boolean;
  onPress: () => void;
}) {
  return (
    <TouchableOpacity
      style={[styles.modeCard, selected && styles.modeCardSelected]}
      onPress={onPress}
      accessibilityRole="radio"
      accessibilityState={{ checked: selected }}
    >
      <Text style={styles.modeTitle}>{title}</Text>
      <Text style={styles.modeDetail}>{detail}</Text>
    </TouchableOpacity>
  );
}

function InlineStatus({
  kind,
  text,
}: {
  kind: "active" | "working" | "error";
  text: string;
}) {
  return (
    <View style={styles.inlineStatus}>
      <Text
        style={[
          styles.statusSymbol,
          kind === "active" && styles.statusActive,
          kind === "working" && styles.statusWorking,
          kind === "error" && styles.statusError,
        ]}
      >
        {kind === "active" ? "●" : kind === "working" ? "↻" : "!"}
      </Text>
      <Text style={styles.inlineStatusText}>{text}</Text>
    </View>
  );
}

function statusText(connection: string): string {
  if (connection === "WAITING") return "Waiting for guest";
  if (connection === "RECONNECTING") return "Reconnecting";
  return "Conversation active";
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, paddingBottom: spacing.xxl },
  hero: { marginBottom: spacing.xl },
  eyebrow: {
    ...typography.caption,
    color: colors.accent,
    letterSpacing: 1.4,
    marginBottom: spacing.xs,
  },
  title: { ...typography.title, color: colors.textPrimary, fontSize: 30 },
  subtitle: {
    ...typography.body,
    color: colors.textSecondary,
    lineHeight: 22,
    marginTop: spacing.sm,
  },
  modeRow: { flexDirection: "row", gap: spacing.md, marginBottom: spacing.xl },
  modeCard: {
    flex: 1,
    padding: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
  },
  modeCardSelected: { borderColor: colors.accent, backgroundColor: colors.accentSoft },
  modeTitle: { ...typography.body, color: colors.textPrimary, fontWeight: "700" },
  modeDetail: { ...typography.caption, color: colors.textSecondary, marginTop: spacing.xs },
  picker: { marginBottom: spacing.lg },
  pickerLabel: { ...typography.heading, color: colors.textPrimary, marginBottom: spacing.sm },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  chip: {
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: radius.pill,
    backgroundColor: colors.chipBackground,
  },
  chipSelected: { backgroundColor: colors.textPrimary },
  chipDisabled: { opacity: 0.3 },
  chipText: { ...typography.body, color: colors.textPrimary },
  chipTextSelected: { color: colors.chipTextActive },
  primaryButton: {
    backgroundColor: colors.accent,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    alignItems: "center",
    marginTop: spacing.sm,
  },
  primaryButtonText: { ...typography.body, color: colors.surface, fontWeight: "700" },
  secondaryButton: {
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.surface,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    alignItems: "center",
    marginTop: spacing.md,
  },
  secondaryButtonText: { ...typography.body, color: colors.textPrimary, fontWeight: "600" },
  inlineStatus: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    paddingVertical: spacing.sm,
  },
  statusSymbol: { fontSize: 17, fontWeight: "800" },
  statusActive: { color: colors.success },
  statusWorking: { color: colors.warning },
  statusError: { color: colors.danger },
  inlineStatusText: { ...typography.body, color: colors.textPrimary, flex: 1 },
  sessionHeader: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: spacing.lg,
  },
  expiryText: { ...typography.caption, color: colors.textSecondary },
  inviteCard: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: spacing.xl,
  },
  qrFrame: { padding: spacing.md, backgroundColor: colors.surface, borderRadius: radius.md },
  inviteTitle: { ...typography.heading, color: colors.textPrimary, marginTop: spacing.lg },
  inviteExpiry: { ...typography.body, color: colors.warning, marginTop: spacing.xs },
  privacyCopy: {
    ...typography.caption,
    color: colors.textSecondary,
    lineHeight: 18,
    textAlign: "center",
    marginTop: spacing.sm,
  },
  panes: { gap: spacing.md },
  voiceCard: {
    marginTop: spacing.md,
    padding: spacing.md,
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
  },
  voiceStatus: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.md,
  },
  voiceStatusSymbol: {
    color: colors.textMuted,
    fontSize: 22,
    fontWeight: "800",
    minWidth: 24,
    textAlign: "center",
  },
  voiceStatusActive: { color: colors.warning },
  voiceStatusWorking: { color: colors.accent },
  voiceStatusError: { color: colors.danger },
  voiceStatusCopy: { flex: 1 },
  voiceStatusTitle: {
    ...typography.body,
    color: colors.textPrimary,
    fontWeight: "700",
  },
  voiceStatusDetail: {
    ...typography.caption,
    color: colors.textSecondary,
    marginTop: spacing.xs,
  },
  consentRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.sm,
    marginTop: spacing.md,
    paddingVertical: spacing.sm,
  },
  consentMark: { fontSize: 20, color: colors.accent },
  consentText: {
    ...typography.caption,
    color: colors.textPrimary,
    flex: 1,
    lineHeight: 18,
  },
  voicePrimaryButton: {
    backgroundColor: colors.accent,
    paddingVertical: spacing.md,
    borderRadius: radius.md,
    alignItems: "center",
    marginTop: spacing.sm,
  },
  voicePrimaryButtonText: {
    ...typography.body,
    color: colors.surface,
    fontWeight: "700",
  },
  voiceCancelButton: {
    paddingVertical: spacing.md,
    alignItems: "center",
    marginTop: spacing.xs,
  },
  voiceCancelButtonText: {
    ...typography.body,
    color: colors.danger,
    fontWeight: "600",
  },
  controlDisabled: { opacity: 0.45 },
  pane: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: spacing.md,
    minHeight: 160,
  },
  paneHeader: { flexDirection: "row", justifyContent: "space-between", marginBottom: spacing.md },
  paneLabel: { ...typography.heading, color: colors.textPrimary },
  paneLanguage: { ...typography.caption, color: colors.textSecondary },
  emptyPane: { ...typography.body, color: colors.textMuted, lineHeight: 22 },
  turnBubble: { borderTopWidth: 1, borderTopColor: colors.border, paddingVertical: spacing.md },
  turnSpeaker: { ...typography.caption, color: colors.accent, marginBottom: spacing.xs },
  turnText: { ...typography.body, color: colors.textPrimary, lineHeight: 23 },
  draftBubble: {
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingVertical: spacing.md,
  },
  draftLabel: {
    ...typography.caption,
    color: colors.textSecondary,
    marginBottom: spacing.xs,
  },
  draftText: {
    ...typography.body,
    color: colors.textSecondary,
    fontStyle: "italic",
    lineHeight: 23,
  },
  endedCard: {
    padding: spacing.lg,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  endedTitle: { ...typography.heading, color: colors.textPrimary, textAlign: "center" },
  endButton: {
    paddingVertical: spacing.md,
    alignItems: "center",
    marginTop: spacing.lg,
  },
  endButtonText: { ...typography.body, color: colors.danger, fontWeight: "700" },
  scannerRoot: { flex: 1, backgroundColor: "#000000" },
  camera: { flex: 1 },
  scanOverlay: {
    position: "absolute",
    left: spacing.lg,
    right: spacing.lg,
    bottom: spacing.xxl,
    backgroundColor: "rgba(17, 19, 24, 0.9)",
    padding: spacing.lg,
    borderRadius: radius.lg,
  },
  scanTitle: { ...typography.heading, color: colors.surface, textAlign: "center" },
  scanHint: { ...typography.body, color: "#D7DAE0", textAlign: "center", marginTop: spacing.xs },
  lightButton: {
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: "center",
    marginTop: spacing.md,
  },
  lightButtonText: { ...typography.body, color: colors.textPrimary, fontWeight: "700" },
});
