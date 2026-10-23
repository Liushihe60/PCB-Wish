import type { ReactNode } from "react";
import { cn } from "@/components/lib/utils.js";
import { PcbWishMark } from "@/components/ui/pcbwish-logo.js";

interface RootStartupLoadingProps {
  label: string;
  children?: ReactNode;
  busy?: boolean;
}

export function RootStartupLoading({ label, children, busy = true }: RootStartupLoadingProps) {
  return (
    <div
      // Web 端全局 html/body/#root 为 Electron 透明背景让路，React 接管后会替换 HTML 启动壳。
      // 这里必须由阻塞态自身承接主题背景，否则远控链接会在 Root 恢复期间继续露出浏览器白底。
      className="flex h-full min-h-dvh flex-col items-center justify-center gap-6 bg-background text-foreground"
      role="status"
      aria-busy={busy}
      aria-label={label}
      data-testid="root-startup-loading"
    >
      <ZCodeStartupLogoBadge />
      {children}
    </div>
  );
}

/** 初始化与引导共用品牌图标，保持底色、描边、圆角和标志比例一致。 */
export function ZCodeStartupLogoBadge({
  animated = true,
  size = "default",
}: {
  animated?: boolean;
  size?: "default" | "titlebar";
}) {
  return (
    <div
      data-desktop-logo-badge={size === "titlebar" ? "true" : undefined}
      className={cn(
        "relative flex items-center justify-center bg-[#6B7E72] before:pointer-events-none before:absolute before:inset-0 before:rounded-[inherit] before:border before:border-[rgba(255,255,255,0.12)] before:content-['']",
        size === "titlebar"
          ? "size-5 rounded shadow-sm/20 before:border-white/20"
          : "size-24 rounded-3xl shadow-xl/20",
      )}
    >
      <PcbWishMark className={size === "titlebar" ? "size-3.5" : "size-16"} />
    </div>
  );
}
