import type {
  ApiFailureCode,
  AudioUploadResult,
  CaptureSession,
} from "../api/client";

export const MAX_PROCESS_ATTEMPTS = 3;

export type CaptureProcessingState =
  | { phase: "idle" }
  | {
      phase: "uploaded";
      upload: AudioUploadResult;
      attempts: number;
    }
  | {
      phase: "processing";
      upload: AudioUploadResult;
      attempts: number;
    }
  | {
      phase: "ready";
      upload: AudioUploadResult;
      attempts: number;
      session: CaptureSession;
    }
  | {
      phase: "failed";
      upload: AudioUploadResult;
      attempts: number;
      code: ApiFailureCode;
    };

export type CaptureProcessingEvent =
  | { type: "upload.succeeded"; upload: AudioUploadResult }
  | { type: "process.started" }
  | { type: "process.succeeded"; session: CaptureSession }
  | { type: "process.failed"; code: ApiFailureCode };

export const initialCaptureProcessingState: CaptureProcessingState = {
  phase: "idle",
};

export function canRetryCaptureProcessing(
  state: CaptureProcessingState
): boolean {
  return state.phase === "failed" && state.attempts < MAX_PROCESS_ATTEMPTS;
}

export function reduceCaptureProcessing(
  state: CaptureProcessingState,
  event: CaptureProcessingEvent
): CaptureProcessingState {
  if (event.type === "upload.succeeded") {
    return {
      phase: "uploaded",
      upload: event.upload,
      attempts: 0,
    };
  }

  if (event.type === "process.started") {
    if (
      (state.phase !== "uploaded" && state.phase !== "failed") ||
      state.attempts >= MAX_PROCESS_ATTEMPTS
    ) {
      return state;
    }
    return {
      phase: "processing",
      upload: state.upload,
      attempts: state.attempts + 1,
    };
  }

  if (event.type === "process.succeeded") {
    if (
      state.phase !== "processing" ||
      event.session.id !== state.upload.sessionId ||
      event.session.status !== "READY"
    ) {
      return state;
    }
    return {
      phase: "ready",
      upload: state.upload,
      attempts: state.attempts,
      session: event.session,
    };
  }

  if (state.phase !== "processing") {
    return state;
  }
  return {
    phase: "failed",
    upload: state.upload,
    attempts: state.attempts,
    code: event.code,
  };
}
