import { TxIcon } from "@talyx/sdk";
import type { Wake } from "./model";

/* What woke a run, in words and as an icon: on the line above the run in the
   stream (after Grok Bot's centred lines) and in Runs. A module event shows
   the name the module declares for it, then the routine. */

const ICON: Record<Wake["kind"], Parameters<typeof TxIcon>[0]["name"]> = {
  you: "user", mention: "message", schedule: "clock", event: "bolt", catchup: "history", resumed: "restore",
};

export function wakeText(wake: Wake): string {
  switch (wake.kind) {
    case "you": return "You";
    case "mention": return `Mentioned by ${wake.name}`;
    case "schedule": return wake.routine;
    case "event": return `${wake.event} · ${wake.routine}`;
    case "catchup": return `Catch-up: ${wake.routine}, due ${wake.due}`;
    case "resumed": return `Resumed: stopped at ${wake.stopped}`;
  }
}

export function WakeIcon({ wake }: { wake: Wake }) {
  return <TxIcon name={ICON[wake.kind]} />;
}
