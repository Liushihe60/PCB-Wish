import { join } from "node:path";
import { resolveAgentDataHome } from "../../data-home.js";

export function getDefaultSessionDbPath(): string {
  return join(resolveAgentDataHome(), ".zcode", "cli", "db", "db.sqlite");
}
