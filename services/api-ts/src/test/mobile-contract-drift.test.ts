import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  agentAnswerSchema,
  audioUploadResultSchema,
  captureSessionListSchema,
  captureSessionSchema,
  captureSummarySchema,
  conversationInviteClaimedSchema,
  conversationLiveSnapshotSchema,
  conversationReplaySchema,
  conversationSessionCreatedSchema,
  conversationSessionSchema,
  conversationTurnSchema,
  memoryNodeListSchema,
  memoryNodeSchema,
  memorySearchResponseSchema,
  pairingCodeSchema,
  permissionDecisionSchema,
  permissionRequestListSchema,
  speechFinalEventSchema,
  speechAudioChunkAckSchema,
  speechReplaySchema,
  speechStreamCreatedSchema,
  speechTerminalEventSchema,
  tokenBalanceSchema,
  tokenLedgerEntryListSchema,
  translationEventSchema,
} from "@voxlog/contracts";

type PathPart = string | number;
type FixtureCase = {
  id: string;
  validator: string;
  value: unknown;
};
type Mutation = {
  id: string;
  validator: string;
  from: string;
  operation: "delete" | "replace";
  path: PathPart[];
  value?: unknown;
};
type Corpus = {
  fixtureVersion: number;
  validCases: FixtureCase[];
  mutations: Mutation[];
};

const corpus = JSON.parse(
  readFileSync(
    new URL("../../../../packages/contracts/fixtures/mobile-wire-v1.json", import.meta.url),
    "utf8"
  )
) as Corpus;

const validators: Record<string, (value: unknown) => boolean> = {
  captureSession: (value) => captureSessionSchema.safeParse(value).success,
  captureSessionList: (value) => captureSessionListSchema.safeParse(value).success,
  captureSummary: (value) => captureSummarySchema.safeParse(value).success,
  audioUploadResult: (value) => audioUploadResultSchema.safeParse(value).success,
  memoryNode: (value) => memoryNodeSchema.safeParse(value).success,
  memoryNodeList: (value) => memoryNodeListSchema.safeParse(value).success,
  memorySearchResponse: (value) => memorySearchResponseSchema.safeParse(value).success,
  pairingCode: (value) => pairingCodeSchema.safeParse(value).success,
  agentAnswer: (value) => agentAnswerSchema.safeParse(value).success,
  permissionRequestList: (value) => permissionRequestListSchema.safeParse(value).success,
  permissionDecision: (value) => permissionDecisionSchema.safeParse(value).success,
  tokenBalance: (value) => tokenBalanceSchema.safeParse(value).success,
  tokenLedger: (value) => tokenLedgerEntryListSchema.safeParse(value).success,
  speechFinalEvent: (value) => speechFinalEventSchema.safeParse(value).success,
  speechStreamCreated: (value) => speechStreamCreatedSchema.safeParse(value).success,
  speechAudioChunkAck: (value) => speechAudioChunkAckSchema.safeParse(value).success,
  speechReplay: (value) => speechReplaySchema.safeParse(value).success,
  speechTerminalEvent: (value) => speechTerminalEventSchema.safeParse(value).success,
  translationEvent: (value) => translationEventSchema.safeParse(value).success,
  conversationSession: (value) => conversationSessionSchema.safeParse(value).success,
  conversationSessionCreated: (value) =>
    conversationSessionCreatedSchema.safeParse(value).success,
  conversationInviteClaimed: (value) =>
    conversationInviteClaimedSchema.safeParse(value).success,
  conversationLiveSnapshot: (value) =>
    conversationLiveSnapshotSchema.safeParse(value).success,
  conversationTurn: (value) => conversationTurnSchema.safeParse(value).success,
  conversationReplay: (value) => conversationReplaySchema.safeParse(value).success,
};

function applyMutation(source: unknown, mutation: Mutation): unknown {
  const clone = structuredClone(source) as Record<string | number, unknown>;
  let target = clone;
  for (const part of mutation.path.slice(0, -1)) {
    target = target[part] as Record<string | number, unknown>;
  }
  const leaf = mutation.path.at(-1);
  if (leaf === undefined) {
    throw new Error(`mutation ${mutation.id} has an empty path`);
  }
  if (mutation.operation === "delete") {
    delete target[leaf];
  } else {
    target[leaf] = mutation.value;
  }
  return clone;
}

test("canonical Mobile fixtures satisfy every shared wire schema", () => {
  assert.equal(corpus.fixtureVersion, 1);
  for (const fixture of corpus.validCases) {
    const validate = validators[fixture.validator];
    assert.ok(validate, `unknown shared validator ${fixture.validator}`);
    assert.equal(validate(fixture.value), true, fixture.id);
  }
});

test("canonical drift mutations fail the matching shared wire schemas", () => {
  const fixtures = new Map(corpus.validCases.map((fixture) => [fixture.id, fixture.value]));
  for (const mutation of corpus.mutations) {
    const validate = validators[mutation.validator];
    const source = fixtures.get(mutation.from);
    assert.ok(validate, `unknown shared validator ${mutation.validator}`);
    assert.notEqual(source, undefined, `unknown source fixture ${mutation.from}`);
    assert.equal(validate(applyMutation(source, mutation)), false, mutation.id);
  }
});
