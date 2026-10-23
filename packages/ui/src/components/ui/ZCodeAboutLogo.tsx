import { cn } from "@/components/lib/utils.js";
import { PcbWishLogo } from "@/components/ui/pcbwish-logo.js";

export function ZCodeAboutLogo({ className }: { className?: string }) {
  return <PcbWishLogo className={className} />;
}

export function ZCodeWordmarkLogo({ className }: { className?: string }) {
  return (
    <svg
      width="210"
      height="60"
      viewBox="0 0 210 60"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("shrink-0 text-current", className)}
      role="img"
      aria-label="PCB Wish"
    >
      <text
        x="0"
        y="46"
        fill="currentColor"
        fontFamily="'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif"
        fontSize="44"
        fontWeight="700"
        letterSpacing="0.5"
      >
        PCB Wish
      </text>
    </svg>
  );
}
