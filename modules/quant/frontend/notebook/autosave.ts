/** When the notebook writes itself to disk: shortly after the changes stop (`pause`), never later than `atMost` after the first unsaved change (an output that keeps streaming
 * would otherwise keep pushing the pause back), and at once when asked (before a run, so a crash cannot take the code that was about to run). */
export function createAutosave({ pause, atMost, save }: { pause: number; atMost: number; save(): void }) {
  let quiet: ReturnType<typeof setTimeout> | null = null;
  let latest: ReturnType<typeof setTimeout> | null = null;
  const clear = () => { for (const timer of [quiet, latest]) if (timer !== null) clearTimeout(timer); quiet = latest = null; };
  const fire = () => { clear(); save(); };
  return {
    /** Something changed and is not saved yet. */
    touch() {
      if (quiet !== null) clearTimeout(quiet);
      quiet = setTimeout(fire, pause);
      if (latest === null) latest = setTimeout(fire, atMost);
    },
    /** Save now (and forget the waiting). */
    now: fire,
    cancel: clear,
    waiting: () => quiet !== null,
  };
}
