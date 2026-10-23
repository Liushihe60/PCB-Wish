import { cn } from "@/components/lib/utils.js";

/**
 * PCBWish 品牌标识：鼠尾草绿圆角板 + 铜色 45° 走线、两枚过孔与一颗愿星。
 * PcbWishMark 只含板内元素（供徽标壳复用，壳即板）；PcbWishLogo 自带板底。
 */
export function PcbWishMark({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="114 116 284 284" fill="none"
      className={cn("shrink-0", className)} aria-hidden="true" focusable="false">
      <circle cx="160" cy="160" r="11" fill="none" stroke="#C3A184" strokeWidth="9"/><circle cx="352" cy="352" r="11" fill="none" stroke="#C3A184" strokeWidth="9"/><path d="M176 348H232L288 264" fill="none" stroke="#C3A184" strokeWidth="16" strokeLinecap="round" strokeLinejoin="round"/><circle cx="176" cy="348" r="18" fill="#C3A184"/><path d="M300 144Q314 186 356 200Q314 214 300 256Q286 214 244 200Q286 186 300 144Z" fill="#EFEAE0"/>
    </svg>
  );
}

export function PcbWishLogo({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" fill="none"
      className={cn("shrink-0", className)} aria-hidden="true" focusable="false">
      <rect x="96" y="96" width="320" height="320" rx="42" fill="#6B7E72"/>
      <circle cx="160" cy="160" r="11" fill="none" stroke="#C3A184" strokeWidth="9"/><circle cx="352" cy="352" r="11" fill="none" stroke="#C3A184" strokeWidth="9"/><path d="M176 348H232L288 264" fill="none" stroke="#C3A184" strokeWidth="16" strokeLinecap="round" strokeLinejoin="round"/><circle cx="176" cy="348" r="18" fill="#C3A184"/><path d="M300 144Q314 186 356 200Q314 214 300 256Q286 214 244 200Q286 186 300 144Z" fill="#EFEAE0"/>
    </svg>
  );
}
