import { useMemo } from "react";
import type { IClientScenesService } from "@zcode/services";
import type { DraftSuggestedPromptItem } from "@/v4/draftSuggestedPromptItems.js";
import { PCB_WISH_DRAFT_SUGGESTED_PROMPTS } from "@/v4/pcbWishSuggestedPrompts.js";

interface UseDraftSuggestedPromptItemsOptions {
  clientScenesService: IClientScenesService;
  rpcReady: boolean;
  workspaceKey: string;
}

/**
 * PCB-Wish fork：首页草稿推荐使用本地硬件领域语料。
 * 保留原签名以兼容上层容器；暂不消费远端 Client Scenes，保证离线与首次启动一致。
 */
export function useDraftSuggestedPromptItems(
  _options: UseDraftSuggestedPromptItemsOptions,
): DraftSuggestedPromptItem[] {
  return useMemo(() => PCB_WISH_DRAFT_SUGGESTED_PROMPTS, []);
}
