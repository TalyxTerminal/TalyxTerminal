import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const cli = fileURLToPath(new URL("../node_modules/vitest/vitest.mjs", import.meta.url));
const selected = process.argv.slice(2);
// Browser process teardown must not compete with the full logic worker pool.
// Targeted runs retain the ordinary `npm test -- <path/options>` behavior.
const batches = selected.length ? [selected] : [
  ["--exclude", "**/{RegisteredPanelHost,WorkspaceCloseFlow,ExperienceDockview}.test.ts"],
  ["src/kernel/RegisteredPanelHost.test.ts", "src/shell/WorkspaceCloseFlow.test.ts", "src/shell/ExperienceDockview.test.ts", "--no-file-parallelism"],
];
for (const args of batches) {
  const result = spawnSync(process.execPath, [cli, "run", ...args], { stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status === null ? 1 : result.status);
}
