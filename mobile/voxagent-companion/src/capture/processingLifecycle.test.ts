import { describe, expect, it } from "vitest";

import type { AudioUploadResult, CaptureSession } from "../api/client";
import {
  MAX_PROCESS_ATTEMPTS,
  canRetryCaptureProcessing,
  initialCaptureProcessingState,
  reduceCaptureProcessing,
} from "./processingLifecycle";

const upload: AudioUploadResult = {
  sessionId: "cap_lifecycle",
  audioPath: "/managed/cap_lifecycle.m4a",
  bytes: 4096,
};

const readySession: CaptureSession = {
  id: "cap_lifecycle",
  workspaceId: "ws_lifecycle",
  ownerUserId: "user_lifecycle",
  edition: "ENTERPRISE",
  source: "MOBILE",
  mode: "MEETING",
  title: null,
  startedAt: "2026-07-26T12:00:00Z",
  endedAt: "2026-07-26T12:01:00Z",
  languageHints: ["en"],
  detectedLanguages: ["en"],
  privacyLevel: "PERSONAL",
  consentState: "GRANTED",
  deviceIds: [],
  localOnlyUntilSync: false,
  status: "READY",
};

describe("capture processing lifecycle", () => {
  it("moves uploaded audio through one processing attempt to ready", () => {
    const uploaded = reduceCaptureProcessing(initialCaptureProcessingState, {
      type: "upload.succeeded",
      upload,
    });
    const processing = reduceCaptureProcessing(uploaded, {
      type: "process.started",
    });
    const ready = reduceCaptureProcessing(processing, {
      type: "process.succeeded",
      session: readySession,
    });

    expect(processing).toMatchObject({
      phase: "processing",
      attempts: 1,
      upload,
    });
    expect(ready).toMatchObject({
      phase: "ready",
      attempts: 1,
      upload,
      session: readySession,
    });
  });

  it("ignores duplicate starts and stale or foreign success events", () => {
    const uploaded = reduceCaptureProcessing(initialCaptureProcessingState, {
      type: "upload.succeeded",
      upload,
    });
    const processing = reduceCaptureProcessing(uploaded, {
      type: "process.started",
    });

    expect(
      reduceCaptureProcessing(processing, { type: "process.started" })
    ).toBe(processing);
    expect(
      reduceCaptureProcessing(uploaded, {
        type: "process.succeeded",
        session: readySession,
      })
    ).toBe(uploaded);
    expect(
      reduceCaptureProcessing(processing, {
        type: "process.succeeded",
        session: { ...readySession, id: "cap_foreign" },
      })
    ).toBe(processing);
  });

  it("preserves the upload after failure and permits a bounded retry", () => {
    let state = reduceCaptureProcessing(initialCaptureProcessingState, {
      type: "upload.succeeded",
      upload,
    });
    state = reduceCaptureProcessing(state, { type: "process.started" });
    state = reduceCaptureProcessing(state, {
      type: "process.failed",
      code: "ENGINE_OFFLINE",
    });

    expect(state).toMatchObject({
      phase: "failed",
      attempts: 1,
      upload,
      code: "ENGINE_OFFLINE",
    });
    expect(canRetryCaptureProcessing(state)).toBe(true);

    state = reduceCaptureProcessing(state, { type: "process.started" });
    expect(state).toMatchObject({
      phase: "processing",
      attempts: 2,
      upload,
    });
  });

  it("stops retrying after the third failed attempt", () => {
    let state = reduceCaptureProcessing(initialCaptureProcessingState, {
      type: "upload.succeeded",
      upload,
    });
    for (let attempt = 0; attempt < MAX_PROCESS_ATTEMPTS; attempt += 1) {
      state = reduceCaptureProcessing(state, { type: "process.started" });
      state = reduceCaptureProcessing(state, {
        type: "process.failed",
        code: "REQUEST_REJECTED",
      });
    }

    expect(state).toMatchObject({
      phase: "failed",
      attempts: MAX_PROCESS_ATTEMPTS,
      upload,
    });
    expect(canRetryCaptureProcessing(state)).toBe(false);
    expect(
      reduceCaptureProcessing(state, { type: "process.started" })
    ).toBe(state);
  });

  it("a new upload supersedes an exhausted lifecycle without losing evidence", () => {
    const exhausted = {
      phase: "failed",
      upload,
      attempts: MAX_PROCESS_ATTEMPTS,
      code: "REQUEST_REJECTED",
    } as const;
    const nextUpload = { ...upload, sessionId: "cap_next" };

    expect(
      reduceCaptureProcessing(exhausted, {
        type: "upload.succeeded",
        upload: nextUpload,
      })
    ).toEqual({
      phase: "uploaded",
      upload: nextUpload,
      attempts: 0,
    });
  });
});
