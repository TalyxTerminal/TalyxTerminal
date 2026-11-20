import { afterEach, describe, expect, it, vi } from "vitest";
import { createAutosave } from "./autosave";

afterEach(() => vi.useRealTimers());

describe("The notebook's autosave", () => {
  it("saves a few seconds after the changes stop, and not before", () => {
    vi.useFakeTimers();
    const save = vi.fn();
    const autosave = createAutosave({ pause: 4000, atMost: 20000, save });
    autosave.touch();
    vi.advanceTimersByTime(3900);
    expect(save).not.toHaveBeenCalled();
    autosave.touch();
    vi.advanceTimersByTime(3900);
    expect(save).not.toHaveBeenCalled();
    vi.advanceTimersByTime(200);
    expect(save).toHaveBeenCalledTimes(1);
  });
  it("saves within the longest wait even while output keeps streaming in", () => {
    vi.useFakeTimers();
    const save = vi.fn();
    const autosave = createAutosave({ pause: 4000, atMost: 20000, save });
    for (let second = 0; second < 25; second++) { autosave.touch(); vi.advanceTimersByTime(1000); }
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls.length).toBe(1);
  });
  it("saves at once when asked, and then waits for nothing", () => {
    vi.useFakeTimers();
    const save = vi.fn();
    const autosave = createAutosave({ pause: 4000, atMost: 20000, save });
    autosave.touch();
    autosave.now();
    expect(save).toHaveBeenCalledTimes(1);
    expect(autosave.waiting()).toBe(false);
    vi.advanceTimersByTime(60000);
    expect(save).toHaveBeenCalledTimes(1);
  });
});
