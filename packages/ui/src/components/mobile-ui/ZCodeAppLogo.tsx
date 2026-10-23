import type { ZCodeAppLogoProps } from "@/components/mobile-ui/contract.js";
import { cn } from "@/components/lib/utils.js";
import { PcbWishLogo } from "@/components/ui/pcbwish-logo.js";

/**
 * 应用 logo（鼠尾草绿板 + 铜色走线），自带底色，深浅主题通用。
 * 装饰性图片：调用方负责在旁边给出可读的品牌文字，避免屏幕阅读器重复朗读。
 */
export function ZCodeAppLogo({ className }: ZCodeAppLogoProps) {
  return (
    <span
      data-app-logo="pcbwish"
      aria-hidden="true"
      className={cn("inline-flex shrink-0 select-none items-center justify-center", className)}
    >
      <PcbWishLogo className="size-full" />
    </span>
  );
}
