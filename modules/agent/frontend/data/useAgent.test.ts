import { describe, expect, it, vi } from "vitest";
import type { Subscription } from "@talyx/sdk";
import { subscribeAndLoad } from "./useAgent";

describe("closing a settings view during event registration", () => {
  it("handles a disposed provider and still reports failures on a live view", async () => {
    const load = vi.fn(), error = vi.fn();
    const disposed = new Error("provider_unloaded");
    const start = () => Promise.reject(disposed);
    const stop = subscribeAndLoad(start, load, error);
    stop();
    await Promise.resolve();
    await Promise.resolve();
    expect(load).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
    subscribeAndLoad(start, load, error);
    await vi.waitFor(() => expect(error).toHaveBeenCalledWith(disposed));
  });

  it("closes a late subscription exactly once without loading the removed view", async () => {
    const close = vi.fn(async () => {}), load = vi.fn(), error = vi.fn();
    let finish!: (value: Subscription) => void;
    const stop = subscribeAndLoad(() => new Promise(resolve => { finish = resolve; }), load, error);
    stop();
    finish({ id:"old", close });
    await vi.waitFor(() => expect(close).toHaveBeenCalledTimes(1));
    expect(load).not.toHaveBeenCalled();
    expect(error).not.toHaveBeenCalled();
  });
});
