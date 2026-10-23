import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { setDataBaseDir } from "@zcode/services/node";

export function resolveBootstrapSettingsFile(homePath: string = homedir()): string {
  return join(homePath, ".zcode", "v2", "setting.json");
}

/** PCB-Wish fork：agent 数据根与宿主 ZCode（~/.zcode）隔离。 */
export const PCB_WISH_DATA_HOME_DIR = ".pcbwish";

export function resolveDefaultDataBaseDir(
  env: NodeJS.ProcessEnv = process.env,
  homePath: string = homedir(),
): string {
  const configured = env.ZCODE_DATA_BASE_DIR?.trim();
  return configured && configured.length > 0
    ? configured
    : join(homePath, PCB_WISH_DATA_HOME_DIR);
}

export function extractBootstrapDataBaseDir(rawValue: unknown): string | null {
  if (!rawValue || typeof rawValue !== "object") {
    return null;
  }

  const dataBaseDir = (rawValue as { dataBaseDir?: unknown }).dataBaseDir;
  if (typeof dataBaseDir !== "string") {
    return null;
  }

  const trimmed = dataBaseDir.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function readBootstrapDataBaseDirFromDisk(
  settingsFile: string = resolveBootstrapSettingsFile(),
): string | null {
  if (!existsSync(settingsFile)) {
    return null;
  }

  try {
    const raw = readFileSync(settingsFile, "utf-8");
    return extractBootstrapDataBaseDir(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function applyEarlyDataBaseDirBootstrap(): string {
  const defaultDataBaseDir = resolveDefaultDataBaseDir();
  const dataBaseDir =
    readBootstrapDataBaseDirFromDisk(
      resolveBootstrapSettingsFile(defaultDataBaseDir),
    ) ?? defaultDataBaseDir;
  const cliDir = join(dataBaseDir, ".zcode", "cli");
  // 启动早期注入隔离数据根：宿主内服务、agent 子进程与 CLI 共用同一根，
  // 避免 logger / crashReporter 先按宿主 HOME 建目录，也避免继承宿主 ZCode 的会话与记忆。
  process.env.ZCODE_DATA_BASE_DIR = dataBaseDir;
  if (!process.env.ZCODE_STORAGE_DIR?.trim()) {
    process.env.ZCODE_STORAGE_DIR = cliDir;
  }
  if (!process.env.ZCODE_SESSION_DB_PATH?.trim()) {
    process.env.ZCODE_SESSION_DB_PATH = join(cliDir, "db", "db.sqlite");
  }
  setDataBaseDir(dataBaseDir);
  return dataBaseDir;
}
