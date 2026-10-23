/**
 * WelcomeScreen —— 仅通过 API Key 接入模型服务，不提供账号登录。
 */
import type { ReactNode } from "react";
import { DesktopOverlayWindowControls } from "@/DesktopOverlayWindowControls.js";
import { PcbWishLogo } from "@/components/ui/pcbwish-logo.js";
import { useZCodeIntl } from "./i18n/IntlProvider.js";
import { LoginApiKeyForm } from "./login/LoginApiKeyForm.js";
import { ThemeHeroVisual } from "./openWorkspacePageThemeHero.js";

interface WelcomeScreenProps {
  onComplete: (reason: LoginCompleteReason) => void | Promise<void>;
}

export type LoginCompleteReason = "apiKey";

export function WelcomeScreen({ onComplete }: WelcomeScreenProps) {
  return (
    <main className="relative flex h-full min-h-dvh items-center justify-center overflow-hidden bg-background px-4 py-6 text-foreground sm:px-6">
      <ThemeHeroVisual className="absolute inset-0" />
      {/* 原生拖拽命中不受 pointer-events/z-index 约束；拖拽层必须避开窗控，且先于 no-drag 区声明。 */}
      <div className="pointer-events-none absolute left-0 top-0 right-0 z-10 flex h-12 items-center [app-region:drag] platform-windows-desktop:right-[134px] platform-linux-desktop:right-[134px]" />
      <DesktopOverlayWindowControls />
      <section className="relative z-10 w-full flex flex-col gap-10 max-w-sm rounded-2xl border border-popover-border bg-background p-8 text-ui-base/relaxed shadow-md sm:p-10">
        <LoginPanel onComplete={onComplete} />
      </section>
    </main>
  );
}

interface LoginPanelProps {
  onComplete: (reason: LoginCompleteReason) => void | Promise<void>;
}

export function LoginPanel({ onComplete }: LoginPanelProps) {
  const { intl } = useZCodeIntl();

  return (
    <>
      <LoginPanelHeader
        title={intl.formatMessage({ id: "login.title" })}
        description={intl.formatMessage({ id: "login.description" })}
      />

      <div className="space-y-6">
        <LoginApiKeyForm
          onSaved={() => {
            return onComplete("apiKey");
          }}
        />
      </div>
    </>
  );
}

function LoginPanelHeader({ title, description }: { title: string; description: string }) {
  return (
    <header className="flex flex-col items-center gap-3 text-center">
      <LoginPanelLogo />
      <div className="flex flex-col items-center gap-1 text-center">
        <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
        <p className="text-ui-base/relaxed text-foreground-subtle">{description}</p>
      </div>
    </header>
  );
}

function LoginPanelLogo(): ReactNode {
  return (
    // 登录 logo 壳是固定深色底，边框不能跟随浅色主题 token，否则浅色主题下边框过重。
    <div
      className="relative mb-1 flex size-16 items-center justify-center rounded-2xl bg-[linear-gradient(180deg,#000000_0%,#151718_100%)] text-[#ffffff] shadow-lg/20 before:pointer-events-none before:absolute before:inset-0 before:rounded-2xl before:border before:border-[rgba(255,255,255,0.1)]"
      aria-label="PCB Wish"
      role="img"
    >
      <PcbWishLogo className="h-auto w-10" />
    </div>
  );
}
