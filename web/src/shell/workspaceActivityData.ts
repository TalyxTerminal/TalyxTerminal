import { useEffect, useState } from "react";
import type { Platform } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
type Activity = Platform.Activity;
type OpenRecord = Platform.OpenRecord;

/* What runs for each open workspace, as its modules report it (platform.activity.list), read again whenever that
   changes (platform.activity.changes). */

export type { Activity };
export interface WorkspaceActivities { workspace: OpenRecord; activities: Activity[] }

export async function readWorkspaceActivities(): Promise<WorkspaceActivities[]> {
  const platform = platformRuntime().platform;
  const workspaces = await platform["workspace.list_open"]({});
  return await Promise.all(workspaces.map(async workspace =>
    ({ workspace, activities: (await platform["activity.list"]({ workspaceId: workspace.workspaceId })).activities })));
}

/** Stops what AI asked for in a workspace: the work its modules report as AI's. */
export async function stopWorkspaceAI(workspaceId: string): Promise<void> {
  const { activities } = await platformRuntime().platform["activity.list"]({ workspaceId });
  const [first, ...rest] = activities.filter(item => item.origin === "ai").map(({ module, id }) => ({ module, id }));
  if (first) await platformRuntime().platform["activity.stop"]({ workspaceId, activities: [first, ...rest] });
}

export function useWorkspaceActivities(): WorkspaceActivities[] {
  const [items, setItems] = useState<WorkspaceActivities[]>([]);
  useEffect(() => {
    let alive = true, reading = false, again = false;
    const read = async () => {
      if (reading) { again = true; return; }
      reading = true;
      try {
        do { again = false; const value = await readWorkspaceActivities(); if (alive) setItems(value); } while (again && alive);
      } finally { reading = false; }
    };
    const subscription = platformRuntime().bridge.subscribe("platform.activity.changes", {}, () => { void read(); });
    return () => { alive = false; void subscription.then(value => value.close()); };
  }, []);
  return items;
}
