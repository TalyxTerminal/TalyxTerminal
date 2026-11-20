import { useCallback, useEffect, useState } from "react";

type Switchable = {versionId: string; auto: boolean};

/** Auto-update switches that follow the click: the value asked for is shown until a read says the same, or until the change failed (then the read
 *  shows). Reads that answer out of order (the list and the item both reload on every library change) cannot flip it back meanwhile. */
export function useWantedAuto(read: Switchable[]) {
  const [wanted, setWanted] = useState<Record<string, boolean>>({});
  const said = read.map(item => `${item.versionId}:${item.auto}`).join(",");
  useEffect(() => setWanted(current => {
    const left = Object.entries(current).filter(([id, on]) => read.find(item => item.versionId === id)?.auto !== on);
    return left.length === Object.keys(current).length ? current : Object.fromEntries(left);
  }), [said]);
  const ask = (versionId: string, on: boolean, send: () => Promise<unknown>) => {
    setWanted(current => ({...current, [versionId]: on}));
    return send().catch(reason => {
      setWanted(current => Object.fromEntries(Object.entries(current).filter(([id]) => id !== versionId)));
      throw reason;
    });
  };
  const apply = useCallback(<T extends Switchable>(list: T[]): T[] => Object.keys(wanted).length === 0 ? list
    : list.map(item => item.versionId in wanted ? {...item, auto: wanted[item.versionId]} : item), [wanted]);
  return {ask, apply};
}
