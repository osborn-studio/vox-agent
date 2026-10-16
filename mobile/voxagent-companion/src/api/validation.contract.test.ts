import { describe, expect, it } from "vitest";

import corpus from "./fixtures/mobile-wire-v1.generated";
import {
  isAgentAnswer,
  isAudioUploadResult,
  isCaptureSession,
  isCaptureSessionArray,
  isCaptureSummary,
  isConversationInviteClaimed,
  isConversationLiveSnapshot,
  isConversationReplay,
  isConversationSession,
  isConversationSessionCreated,
  isConversationTurn,
  isMemoryNode,
  isMemoryNodeArray,
  isMemorySearchResponse,
  isPairingCode,
  isPermissionDecision,
  isPermissionRequestArray,
  isSpeechFinalEvent,
  isSpeechAudioChunkAck,
  isSpeechReplay,
  isSpeechStreamCreated,
  isSpeechTerminalEvent,
  isTokenBalance,
  isTokenLedger,
  isTranslationEvent,
  type Validator,
} from "./validation";

type PathPart = string | number;

const validators: Record<string, Validator<unknown>> = {
  captureSession: isCaptureSession,
  captureSessionList: isCaptureSessionArray,
  captureSummary: isCaptureSummary,
  audioUploadResult: isAudioUploadResult,
  memoryNode: isMemoryNode,
  memoryNodeList: isMemoryNodeArray,
  memorySearchResponse: isMemorySearchResponse,
  pairingCode: isPairingCode,
  agentAnswer: isAgentAnswer,
  permissionRequestList: isPermissionRequestArray,
  permissionDecision: isPermissionDecision,
  tokenBalance: isTokenBalance,
  tokenLedger: isTokenLedger,
  speechFinalEvent: isSpeechFinalEvent,
  speechStreamCreated: isSpeechStreamCreated,
  speechAudioChunkAck: isSpeechAudioChunkAck,
  speechReplay: isSpeechReplay,
  speechTerminalEvent: isSpeechTerminalEvent,
  translationEvent: isTranslationEvent,
  conversationSession: isConversationSession,
  conversationSessionCreated: isConversationSessionCreated,
  conversationInviteClaimed: isConversationInviteClaimed,
  conversationLiveSnapshot: isConversationLiveSnapshot,
  conversationTurn: isConversationTurn,
  conversationReplay: isConversationReplay,
};

function applyMutation(
  source: unknown,
  mutation: (typeof corpus.mutations)[number]
): unknown {
  const clone = structuredClone(source) as Record<string | number, unknown>;
  let target = clone;
  for (const part of mutation.path.slice(0, -1) as PathPart[]) {
    target = target[part] as Record<string | number, unknown>;
  }
  const leaf = mutation.path.at(-1) as PathPart;
  if (mutation.operation === "delete") {
    delete target[leaf];
  } else {
    target[leaf] = "value" in mutation ? mutation.value : undefined;
  }
  return clone;
}

describe("canonical shared-contract drift corpus", () => {
  it("accepts every canonical Mobile response fixture", () => {
    expect(corpus.fixtureVersion).toBe(1);
    for (const fixture of corpus.validCases) {
      const validate = validators[fixture.validator];
      expect(validate, `unknown Mobile validator ${fixture.validator}`).toBeDefined();
      expect(validate(fixture.value), fixture.id).toBe(true);
    }
  });

  it("rejects every canonical required-field and enum mutation", () => {
    const fixtures = new Map(
      corpus.validCases.map((fixture) => [fixture.id, fixture.value] as const)
    );
    for (const mutation of corpus.mutations) {
      const validate = validators[mutation.validator];
      const source = fixtures.get(mutation.from);
      expect(validate, `unknown Mobile validator ${mutation.validator}`).toBeDefined();
      expect(source, `unknown source fixture ${mutation.from}`).toBeDefined();
      expect(validate(applyMutation(source, mutation)), mutation.id).toBe(false);
    }
  });
});
