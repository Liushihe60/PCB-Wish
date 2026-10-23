import { useState } from "react";
import { isApiKeyAccess } from "@zcode/provider";
import { GlobeIcon, Loader2Icon, TriangleAlertIcon } from "lucide-react";
import { BIGMODEL_PROVIDER_ID, ZAI_PROVIDER_ID } from "@zcode/shared";
import {
  TID_LOGIN_API_KEY_CONTINUE_BUTTON,
  TID_LOGIN_API_KEY_ERROR,
  TID_LOGIN_API_KEY_INPUT,
  TID_LOGIN_API_KEY_PROVIDER_ITEM,
  TID_LOGIN_API_KEY_PROVIDER_TRIGGER,
  TID_LOGIN_API_KEY_SKIP_BUTTON,
  testId,
} from "@zcode/shared/test-ids";
import { Alert, AlertDescription } from "@/components/ui/alert.js";
import { Button } from "@/components/ui/button.js";
import { Input } from "@/components/ui/input.js";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select.js";
import { usePlatform } from "@/hooks/usePlatform.js";
import { useProviderSettingsView } from "@/hooks/useProviderSettingsView.js";
import { useServices } from "@/hooks/useServices.js";
import { useZCodeIntl } from "@/i18n/IntlProvider.js";
import { logger } from "@/logger.js";
import { renderOAuthProviderIcon } from "@/lib/oauthProviderIcon.js";
import {
  buildLoginApiKeyDefaultModelPreferenceFromSelection,
  buildLoginCustomModelConfig,
  normalizeLoginCustomBaseUrl,
  resolveLoginApiKeyDefaultProvider,
  resolveLoginApiKeyTemplateId,
  resolveLoginApiKeyProviderLabel,
  resolveLoginCustomProviderName,
  setLoginGateSkipped,
  shouldShowLoginApiKeyLink,
  type ApiKeyProviderChoice,
} from "@/login/LoginApiKeyForm.helpers.js";
import { useZCodeStore } from "@/store/StoreProvider.js";

interface LoginApiKeyFormProps {
  onSaved: () => void | Promise<void>;
  onSkip?: () => void | Promise<void>;
}

export function LoginApiKeyForm({ onSaved, onSkip }: LoginApiKeyFormProps) {
  const { intl, locale } = useZCodeIntl();
  const platform = usePlatform();
  const { modelSelectionService, providerSettingsService } = useServices();
  const markApiKeyLoginSuccess = useZCodeStore((state) => state.markApiKeyLoginSuccess);
  const [providerChoice, setProviderChoice] = useState<ApiKeyProviderChoice>(() =>
    resolveLoginApiKeyDefaultProvider(locale),
  );
  const [apiKeyValue, setApiKeyValue] = useState("");
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const [customModelId, setCustomModelId] = useState("");
  const [saving, setSaving] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const providerSettingsRead = useProviderSettingsView();
  const providerSettingsView =
    providerSettingsRead.state.status === "ready" ? providerSettingsRead.state.view : null;

  const isCustomProvider = providerChoice === "custom";
  const providerLabel = resolveLoginApiKeyProviderLabel(providerChoice);
  const templateId = resolveLoginApiKeyTemplateId(providerChoice);
  const templateAccess = providerSettingsView?.providerTemplates.find(
    (template) => template.templateId === templateId,
  )?.config.access;
  const apiKeyUrl = isApiKeyAccess(templateAccess) ? templateAccess.apiKeyManagementUrl : undefined;
  // 用户已经输入或回填 API Key 后，右侧获取入口会挤占密码输入区域。
  const showApiKeyLink = shouldShowLoginApiKeyLink(apiKeyValue, apiKeyUrl ?? undefined);

  const saveApiKeyProvider = async () => {
    const apiKey = apiKeyValue.trim();
    if (!apiKey) {
      setError(intl.formatMessage({ id: "login.apiKey.emptyError" }));
      return;
    }

    const customBaseUrlNormalized = normalizeLoginCustomBaseUrl(customBaseUrl);
    if (isCustomProvider) {
      if (!customBaseUrl.trim()) {
        setError(intl.formatMessage({ id: "login.apiKey.custom.baseUrlRequired" }));
        return;
      }
      if (!customBaseUrlNormalized) {
        setError(intl.formatMessage({ id: "login.apiKey.custom.baseUrlInvalid" }));
        return;
      }
      if (!customModelId.trim()) {
        setError(intl.formatMessage({ id: "login.apiKey.custom.modelRequired" }));
        return;
      }
    }

    setSaving(true);
    setError(null);
    try {
      let providerId: string;
      if (isCustomProvider && customBaseUrlNormalized) {
        const created = await providerSettingsService.createPersonalProvider({
          providerName: resolveLoginCustomProviderName(customBaseUrlNormalized),
          initialConfig: {
            access: { type: "api-key", apiKey },
            api: { type: "openai-chat-completions", baseUrl: customBaseUrlNormalized },
          },
        });
        await providerSettingsService.addPersonalModel(
          created.providerId,
          customModelId.trim(),
          buildLoginCustomModelConfig(),
          false,
        );
        providerId = created.providerId;
      } else {
        const template = (await providerSettingsService.getView()).providerTemplates.find(
          (item) => item.templateId === templateId,
        );
        if (!template || !isApiKeyAccess(template.config.access)) {
          setError(
            intl.formatMessage(
              { id: "login.apiKey.providerMissingError" },
              { provider: providerLabel },
            ),
          );
          return;
        }

        const created = await providerSettingsService.createPersonalProvider({
          templateId,
          initialConfig: { access: { type: template.config.access.type, apiKey } },
        });
        providerId = created.providerId;
      }

      const defaultModelPreference = buildLoginApiKeyDefaultModelPreferenceFromSelection(
        await modelSelectionService.getView(),
        providerId,
      );
      markApiKeyLoginSuccess(defaultModelPreference);
      setLoginGateSkipped(false);
      await onSaved();
    } catch (saveError) {
      logger.error("[LoginEntry] 保存 API Key provider 失败", {
        templateId,
        isCustomProvider,
        error: saveError,
      });
      setError(
        intl.formatMessage(
          { id: "login.apiKey.saveError" },
          {
            error: saveError instanceof Error ? saveError.message : String(saveError),
          },
        ),
      );
    } finally {
      setSaving(false);
    }
  };

  const skipLogin = async () => {
    setSkipping(true);
    setError(null);
    try {
      setLoginGateSkipped(true);
      await onSkip?.();
    } catch (skipError) {
      logger.error("[LoginEntry] 跳过 API Key 登录失败", { error: skipError });
      setLoginGateSkipped(false);
      setError(
        intl.formatMessage(
          { id: "login.apiKey.skipError" },
          {
            error: skipError instanceof Error ? skipError.message : String(skipError),
          },
        ),
      );
    } finally {
      setSkipping(false);
    }
  };

  const busy = saving || skipping;

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-ui-base font-medium text-foreground">
          {intl.formatMessage({ id: "login.apiKey.title" })}
        </h2>
        <div className="space-y-2">
          <div>
            <Select
              value={providerChoice}
              onValueChange={(value) => {
                setProviderChoice(value as ApiKeyProviderChoice);
                setError(null);
              }}
              disabled={busy}
            >
              <SelectTrigger
                id="login-api-key-provider"
                size="lg"
                className="h-10 w-full text-ui-base"
                data-testid={TID_LOGIN_API_KEY_PROVIDER_TRIGGER}
                aria-label={intl.formatMessage({
                  id: "login.apiKey.providerLabel",
                })}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end" className="rounded-lg">
                <SelectItem
                  value="zai"
                  className="rounded-md"
                  data-testid={testId(TID_LOGIN_API_KEY_PROVIDER_ITEM, "zai")}
                >
                  {renderOAuthProviderIcon(ZAI_PROVIDER_ID, "size-4")}
                  {intl.formatMessage({ id: "login.apiKey.provider.zai" })}
                </SelectItem>
                <SelectItem
                  value="bigmodel"
                  className="rounded-md"
                  data-testid={testId(TID_LOGIN_API_KEY_PROVIDER_ITEM, "bigmodel")}
                >
                  {renderOAuthProviderIcon(BIGMODEL_PROVIDER_ID, "size-4")}
                  {intl.formatMessage({
                    id: "login.apiKey.provider.bigmodel",
                  })}
                </SelectItem>
                <SelectItem
                  value="custom"
                  className="rounded-md"
                  data-testid={testId(TID_LOGIN_API_KEY_PROVIDER_ITEM, "custom")}
                >
                  <GlobeIcon className="size-4" aria-hidden="true" />
                  {intl.formatMessage({ id: "login.apiKey.provider.custom" })}
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          {isCustomProvider ? (
            <>
              <Input
                id="login-api-key-base-url"
                type="url"
                size="lg"
                className="h-10 w-full text-ui-base"
                data-testid="login-api-key-base-url-input"
                aria-label={intl.formatMessage({
                  id: "login.apiKey.custom.baseUrl",
                })}
                value={customBaseUrl}
                placeholder={intl.formatMessage({
                  id: "login.apiKey.custom.baseUrlPlaceholder",
                })}
                autoComplete="off"
                disabled={busy}
                onChange={(event) => {
                  setCustomBaseUrl(event.target.value);
                  setError(null);
                }}
              />
              <Input
                id="login-api-key-model"
                type="text"
                size="lg"
                className="h-10 w-full text-ui-base"
                data-testid="login-api-key-model-input"
                aria-label={intl.formatMessage({
                  id: "login.apiKey.custom.model",
                })}
                value={customModelId}
                placeholder={intl.formatMessage({
                  id: "login.apiKey.custom.modelPlaceholder",
                })}
                autoComplete="off"
                disabled={busy}
                onChange={(event) => {
                  setCustomModelId(event.target.value);
                  setError(null);
                }}
              />
            </>
          ) : null}
          <div className="relative">
            <Input
              id="login-api-key"
              type="password"
              size="lg"
              className={`h-10 w-full text-ui-base ${showApiKeyLink ? "pr-28" : ""}`}
              data-testid={TID_LOGIN_API_KEY_INPUT}
              aria-label={intl.formatMessage({
                id: "login.apiKey.placeholder",
              })}
              value={apiKeyValue}
              placeholder={intl.formatMessage({
                id: "login.apiKey.placeholder",
              })}
              autoComplete="off"
              onChange={(event) => {
                setApiKeyValue(event.target.value);
                setError(null);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && apiKeyValue.trim() && !busy) {
                  void saveApiKeyProvider();
                }
              }}
            />
            {showApiKeyLink ? (
              <button
                type="button"
                className="absolute right-3.5 top-1/2 -translate-y-1/2 text-ui-base font-medium text-brand underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
                disabled={busy}
                onClick={() => {
                  if (apiKeyUrl) {
                    platform.openExternal(apiKeyUrl);
                  }
                }}
              >
                {intl.formatMessage({ id: "login.apiKey.getApiKey" })}
              </button>
            ) : null}
          </div>
        </div>
      </div>

      {error ? (
        <Alert variant="destructive" data-testid={TID_LOGIN_API_KEY_ERROR}>
          <TriangleAlertIcon className="size-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}

      <div className="space-y-2">
        <Button
          type="button"
          className="h-10 w-full text-ui-base"
          size="lg"
          data-testid={TID_LOGIN_API_KEY_CONTINUE_BUTTON}
          disabled={
            !apiKeyValue.trim() ||
            (isCustomProvider && (!customBaseUrl.trim() || !customModelId.trim())) ||
            busy
          }
          onClick={() => void saveApiKeyProvider()}
        >
          {saving ? <Loader2Icon className="size-4 animate-spin" /> : null}
          {intl.formatMessage({ id: "login.apiKey.continue" })}
        </Button>
        {onSkip ? (
          <Button
            type="button"
            variant="link"
            className="h-7 w-full text-ui-base text-foreground-subtle hover:text-foreground"
            data-testid={TID_LOGIN_API_KEY_SKIP_BUTTON}
            disabled={busy}
            onClick={() => void skipLogin()}
          >
            {skipping ? <Loader2Icon className="size-4 animate-spin" /> : null}
            {intl.formatMessage({ id: "login.skip" })}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
