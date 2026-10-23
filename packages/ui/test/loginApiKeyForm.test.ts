import { describe, expect, it } from "vitest";
import {
  buildLoginApiKeyDefaultModelPreferenceFromSelection,
  normalizeLoginCustomBaseUrl,
  resolveLoginApiKeyDefaultProvider,
  resolveHydratedLoginApiKeyValue,
  resolveLoginApiKeyTemplateId,
  resolveLoginCustomProviderName,
  shouldShowLoginApiKeyLink,
} from "@/login/LoginApiKeyForm.helpers.js";
import { encodeCustomModelValue } from "@/lib/zcodeCustomModelValue.js";

describe("LoginApiKeyForm provider update helpers", () => {
  it("只根据 App 已解析语言决定 API key provider 默认值", () => {
    expect(resolveLoginApiKeyDefaultProvider("zh-CN")).toBe("bigmodel");
    expect(resolveLoginApiKeyDefaultProvider("en-US")).toBe("zai");
  });

  it("按弹窗里的提供方选择写入对应内置 provider id", () => {
    expect(resolveLoginApiKeyTemplateId("zai")).toBe("zai-api");
    expect(resolveLoginApiKeyTemplateId("bigmodel")).toBe("bigmodel-api");
    expect(resolveLoginApiKeyTemplateId("custom")).toBeUndefined();
  });

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

  it("只有输入框为空且 provider 有 URL 时展示获取 API key 链接", () => {
    expect(shouldShowLoginApiKeyLink("", "https://z.ai/manage-apikey/apikey-list")).toBe(true);
    expect(shouldShowLoginApiKeyLink(" sk-login ", "https://z.ai/manage-apikey/apikey-list")).toBe(
      false,
    );
    expect(shouldShowLoginApiKeyLink("", undefined)).toBe(false);
  });

  it("新 Provider Runtime 从 Selection View 选择 API key 登录后的默认模型", () => {
    expect(
      buildLoginApiKeyDefaultModelPreferenceFromSelection(
        {
          revision: 3,
          providers: [
            {
              providerId: "bigmodel-api",
              config: {
                kind: "api",
                models: ["glm-5.3", "glm-5.2"],
              },
              models: [
                { modelId: "glm-5.3", config: {} },
                { modelId: "glm-5.2", config: {} },
              ],
            },
          ],
        },
        "bigmodel-api",
      ),
    ).toBe(encodeCustomModelValue("bigmodel-api", "glm-5.3"));
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
