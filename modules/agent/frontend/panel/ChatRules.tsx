import { t } from "../i18n";
import { useEffect, useState } from "react";
import { TxButton, TxDialog, TxEmpty, TxRow, TxSettingsNote, useTxToast } from "@talyx/sdk";
import type { AgentClient } from "../data/client";
import type { PermissionRule } from "../data/types";

/* What this chat may do beyond its permission setting: the actions you answered "Allow for this chat" to, each
   removable. A stricter permission ends them. */

function reason(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The chat's allowances; reloaded when asked, as when its permission menu opens. */
export function useChatRules(client: AgentClient, conversationId: string | null) {
  const [items, setItems] = useState<PermissionRule[] | null>(null), [error, setError] = useState<string | null>(null);
  const load = () => conversationId ? client.permissionRules(conversationId).then(value => { setItems(value); setError(null); }).catch(failure => setError(reason(failure))) : Promise.resolve();
  useEffect(() => { setItems(null); void load(); }, [client, conversationId]);
  return { items, error, load };
}

export function ChatRules({ client, conversationId, onClose }: { client: AgentClient; conversationId: string; onClose: () => void }) {
  const toast = useTxToast();
  const { items, error, load } = useChatRules(client, conversationId);
  const remove = (rule: PermissionRule) => client.revokePermission(conversationId, rule.id).then(load)
    .catch(failure => toast({ title: t("Couldn't remove it"), detail: reason(failure), tone: "failed" }));
  return <TxDialog open onOpenChange={value => { if (!value) onClose(); }} title={t("Allowed in this chat")} size="standard" footer={<TxButton onClick={onClose}>{t("Done")}</TxButton>}>
    <div className="agent-chat-rules">
      {error && <TxSettingsNote tone="error">{error}</TxSettingsNote>}
      {items && items.length === 0 && <TxEmpty title={t("Nothing extra")} text={t("When you answer “Allow for this chat” on an approval, it shows here.")} />}
      {items && items.length > 0 && <section aria-label={t("Allowed for this chat")}>
        {items.map(rule => {
          const action = rule.action;
          return <TxRow key={rule.id} title={action.split(".").at(-1)?.replaceAll("_", " ")} detail={action.split(".")[0]}>
            <TxButton size="sm" variant="quiet" onClick={() => void remove(rule)}>{t("Remove")}</TxButton>
          </TxRow>;
        })}
      </section>}
    </div>
  </TxDialog>;
}
