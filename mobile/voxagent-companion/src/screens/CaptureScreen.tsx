import React, { useReducer, useState } from "react";
import {
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import {
  RecordingPresets,
  requestRecordingPermissionsAsync,
  useAudioRecorder,
} from "expo-audio";

import {
  type ApiFailureCode,
  AudioUploadResult,
  CaptureMode,
  CaptureSession,
  createCaptureSession,
  createPairingCode,
  PairingCode,
  processCaptureSession,
  uploadAudio,
} from "../api/client";
import {
  canRetryCaptureProcessing,
  CaptureProcessingState,
  initialCaptureProcessingState,
  MAX_PROCESS_ATTEMPTS,
  reduceCaptureProcessing,
} from "../capture/processingLifecycle";
import {
  ConnectionBanner,
  type ConnectionStatus,
} from "../components/ConnectionBanner";
import { colors, radius, spacing, typography } from "../theme";

// The recording lifecycle is a small state machine so the UI can show exactly
// where a capture is (and, on failure, why). See RECORD_FLOW notes below.
type RecState =
  | "idle"
  | "requesting-permission"
  | "recording"
  | "stopping"
  | "uploading"
  | "uploaded"
  | "failed";

type RecError =
  | "permission-denied"
  | "recorder-unavailable"
  | "upload-failed"
  | "offline"
  | "server-error";

const REC_ERROR_TEXT: Record<RecError, string> = {
  "permission-denied":
    "Microphone permission denied — enable it in Settings to record.",
  "recorder-unavailable": "The recorder is unavailable on this device.",
  "upload-failed": "Upload failed. Please try again.",
  offline: "Engine unreachable — try the upload again when back online.",
  "server-error": "The server rejected the upload. Please try again.",
};

const PROCESS_ERROR_TEXT: Record<ApiFailureCode, string> = {
  ENGINE_OFFLINE: "The engine is offline. The uploaded recording is safe.",
  AUTH_REQUIRED: "Reconnect this device before processing the upload.",
  AUTHORIZATION_DENIED:
    "This account cannot process the uploaded recording.",
  REQUEST_REJECTED:
    "The engine could not process this recording. The upload is still available.",
  INVALID_RESPONSE:
    "The engine returned an invalid processing result. The upload is still available.",
};

function recButtonLabel(state: RecState): string {
  switch (state) {
    case "requesting-permission":
      return "Requesting mic…";
    case "recording":
      return "Stop & upload";
    case "stopping":
      return "Stopping…";
    case "uploading":
      return "Uploading…";
    case "uploaded":
      return "Record again";
    case "failed":
      return "Retry recording";
    default:
      return "Record audio";
  }
}

function recStatusText(state: RecState): string {
  switch (state) {
    case "requesting-permission":
      return "Asking for microphone permission…";
    case "recording":
      return "Recording — tap to stop and upload.";
    case "stopping":
      return "Finishing the recording…";
    case "uploading":
      return "Uploading the recording to the session…";
    case "uploaded":
      return "Upload complete. Vox Agent is processing it now.";
    case "failed":
      return "Recording did not complete.";
    default:
      return "Record from the phone mic and upload to a capture session.";
  }
}

function uploadedStatusText(state: CaptureProcessingState): string {
  switch (state.phase) {
    case "processing":
      return "Upload complete. Vox Agent is processing it now.";
    case "ready":
      return "Upload processed. Its memory is ready to review.";
    case "failed":
      return "Processing stopped, but the uploaded recording is still safe.";
    default:
      return "Upload complete. Waiting for processing to start.";
  }
}

const MODES: CaptureMode[] = ["MEETING", "TRANSLATION", "INTERVIEW", "IDEA"];
const LANGUAGES = ["Auto", "Chinese", "Japanese", "English"];
const DEVICES = ["Phone Mic", "Simulated VOXPIN"];

export default function CaptureScreen({
  onOpenMemory,
}: {
  onOpenMemory?: (sessionId: string) => void;
}) {
  const [mode, setMode] = useState<CaptureMode>("MEETING");
  const [language, setLanguage] = useState("Auto");
  const [device, setDevice] = useState(DEVICES[0]);
  const [session, setSession] = useState<CaptureSession | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [isPairing, setIsPairing] = useState(false);
  const [connectionState, setConnectionState] =
    useState<ConnectionStatus>("unchecked");

  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const [recState, setRecState] = useState<RecState>("idle");
  const [recError, setRecError] = useState<RecError | null>(null);
  const [uploadInfo, setUploadInfo] = useState<AudioUploadResult | null>(null);
  const [processingState, dispatchProcessing] = useReducer(
    reduceCaptureProcessing,
    initialCaptureProcessingState
  );

  const languageHintFor = (label: string) => {
    switch (label) {
      case "Chinese":
        return "zh";
      case "Japanese":
        return "ja";
      case "English":
        return "en";
      default:
        return "auto";
    }
  };

  const recordConnectionFailure = (offline: boolean) => {
    setConnectionState(offline ? "offline" : "unavailable");
  };

  const startSession = async () => {
    setIsStarting(true);
    try {
      const result = await createCaptureSession({
        mode,
        languageHints: [languageHintFor(language)],
      });
      if (result.ok) {
        setSession(result.data);
        setConnectionState(result.source);
      } else {
        setSession(null);
        recordConnectionFailure(result.offline);
      }
    } finally {
      setIsStarting(false);
    }
  };

  const pairDevice = async () => {
    setIsPairing(true);
    try {
      const result = await createPairingCode();
      if (result.ok) {
        setPairing(result.data);
        setConnectionState(result.source);
      } else {
        setPairing(null);
        recordConnectionFailure(result.offline);
      }
    } finally {
      setIsPairing(false);
    }
  };

  // NOTE: the microphone-capture path (permission prompt, prepare/record/stop,
  // and the real file URI) can only be exercised on a device/simulator — it is
  // typecheck-verified here, not run. The upload half (uploadAudio) is unit
  // tested in ../api/client.test.ts.
  const startRecording = async () => {
    setRecError(null);
    setRecState("requesting-permission");
    try {
      const perm = await requestRecordingPermissionsAsync();
      if (!perm.granted) {
        setRecError("permission-denied");
        setRecState("failed");
        return;
      }
    } catch {
      setRecError("permission-denied");
      setRecState("failed");
      return;
    }
    try {
      await recorder.prepareToRecordAsync();
      recorder.record();
      setUploadInfo(null);
      if (
        processingState.phase === "ready" ||
        processingState.phase === "failed"
      ) {
        setSession(null);
      }
      setRecState("recording");
    } catch {
      setRecError("recorder-unavailable");
      setRecState("failed");
    }
  };

  const processUpload = async (upload: AudioUploadResult) => {
    dispatchProcessing({ type: "process.started" });
    const result = await processCaptureSession(upload.sessionId);
    if (result.ok) {
      setSession(result.data);
      setConnectionState(result.source);
      dispatchProcessing({ type: "process.succeeded", session: result.data });
      return;
    }
    recordConnectionFailure(result.offline);
    dispatchProcessing({ type: "process.failed", code: result.code });
  };

  const stopAndUpload = async () => {
    setRecState("stopping");
    let uri: string | null = null;
    try {
      await recorder.stop();
      uri = recorder.uri;
    } catch {
      setRecError("recorder-unavailable");
      setRecState("failed");
      return;
    }
    if (!uri) {
      setRecError("recorder-unavailable");
      setRecState("failed");
      return;
    }

    // Ensure a capture session exists to upload the audio into.
    let sessionId = session?.id;
    if (!sessionId) {
      const createResult = await createCaptureSession({
        mode,
        source: "MOBILE",
        languageHints: [languageHintFor(language)],
      });
      if (!createResult.ok) {
        recordConnectionFailure(createResult.offline);
        setRecError(createResult.offline ? "offline" : "server-error");
        setRecState("failed");
        return;
      }
      setSession(createResult.data);
      setConnectionState(createResult.source);
      sessionId = createResult.data.id;
    }

    setRecState("uploading");
    const result = await uploadAudio(sessionId, uri);
    if (result.ok) {
      setUploadInfo(result.data);
      setConnectionState(result.source);
      setRecState("uploaded");
      dispatchProcessing({ type: "upload.succeeded", upload: result.data });
      await processUpload(result.data);
    } else {
      recordConnectionFailure(result.offline);
      setRecError(result.offline ? "offline" : "server-error");
      setRecState("failed");
    }
  };

  const recBusy =
    recState === "requesting-permission" ||
    recState === "stopping" ||
    recState === "uploading" ||
    processingState.phase === "processing";

  return (
    <ScrollView
      style={styles.container}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
    >
      <Text style={styles.title}>Capture</Text>
      <ConnectionBanner
        status={connectionState}
        detail={
          connectionState === "live"
            ? "Connected to the Vox Agent engine."
            : connectionState === "demo"
              ? "Connected to the configured demo engine; data is not production."
            : connectionState === "offline"
              ? "No session, pairing, or upload data was fabricated."
              : connectionState === "unavailable"
                ? "The engine rejected the request."
                : "Start a capture or pair to verify."
        }
      />

      <View style={styles.recordArea}>
        <TouchableOpacity
          style={[styles.recordButton, session && styles.recordButtonActive]}
          onPress={() => (session ? setSession(null) : startSession())}
          disabled={isStarting}
          accessibilityRole="button"
          accessibilityLabel={
            session ? "Close capture session" : "Start capture session"
          }
        >
          <Text style={styles.recordButtonLabel}>
            {isStarting ? "..." : session ? "STOP" : "REC"}
          </Text>
        </TouchableOpacity>

        {session ? (
          <View style={styles.statusPill}>
            <View style={styles.statusDot} />
            <Text style={styles.statusPillText}>
              {session.status} · {session.id}
            </Text>
          </View>
        ) : (
          <Text style={styles.hint}>Tap to start a capture session</Text>
        )}
      </View>

      <Section title="Mode">
        <ChipRow>
          {MODES.map((m) => (
            <Chip
              key={m}
              label={formatMode(m)}
              active={m === mode}
              onPress={() => setMode(m)}
            />
          ))}
        </ChipRow>
      </Section>

      <Section title="Language">
        <ChipRow>
          {LANGUAGES.map((l) => (
            <Chip
              key={l}
              label={l}
              active={l === language}
              onPress={() => setLanguage(l)}
            />
          ))}
        </ChipRow>
      </Section>

      <Section title="Device">
        <ChipRow>
          {DEVICES.map((d) => (
            <Chip
              key={d}
              label={d}
              active={d === device}
              onPress={() => setDevice(d)}
            />
          ))}
        </ChipRow>
      </Section>

      <View style={styles.divider} />

      <Section title="Record & upload audio">
        <TouchableOpacity
          style={styles.secondaryButton}
          onPress={recState === "recording" ? stopAndUpload : startRecording}
          disabled={recBusy}
          accessibilityRole="button"
          accessibilityLabel={recButtonLabel(recState)}
        >
          <Text style={styles.secondaryButtonText}>{recButtonLabel(recState)}</Text>
        </TouchableOpacity>

        <Text style={styles.hint}>
          {recState === "uploaded"
            ? uploadedStatusText(processingState)
            : recStatusText(recState)}
        </Text>

        {recState === "uploaded" && uploadInfo && (
          <Text style={styles.uploadOk}>
            Uploaded {uploadInfo.bytes} bytes for session {uploadInfo.sessionId}.
          </Text>
        )}

        {recState === "failed" && recError && (
          <Text style={styles.errorText}>{REC_ERROR_TEXT[recError]}</Text>
        )}

        {uploadInfo && (
          <CaptureProcessingPanel
            state={processingState}
            onRetry={() => void processUpload(uploadInfo)}
            onOpenMemory={onOpenMemory}
          />
        )}
      </Section>

      <View style={styles.divider} />

      <Section title="Hardware pairing">
        <TouchableOpacity
          style={styles.secondaryButton}
          onPress={pairDevice}
          disabled={isPairing}
        >
          <Text style={styles.secondaryButtonText}>
            {isPairing ? "Generating code..." : "Pair device"}
          </Text>
        </TouchableOpacity>

        {pairing && (
          <View style={styles.pairingCard}>
            <Text style={styles.pairingCode}>{pairing.code}</Text>
            <Text style={styles.pairingPayload}>{pairing.qrPayload}</Text>
            <Text style={styles.pairingExpiry}>
              Expires {new Date(pairing.expiresAt).toLocaleTimeString()}
            </Text>
          </View>
        )}
      </Section>
    </ScrollView>
  );
}

export function CaptureProcessingPanel({
  state,
  onRetry,
  onOpenMemory,
}: {
  state: CaptureProcessingState;
  onRetry: () => void;
  onOpenMemory?: (sessionId: string) => void;
}) {
  if (state.phase === "idle") return null;

  const attemptsRemaining = MAX_PROCESS_ATTEMPTS - state.attempts;
  return (
    <View
      style={styles.processingCard}
      accessibilityRole="summary"
      accessibilityLabel={`Capture processing ${state.phase}`}
    >
      {state.phase === "uploaded" && (
        <>
          <Text style={styles.processingTitle}>✓ Upload complete</Text>
          <Text style={styles.processingDetail}>
            Waiting to process session {state.upload.sessionId}.
          </Text>
        </>
      )}

      {state.phase === "processing" && (
        <>
          <Text style={styles.processingTitle}>… Processing recording</Text>
          <Text style={styles.processingDetail}>
            {`Attempt ${state.attempts} of ${MAX_PROCESS_ATTEMPTS}. You can switch tabs while Vox Agent works.`}
          </Text>
        </>
      )}

      {state.phase === "ready" && (
        <>
          <Text style={styles.processingReadyTitle}>✓ Memory ready</Text>
          <Text style={styles.processingDetail}>
            Session {state.session.id} is ready to review.
          </Text>
          <TouchableOpacity
            style={styles.processingAction}
            onPress={() => onOpenMemory?.(state.session.id)}
            accessibilityRole="button"
            accessibilityLabel="Open processed memory"
          >
            <Text style={styles.processingActionText}>Open memory</Text>
          </TouchableOpacity>
        </>
      )}

      {state.phase === "failed" && (
        <>
          <Text style={styles.processingErrorTitle}>! Processing failed</Text>
          <Text style={styles.processingDetail}>
            {PROCESS_ERROR_TEXT[state.code]}
          </Text>
          {canRetryCaptureProcessing(state) ? (
            <TouchableOpacity
              style={styles.processingAction}
              onPress={onRetry}
              accessibilityRole="button"
              accessibilityLabel="Try processing again"
            >
              <Text style={styles.processingActionText}>
                {`Try processing again · ${attemptsRemaining} ${
                  attemptsRemaining === 1 ? "attempt" : "attempts"
                } remaining`}
              </Text>
            </TouchableOpacity>
          ) : (
            <Text style={styles.retryExhausted}>
              Retry limit reached. Start a new recording or check the engine.
            </Text>
          )}
        </>
      )}
    </View>
  );
}

function formatMode(mode: CaptureMode): string {
  return mode.charAt(0) + mode.slice(1).toLowerCase();
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {children}
    </View>
  );
}

function ChipRow({ children }: { children: React.ReactNode }) {
  return <View style={styles.chipRow}>{children}</View>;
}

function Chip({
  label,
  active,
  onPress,
}: {
  label: string;
  active: boolean;
  onPress: () => void;
}) {
  return (
    <TouchableOpacity
      style={[styles.chip, active && styles.chipActive]}
      onPress={onPress}
    >
      <Text style={[styles.chipText, active && styles.chipTextActive]}>
        {label}
      </Text>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  title: {
    ...typography.title,
    color: colors.textPrimary,
    marginBottom: spacing.lg,
  },
  recordArea: {
    alignItems: "center",
    marginBottom: spacing.xl,
  },
  recordButton: {
    width: 140,
    height: 140,
    borderRadius: radius.circle,
    backgroundColor: colors.danger,
    alignItems: "center",
    justifyContent: "center",
    shadowColor: "#000",
    shadowOpacity: 0.15,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 6 },
    elevation: 4,
  },
  recordButtonActive: {
    backgroundColor: colors.textPrimary,
  },
  recordButtonLabel: {
    color: "#FFFFFF",
    fontSize: 20,
    fontWeight: "700",
    letterSpacing: 1,
  },
  hint: {
    marginTop: spacing.md,
    color: colors.textSecondary,
    ...typography.body,
  },
  uploadOk: {
    marginTop: spacing.sm,
    color: colors.success,
    ...typography.body,
  },
  errorText: {
    marginTop: spacing.sm,
    color: colors.danger,
    ...typography.body,
  },
  processingCard: {
    marginTop: spacing.md,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    backgroundColor: colors.surface,
  },
  processingTitle: {
    ...typography.body,
    color: colors.textPrimary,
    fontWeight: "700",
  },
  processingReadyTitle: {
    ...typography.body,
    color: colors.success,
    fontWeight: "700",
  },
  processingErrorTitle: {
    ...typography.body,
    color: colors.danger,
    fontWeight: "700",
  },
  processingDetail: {
    ...typography.body,
    color: colors.textSecondary,
    marginTop: spacing.xs,
  },
  processingAction: {
    marginTop: spacing.md,
    paddingVertical: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: radius.md,
    backgroundColor: colors.accent,
    alignItems: "center",
  },
  processingActionText: {
    ...typography.body,
    color: colors.chipTextActive,
    fontWeight: "600",
  },
  retryExhausted: {
    ...typography.caption,
    color: colors.danger,
    marginTop: spacing.sm,
  },
  statusPill: {
    marginTop: spacing.md,
    flexDirection: "row",
    alignItems: "center",
    backgroundColor: colors.accentSoft,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: radius.circle,
    backgroundColor: colors.danger,
    marginRight: spacing.sm,
  },
  statusPillText: {
    color: colors.accent,
    ...typography.caption,
  },
  section: {
    marginBottom: spacing.lg,
  },
  sectionTitle: {
    ...typography.heading,
    color: colors.textPrimary,
    marginBottom: spacing.sm,
  },
  chipRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: spacing.sm,
  },
  chip: {
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radius.pill,
    backgroundColor: colors.chipBackground,
  },
  chipActive: {
    backgroundColor: colors.chipBackgroundActive,
  },
  chipText: {
    ...typography.body,
    color: colors.textPrimary,
  },
  chipTextActive: {
    color: colors.chipTextActive,
  },
  divider: {
    height: 1,
    backgroundColor: colors.border,
    marginBottom: spacing.lg,
  },
  secondaryButton: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    paddingVertical: spacing.md,
    alignItems: "center",
    backgroundColor: colors.surface,
  },
  secondaryButtonText: {
    ...typography.body,
    color: colors.textPrimary,
    fontWeight: "600",
  },
  pairingCard: {
    marginTop: spacing.md,
    backgroundColor: colors.surface,
    borderRadius: radius.md,
    padding: spacing.md,
    borderWidth: 1,
    borderColor: colors.border,
  },
  pairingCode: {
    fontSize: 28,
    fontWeight: "700",
    letterSpacing: 4,
    color: colors.textPrimary,
    marginBottom: spacing.xs,
  },
  pairingPayload: {
    ...typography.caption,
    color: colors.textSecondary,
    marginBottom: spacing.xs,
  },
  pairingExpiry: {
    ...typography.caption,
    color: colors.textMuted,
  },
});
