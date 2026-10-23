import type { ModelSelectionView } from "@zcode/services";
import { encodeCustomModelValue } from "@/lib/zcodeCustomModelValue.js";

// 登录页只做首次引导；用户主动跳过后不再每次启动强制弹出，配置好 Provider 后自动清除。
const LOGIN_GATE_SKIP_STORAGE_KEY = "pcbwish.loginGateSkipped";

export function normalizeLoginCustomBaseUrl(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  try {
    const url = new URL(trimmed);
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return null;
    }
    return trimmed.replace(/\/+$/u, "");
  } catch {
    return null;
  }
}

// 自定义平台没有内置推荐配置，登录时必须写入手动模型允许的完整字段，其余执行字段由系统默认补齐。
export function buildLoginCustomModelConfig() {
  return {
    enabled: true,
    properties: {
      contextWindow: 128_000,
      supportsJsonSchemaOutput: true,
      supportsNativeWebSearch: false,
      supportsMidConversationSystem: false,
      inputFormat: {
        supportsImage: false,
        supportsVideo: false,
        supportsPdf: false,
      },
    },
    optionSpecs: {
      reasoningLevel: { values: ["disabled"], map: "{}" },
      maxOutputTokens: { max: 8_192 },
    },
  } as const;
}

export function resolveLoginCustomProviderName(baseUrl: string): string {
  try {
    return new URL(baseUrl).hostname || "Custom";
  } catch {
    return "Custom";
  }
}

export function isLoginGateSkipped(): boolean {
  try {
    return globalThis.localStorage?.getItem(LOGIN_GATE_SKIP_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function setLoginGateSkipped(skipped: boolean): void {
  try {
    if (skipped) {
      globalThis.localStorage?.setItem(LOGIN_GATE_SKIP_STORAGE_KEY, "1");
    } else {
      globalThis.localStorage?.removeItem(LOGIN_GATE_SKIP_STORAGE_KEY);
    }
  } catch {
    // 存储不可用时只影响“下次是否再提示”，不能阻断登录流程。
  }
}

export function buildLoginApiKeyDefaultModelPreferenceFromSelection(
  view: ModelSelectionView,
  providerId: string,
): string | null {
  const firstModel = view.providers.find((provider) => provider.providerId === providerId)
    ?.models[0]?.modelId;
  return firstModel ? encodeCustomModelValue(providerId, firstModel) : null;
}

export function resolveHydratedLoginApiKeyValue({
  savedApiKey,
  userEdited,
}: {
  savedApiKey: string;
  userEdited: boolean;
}): string | null {
  if (userEdited) {
    return null;
  }
  return savedApiKey.trim();
}
