import type { ObjectDetail } from "../objects";

export type RestoredObject = ObjectDetail & {restore: {extraFiles: string[]; nonTextFiles: string[]; commonDifferences: string[]}};
export function restoreNotice({restore}: RestoredObject): string {
  const groups: [string, string[]][] = [["New files kept", restore.extraFiles], ["Non-text files kept", restore.nonTextFiles], ["Common files differ", restore.commonDifferences]];
  return groups.map(([label, names]) => names.length ? `${label}: ${names.join(", ")}` : "").filter(Boolean).join(" · ");
}
