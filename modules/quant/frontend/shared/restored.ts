import { t } from "../i18n";
import type { ObjectDetail } from "../objects";

export type RestoredObject = ObjectDetail & {restore: {extraFiles: string[]; nonTextFiles: string[]; commonDifferences: string[]}};
export function restoreNotice({restore}: RestoredObject): string {
  const groups: [string, string[]][] = [[t("New files kept"), restore.extraFiles], [t("Non-text files kept"), restore.nonTextFiles], [t("Common files differ"), restore.commonDifferences]];
  return groups.map(([label, names]) => names.length ? `${label}: ${names.join(", ")}` : "").filter(Boolean).join(" · ");
}
