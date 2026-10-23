import { homedir } from "node:os";

/**
 * PCB-Wish fork：agent 数据根与宿主 ZCode 隔离。
 * 桌面启动时注入 ZCODE_DATA_BASE_DIR（~/.pcbwish）；独立用 CLI 时保持原有 homedir 行为。
 */
export function resolveAgentDataHome(env: NodeJS.ProcessEnv = process.env): string {
  const configured = env.ZCODE_DATA_BASE_DIR?.trim();
  return configured && configured.length > 0 ? configured : homedir();
}
