import { BUILTIN_PROVIDER_TEMPLATE_IDS, type Locale } from "@zcode/shared";
import type { ModelSelectionView } from "@zcode/services";
import { encodeCustomModelValue } from "@/lib/zcodeCustomModelValue.js";

export type ApiKeyProviderChoice = "zai" | "bigmodel" | "custom";

// 登录页只做首次引导；用户主动跳过后不再每次启动强制弹出，配置好 Provider 后自动清除。
const LOGIN_GATE_SKIP_STORAGE_KEY = "pcbwish.loginGateSkipped";

export function resolveLoginApiKeyDefaultProvider(locale: Locale): ApiKeyProviderChoice {
  return locale === "zh-CN" ? "bigmodel" : "zai";
}

export function resolveLoginApiKeyTemplateId(
  choice: ApiKeyProviderChoice,
): "zai-api" | "bigmodel-api" | undefined {
  if (choice === "custom") {
    return undefined;
  }
  return choice === "zai"
    ? BUILTIN_PROVIDER_TEMPLATE_IDS.zai
    : BUILTIN_PROVIDER_TEMPLATE_IDS.bigmodel;
}

export function resolveLoginApiKeyProviderLabel(choice: ApiKeyProviderChoice): string {
  if (choice === "custom") {
    return "Custom";
  }
  // Welcome Screen API Key 错误提示需要使用 BigModel 品牌固定写法。
  return choice === "zai" ? "Z.ai" : "BigModel";
}

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

export function shouldShowLoginApiKeyLink(
  apiKeyValue: string,
  apiKeyUrl: string | undefined,
): boolean {
  return Boolean(apiKeyUrl) && apiKeyValue.trim().length === 0;
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
