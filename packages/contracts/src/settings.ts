import { z } from "zod";

export const providerStatusSchema = z.object({
  configured: z.boolean()
});

export const llmRouteSchema = z.enum([
  "openai-gpt",
  "qwen-turbo",
  "doubao",
  "claude",
  "openai-compatible-custom",
  "ollama"
]);
export const sttRouteSchema = z.enum([
  "qwen-us",
  "qwen-cn",
  "openai-whisper",
  "siliconflow-whisper",
  "whispercpp-local",
  "qwen-local",
  "sherpa-sensevoice"
]);

const llmModelSchema = z.string().trim().min(1).max(200);
const secretInputSchema = z
  .string()
  .max(20_000)
  .refine((value) => !/[\r\n\0]/u.test(value), "credential contains invalid control characters");
const isLoopbackHost = (hostname: string) => {
  const host = hostname.toLowerCase();
  if (host === "localhost" || host === "[::1]" || host === "::1") return true;
  const octets = host.split(".");
  return (
    octets.length === 4 &&
    octets[0] === "127" &&
    octets.every((octet) => /^\d{1,3}$/u.test(octet) && Number(octet) <= 255)
  );
};
const optionalLlmBaseUrlSchema = z
  .string()
  .trim()
  .max(500)
  .refine((value) => {
    if (!value) return true;
    try {
      const url = new URL(value);
      return (
        (url.protocol === "http:" || url.protocol === "https:") &&
        (url.protocol === "https:" || isLoopbackHost(url.hostname)) &&
        !url.username &&
        !url.password &&
        !url.search &&
        !url.hash
      );
    } catch {
      return false;
    }
  }, "must use HTTPS, or HTTP on loopback, without credentials, query, or fragment");

export const profileSummarySchema = z.object({
  name: z.string().min(1).max(120),
  stt_main: sttRouteSchema,
  stt_fallback: sttRouteSchema,
  llm_main: llmRouteSchema,
  llm_fallback: llmRouteSchema
});

export const providerSettingsSchema = z.object({
  providers: z.object({
    dashscope_us: providerStatusSchema,
    dashscope_cn: providerStatusSchema,
    openai: providerStatusSchema,
    siliconflow: providerStatusSchema,
    doubao: providerStatusSchema,
    anthropic: providerStatusSchema,
    custom_llm: providerStatusSchema
  }),
  active_profile: z.string().min(1).max(120),
  digest_enhancement_enabled: z.boolean(),
  digest_enhancement_provider: z.string().min(1).max(120),
  llm_config: z.object({
    openai_model: llmModelSchema,
    qwen_model: llmModelSchema,
    doubao_model: llmModelSchema,
    anthropic_model: llmModelSchema,
    custom_base_url: optionalLlmBaseUrlSchema,
    custom_model: z.string().trim().max(200),
    ollama_model: llmModelSchema
  }),
  profiles: z.array(profileSummarySchema),
  backend_auth_required: z.boolean()
});

export const providerConnectivityCheckSchema = z.object({
  key: z.string(),
  label: z.string(),
  status: z.enum(["ok", "warn", "fail"]),
  message: z.string()
});

export const providerConnectivitySchema = z.object({
  ready: z.boolean(),
  active_profile: z.string(),
  recommended_stt_provider: z.string(),
  backend_url_reachable: z.boolean(),
  configured_provider_count: z.number().int().nonnegative(),
  checks: z.array(providerConnectivityCheckSchema)
});

export const providerQualificationEntrySchema = z.object({
  id: z.string().min(1).max(120),
  label: z.string().min(1).max(160),
  kind: z.enum(["stt", "llm"]),
  transport: z.enum(["cloud", "local"]),
  capabilities: z.array(z.string().min(1).max(80)).max(20),
  support: z.enum(["supported", "install_required", "deferred", "platform_unavailable"]),
  readiness: z.enum([
    "ready",
    "configured_unverified",
    "missing_credential",
    "missing_runtime",
    "not_implemented",
    "platform_unavailable"
  ]),
  configured: z.boolean(),
  runtime_available: z.boolean().nullable(),
  package_available: z.boolean().nullable(),
  assets_available: z.boolean().nullable(),
  probe_status: z.enum(["not_run", "passed", "failed", "not_applicable"]),
  active_roles: z
    .array(z.enum(["stt_main", "stt_fallback", "llm_main", "llm_fallback"]))
    .max(4),
  action: z.string().min(1).max(500),
  install_command: z.string().min(1).max(500).nullable(),
  asset_command: z.string().min(1).max(500).nullable()
});

export const providerQualificationSchema = z.object({
  contract_version: z.literal("2026-07-19.2"),
  platform: z.enum(["windows", "macos", "linux", "unknown"]),
  active_profile: z.string().min(1).max(120),
  network_environment: z.object({
    httpx_trust_env: z.literal(true),
    proxy_environment_configured: z.boolean(),
    no_proxy_configured: z.boolean()
  }),
  summary: z.object({
    qualified_count: z.number().int().nonnegative(),
    configured_count: z.number().int().nonnegative(),
    action_required_count: z.number().int().nonnegative(),
    active_routes_configured: z.boolean()
  }),
  providers: z.array(providerQualificationEntrySchema).max(100)
});

export const providerSettingsUpdateSchema = z
  .object({
    dashscope_key_us: secretInputSchema.optional(),
    dashscope_key_cn: secretInputSchema.optional(),
    openai_key: secretInputSchema.optional(),
    siliconflow_key: secretInputSchema.optional(),
    doubao_key: secretInputSchema.optional(),
    anthropic_key: secretInputSchema.optional(),
    custom_llm_key: secretInputSchema.optional(),
    openai_llm_model: llmModelSchema.optional(),
    qwen_llm_model: llmModelSchema.optional(),
    doubao_model: llmModelSchema.optional(),
    anthropic_model: llmModelSchema.optional(),
    custom_llm_base_url: optionalLlmBaseUrlSchema.optional(),
    custom_llm_model: z.string().trim().max(200).optional(),
    ollama_model: llmModelSchema.optional(),
    llm_main: llmRouteSchema.optional(),
    llm_fallback: llmRouteSchema.optional(),
    stt_main: sttRouteSchema.optional(),
    stt_fallback: sttRouteSchema.optional(),
    active_profile: z.string().min(1).max(120).optional(),
    digest_enhancement_enabled: z.boolean().optional(),
    digest_enhancement_provider: z.string().min(1).max(120).optional()
  })
  .strict()
  .superRefine((value, context) => {
    if (value.llm_main && value.llm_fallback && value.llm_main === value.llm_fallback) {
      context.addIssue({
        code: "custom",
        path: ["llm_fallback"],
        message: "LLM main and fallback routes must be different"
      });
    }
    if (value.stt_main && value.stt_fallback && value.stt_main === value.stt_fallback) {
      context.addIssue({
        code: "custom",
        path: ["stt_fallback"],
        message: "STT main and fallback routes must be different"
      });
    }
  });

export type ProviderSettings = z.infer<typeof providerSettingsSchema>;
export type ProviderConnectivity = z.infer<typeof providerConnectivitySchema>;
export type ProviderQualification = z.infer<typeof providerQualificationSchema>;
export type ProviderSettingsUpdate = z.infer<typeof providerSettingsUpdateSchema>;
