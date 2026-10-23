import { useState } from "react";
import { Loader2Icon, TriangleAlertIcon } from "lucide-react";
import {
  TID_LOGIN_API_KEY_CONTINUE_BUTTON,
  TID_LOGIN_API_KEY_ERROR,
  TID_LOGIN_API_KEY_INPUT,
  TID_LOGIN_API_KEY_SKIP_BUTTON,
} from "@zcode/shared/test-ids";
import { Alert, AlertDescription } from "@/components/ui/alert.js";
import { Button } from "@/components/ui/button.js";
import { Input } from "@/components/ui/input.js";
import { useServices } from "@/hooks/useServices.js";
import { useZCodeIntl } from "@/i18n/IntlProvider.js";
import { logger } from "@/logger.js";
import {
  buildLoginApiKeyDefaultModelPreferenceFromSelection,
  buildLoginCustomModelConfig,
  normalizeLoginCustomBaseUrl,
  resolveLoginCustomProviderName,
  setLoginGateSkipped,
} from "@/login/LoginApiKeyForm.helpers.js";
import { useZCodeStore } from "@/store/StoreProvider.js";

interface LoginApiKeyFormProps {
  onSaved: () => void | Promise<void>;
  onSkip?: () => void | Promise<void>;
}

export function LoginApiKeyForm({ onSaved, onSkip }: LoginApiKeyFormProps) {
  const { intl } = useZCodeIntl();
  const { modelSelectionService, providerSettingsService } = useServices();
  const markApiKeyLoginSuccess = useZCodeStore((state) => state.markApiKeyLoginSuccess);
  const [baseUrlValue, setBaseUrlValue] = useState("");
  const [modelIdValue, setModelIdValue] = useState("");
  const [apiKeyValue, setApiKeyValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [skipping, setSkipping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const saveCustomProvider = async () => {
    const baseUrl = normalizeLoginCustomBaseUrl(baseUrlValue);
    if (!baseUrlValue.trim()) {
      setError(intl.formatMessage({ id: "login.apiKey.custom.baseUrlRequired" }));
      return;
    }
    if (!baseUrl) {
      setError(intl.formatMessage({ id: "login.apiKey.custom.baseUrlInvalid" }));
      return;
    }
    const modelId = modelIdValue.trim();
    if (!modelId) {
      setError(intl.formatMessage({ id: "login.apiKey.custom.modelRequired" }));
      return;
    }
    const apiKey = apiKeyValue.trim();
    if (!apiKey) {
      setError(intl.formatMessage({ id: "login.apiKey.emptyError" }));
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const created = await providerSettingsService.createPersonalProvider({
        providerName: resolveLoginCustomProviderName(baseUrl),
        initialConfig: {
          access: { type: "api-key", apiKey },
          api: { type: "openai-chat-completions", baseUrl },
        },
      });
      await providerSettingsService.addPersonalModel(
        created.providerId,
        modelId,
        buildLoginCustomModelConfig(),
        false,
      );
      const defaultModelPreference = buildLoginApiKeyDefaultModelPreferenceFromSelection(
        await modelSelectionService.getView(),
        created.providerId,
      );
      markApiKeyLoginSuccess(defaultModelPreference);
      setLoginGateSkipped(false);
      await onSaved();
    } catch (saveError) {
      logger.error("[LoginEntry] 保存自定义模型服务失败", { error: saveError });
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
  const canSubmit = Boolean(baseUrlValue.trim() && modelIdValue.trim() && apiKeyValue.trim());

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <h2 className="text-ui-base font-medium text-foreground">
          {intl.formatMessage({ id: "login.apiKey.title" })}
        </h2>
        <p className="text-ui-base text-foreground-subtle">
          {intl.formatMessage({ id: "login.apiKey.custom.hint" })}
        </p>
        <div className="space-y-2">
          <Input
            id="login-api-key-base-url"
            type="url"
            size="lg"
            className="h-10 w-full text-ui-base"
            data-testid="login-api-key-base-url-input"
            aria-label={intl.formatMessage({ id: "login.apiKey.custom.baseUrl" })}
            value={baseUrlValue}
            placeholder={intl.formatMessage({ id: "login.apiKey.custom.baseUrlPlaceholder" })}
            autoComplete="off"
            disabled={busy}
            onChange={(event) => {
              setBaseUrlValue(event.target.value);
              setError(null);
            }}
          />
          <Input
            id="login-api-key-model"
            type="text"
            size="lg"
            className="h-10 w-full text-ui-base"
            data-testid="login-api-key-model-input"
            aria-label={intl.formatMessage({ id: "login.apiKey.custom.model" })}
            value={modelIdValue}
            placeholder={intl.formatMessage({ id: "login.apiKey.custom.modelPlaceholder" })}
            autoComplete="off"
            disabled={busy}
            onChange={(event) => {
              setModelIdValue(event.target.value);
              setError(null);
            }}
          />
          <Input
            id="login-api-key"
            type="password"
            size="lg"
            className="h-10 w-full text-ui-base"
            data-testid={TID_LOGIN_API_KEY_INPUT}
            aria-label={intl.formatMessage({ id: "login.apiKey.placeholder" })}
            value={apiKeyValue}
            placeholder={intl.formatMessage({ id: "login.apiKey.placeholder" })}
            autoComplete="off"
            disabled={busy}
            onChange={(event) => {
              setApiKeyValue(event.target.value);
              setError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && canSubmit && !busy) {
                void saveCustomProvider();
              }
            }}
          />
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
          disabled={!canSubmit || busy}
          onClick={() => void saveCustomProvider()}
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
