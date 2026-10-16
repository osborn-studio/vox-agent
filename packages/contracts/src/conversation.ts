import { z } from "zod";

import {
  speechFinalEventSchema,
  speechLanguageCodeSchema,
  speechPartialEventSchema
} from "./streaming-speech.js";
import {
  translationFinalEventSchema,
  translationSourceCitationSchema
} from "./translation.js";

export const CONVERSATION_SESSION_SCHEMA = "voxagent.conversation.session" as const;
export const CONVERSATION_SESSION_SCHEMA_VERSION = 1 as const;
export const CONVERSATION_TURN_SCHEMA = "voxagent.conversation.turn" as const;
export const CONVERSATION_TURN_SCHEMA_VERSION = 1 as const;
export const CONVERSATION_LIVE_SCHEMA = "voxagent.conversation.live" as const;
export const CONVERSATION_LIVE_SCHEMA_VERSION = 1 as const;

export const conversationModeSchema = z.enum(["FACE_TO_FACE", "REMOTE_QR"]);
export const conversationStatusSchema = z.enum(["WAITING", "ACTIVE", "ENDED"]);
export const conversationParticipantRoleSchema = z.enum(["HOST", "GUEST"]);

export const createConversationSessionRequestSchema = z
  .object({
    mode: conversationModeSchema,
    hostLanguage: speechLanguageCodeSchema,
    guestLanguage: speechLanguageCodeSchema
  })
  .strict()
  .refine((request) => request.hostLanguage !== request.guestLanguage, {
    message: "hostLanguage and guestLanguage must differ",
    path: ["guestLanguage"]
  });

export const conversationSessionSchema = z
  .object({
    schema: z.literal(CONVERSATION_SESSION_SCHEMA),
    schemaVersion: z.literal(CONVERSATION_SESSION_SCHEMA_VERSION),
    id: z.string().min(1).max(160),
    mode: conversationModeSchema,
    status: conversationStatusSchema,
    hostLanguage: speechLanguageCodeSchema,
    guestLanguage: speechLanguageCodeSchema,
    participantCount: z.number().int().min(1).max(2),
    createdAt: z.string().datetime({ offset: true }),
    expiresAt: z.string().datetime({ offset: true }),
    nextSequence: z.number().int().positive()
  })
  .strict()
  .refine((session) => session.hostLanguage !== session.guestLanguage, {
    message: "hostLanguage and guestLanguage must differ",
    path: ["guestLanguage"]
  });

export const conversationInviteSchema = z
  .object({
    inviteToken: z.string().min(20).max(512),
    qrPayload: z.string().min(1).max(1_024),
    expiresAt: z.string().datetime({ offset: true })
  })
  .strict()
  .refine(
    (invite) =>
      invite.qrPayload ===
      `voxagent://conversation/join#v=1&token=${encodeURIComponent(invite.inviteToken)}`,
    {
      message: "qrPayload must contain only version 1 and the opaque invite token",
      path: ["qrPayload"]
    }
  );

export const conversationSessionCreatedSchema = z
  .object({
    session: conversationSessionSchema,
    hostAccessToken: z.string().min(20).max(512),
    localGuestAccessToken: z.string().min(20).max(512).nullable(),
    invite: conversationInviteSchema.nullable()
  })
  .strict()
  .superRefine((created, context) => {
    if (
      created.session.mode === "REMOTE_QR" &&
      (created.session.status !== "WAITING" ||
        created.session.participantCount !== 1 ||
        created.localGuestAccessToken !== null ||
        created.invite === null)
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "REMOTE_QR creation must return one host and one invite"
      });
    }
    if (
      created.session.mode === "FACE_TO_FACE" &&
      (created.session.status !== "ACTIVE" ||
        created.session.participantCount !== 2 ||
        created.localGuestAccessToken === null ||
        created.invite !== null)
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "FACE_TO_FACE creation must return local host and guest capabilities"
      });
    }
  });

export const claimConversationInviteRequestSchema = z
  .object({
    inviteToken: z.string().min(1).max(512)
  })
  .strict();

export const conversationInviteClaimedSchema = z
  .object({
    session: conversationSessionSchema,
    participantRole: z.literal("GUEST"),
    accessToken: z.string().min(20).max(512)
  })
  .strict()
  .refine(
    (claimed) =>
      claimed.session.status === "ACTIVE" && claimed.session.participantCount === 2,
    { message: "claimed conversation must be active with two participants" }
  );

export const conversationSpeechFinalEventSchema = speechFinalEventSchema.extend({
  sessionId: z.string().min(1).max(160)
});

export const conversationSpeechPartialEventSchema = speechPartialEventSchema.extend({
  sessionId: z.string().min(1).max(160)
});

export const conversationTranslationFinalEventSchema = translationFinalEventSchema.extend({
  payload: translationFinalEventSchema.shape.payload.extend({
    sourceSegments: z.array(translationSourceCitationSchema).length(1)
  })
});

export const publishConversationTurnRequestSchema = z
  .object({
    clientEventId: z.string().min(1).max(160),
    speechEvent: conversationSpeechFinalEventSchema,
    translationEvent: conversationTranslationFinalEventSchema
  })
  .strict()
  .superRefine((request, context) => {
    const speech = request.speechEvent;
    const translated = request.translationEvent.payload;
    const citation = translated.sourceSegments[0];
    const payload = speech.payload;
    if (
      translated.sourceLanguage !== payload.language.code ||
      citation.streamId !== speech.streamId ||
      citation.segmentId !== payload.segmentId ||
      citation.revision !== payload.revision ||
      citation.audioStartMs !== payload.audioStartMs ||
      citation.audioEndMs !== payload.audioEndMs
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "translation citation and source language must match the speech event",
        path: ["translationEvent"]
      });
    }
  });

export const conversationTurnSchema = z
  .object({
    schema: z.literal(CONVERSATION_TURN_SCHEMA),
    schemaVersion: z.literal(CONVERSATION_TURN_SCHEMA_VERSION),
    type: z.literal("conversation.turn"),
    eventId: z.string().min(1).max(128),
    sessionId: z.string().min(1).max(160),
    sequence: z.number().int().positive(),
    participantRole: conversationParticipantRoleSchema,
    clientEventId: z.string().min(1).max(160),
    speechEvent: conversationSpeechFinalEventSchema,
    translationEvent: conversationTranslationFinalEventSchema,
    createdAt: z.string().datetime({ offset: true })
  })
  .strict()
  .superRefine((turn, context) => {
    if (turn.speechEvent.sessionId !== turn.sessionId) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "speech event sessionId must match the conversation turn",
        path: ["speechEvent", "sessionId"]
      });
    }
    const request = publishConversationTurnRequestSchema.safeParse({
      clientEventId: turn.clientEventId,
      speechEvent: turn.speechEvent,
      translationEvent: turn.translationEvent
    });
    if (!request.success) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "conversation turn source contract is invalid"
      });
    }
  });

export const conversationReplaySchema = z
  .object({
    sessionId: z.string().min(1).max(160),
    turns: z.array(conversationTurnSchema).max(1_000),
    nextSequence: z.number().int().positive(),
    status: z.enum(["WAITING", "ACTIVE"])
  })
  .strict();

export const publishConversationLiveRequestSchema = z
  .object({
    speechEvent: conversationSpeechPartialEventSchema
  })
  .strict();

export const conversationLiveDraftSchema = z
  .object({
    schema: z.literal(CONVERSATION_LIVE_SCHEMA),
    schemaVersion: z.literal(CONVERSATION_LIVE_SCHEMA_VERSION),
    type: z.literal("conversation.live"),
    sessionId: z.string().min(1).max(160),
    participantRole: conversationParticipantRoleSchema,
    streamId: z.string().min(1).max(128),
    segmentId: z.string().min(1).max(128),
    revision: z.number().int().positive(),
    sourceLanguage: speechLanguageCodeSchema,
    targetLanguage: speechLanguageCodeSchema,
    speechEvent: conversationSpeechPartialEventSchema,
    updatedAt: z.string().datetime({ offset: true })
  })
  .strict()
  .superRefine((draft, context) => {
    const speech = draft.speechEvent;
    if (
      draft.sessionId !== speech.sessionId ||
      draft.streamId !== speech.streamId ||
      draft.segmentId !== speech.payload.segmentId ||
      draft.revision !== speech.payload.revision ||
      draft.sourceLanguage !== speech.payload.language.code
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "live draft metadata must match its partial speech event",
        path: ["speechEvent"]
      });
    }
    if (draft.sourceLanguage === draft.targetLanguage) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "live draft language direction must contain two languages",
        path: ["targetLanguage"]
      });
    }
  });

export const conversationLiveSnapshotSchema = z
  .object({
    sessionId: z.string().min(1).max(160),
    host: conversationLiveDraftSchema.nullable(),
    guest: conversationLiveDraftSchema.nullable(),
    status: z.literal("ACTIVE")
  })
  .strict()
  .superRefine((snapshot, context) => {
    for (const [field, role] of [
      ["host", "HOST"],
      ["guest", "GUEST"]
    ] as const) {
      const draft = snapshot[field];
      if (
        draft !== null &&
        (draft.sessionId !== snapshot.sessionId ||
          draft.participantRole !== role)
      ) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: `${field} draft must match snapshot session and role`,
          path: [field]
        });
      }
    }
  });

export type ConversationMode = z.infer<typeof conversationModeSchema>;
export type ConversationSession = z.infer<typeof conversationSessionSchema>;
export type ConversationTurn = z.infer<typeof conversationTurnSchema>;
export type ConversationReplay = z.infer<typeof conversationReplaySchema>;
export type ConversationLiveDraft = z.infer<typeof conversationLiveDraftSchema>;
export type ConversationLiveSnapshot = z.infer<
  typeof conversationLiveSnapshotSchema
>;
