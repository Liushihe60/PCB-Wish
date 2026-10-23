import { describe, expect, it } from "vitest";
import {
  buildLoginApiKeyDefaultModelPreferenceFromSelection,
  normalizeLoginCustomBaseUrl,
  resolveHydratedLoginApiKeyValue,
  resolveLoginCustomProviderName,
} from "@/login/LoginApiKeyForm.helpers.js";
import { encodeCustomModelValue } from "@/lib/zcodeCustomModelValue.js";

describe("LoginApiKeyForm provider update helpers", () => {
  it("自定义平台只接受 http/https 服务地址，并按域名命名 Provider", () => {
    expect(normalizeLoginCustomBaseUrl(" https://api.deepseek.com/v1/ ")).toBe(
      "https://api.deepseek.com/v1",
    );
    expect(normalizeLoginCustomBaseUrl("api.deepseek.com")).toBeNull();
    expect(normalizeLoginCustomBaseUrl("ftp://api.deepseek.com")).toBeNull();
    expect(normalizeLoginCustomBaseUrl("   ")).toBeNull();
    expect(resolveLoginCustomProviderName("https://api.deepseek.com/v1")).toBe(
      "api.deepseek.com",
    );
  });

  it("新 Provider Runtime 从 Selection View 选择自定义登录后的默认模型", () => {
    expect(
      buildLoginApiKeyDefaultModelPreferenceFromSelection(
        {
          revision: 3,
          providers: [
            {
              providerId: "custom-api",
              config: {
                kind: "api",
                models: ["custom-model-a", "custom-model-b"],
              },
              models: [
                { modelId: "custom-model-a", config: {} },
                { modelId: "custom-model-b", config: {} },
              ],
            },
          ],
        },
        "custom-api",
      ),
    ).toBe(encodeCustomModelValue("custom-api", "custom-model-a"));
  });

  it("已保存 API key 只在用户尚未编辑输入框时回填", () => {
    expect(
      resolveHydratedLoginApiKeyValue({
        savedApiKey: " sk-saved ",
        userEdited: false,
      }),
    ).toBe("sk-saved");
    expect(
      resolveHydratedLoginApiKeyValue({
        savedApiKey: "sk-saved",
        userEdited: true,
      }),
    ).toBeNull();
  });
});
