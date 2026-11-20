/* Chart's status items (platform.status_item) from the workspace stream's health: shown only while something is wrong. */
import type { ModuleContext } from "@talyx/sdk";
import { store } from "./store";
import type { Health } from "./wire";

export function reportHealth(ctx: ModuleContext): () => void {
  const show = (health: Health | null) => {
    const pine = health?.pine;
    if (pine?.state === "restarting") ctx.status.set("indicators", { level: "warn", text: "Indicators are restarting.", tooltip: "Restart" });
    else if (pine?.state === "down") ctx.status.set("indicators", { level: "error", text: `Indicators stopped: ${pine.reason}`, tooltip: "Restart" });
    else ctx.status.set("indicators", { level: "ok", visible: false });
    const alertStore = health?.alertStore;
    if (alertStore?.state === "failing") ctx.status.set("alert_store", { level: "error", text: `Alerts couldn't be saved: ${alertStore.reason}` });
    else ctx.status.set("alert_store", { level: "ok", visible: false });
  };
  show(store.getState().health);
  return store.subscribe((state, previous) => { if (state.health !== previous.health) show(state.health); });
}
