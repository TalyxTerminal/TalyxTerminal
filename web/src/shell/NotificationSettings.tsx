import { intlLocale } from "@talyx/sdk";
import { t } from "./i18n";
import { useCallback, useEffect, useState } from "react";
import {
  ModuleView, TxButton, TxDialog, TxEmpty, TxField, TxIcon, TxIconButton, TxInput, TxMenu, TxRow, TxSettingsGroup, TxSettingsNote, TxSettingsPage,
  TxSwitch, TxTooltip, type Json,
} from "@talyx/sdk";
import type { Platform } from "@talyx/sdk";
import { platformRuntime } from "../kernel/runtime";
type Channel = Platform.Channel;
type ChannelDelete = Platform.ChannelDelete;
type Delivery = Platform.ChannelDelivery;
type ChannelSave = Platform.ChannelSave;
type PlatformTypes = Platform.ContractTypes;
type Deliveries = Platform.Delivery;

type Kind = Channel["kind"];

const kinds = (): Record<Kind, { name: string; help: string }> => ({
  telegram: { name: "Telegram", help: t("Create a bot with @BotFather and send it any message. The chat ID is your own Telegram user ID or a group's ID.") },
  feishu: { name: t("Feishu"), help: t("Add a custom bot to a Feishu group and copy its webhook. Fill in the signing secret if the bot has one.") },
  discord: { name: "Discord", help: t("In a channel's settings, open Integrations › Webhooks and copy the webhook URL.") },
});

const statusLabels = (): Record<Delivery["status"], string> => ({
  pending: t("Waiting to send"), sending: t("Sending"), sent: t("Sent"), failed: t("Failed"), unknown: t("Not confirmed"), cancelled: t("Cancelled"),
});

function errorText(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

function since(iso: string): string {
  const seconds = (Date.now() - Date.parse(iso)) / 1000;
  if (seconds < 60) return t("just now");
  if (seconds < 3600) return t("{minutes} min ago", { minutes: Math.floor(seconds / 60) });
  if (seconds < 86400) return t("{hours} h ago", { hours: Math.floor(seconds / 3600) });
  return new Date(iso).toLocaleDateString(intlLocale(), { month: "short", day: "numeric" });
}

function channelDetail(channel: Channel): string {
  const parts = channel.name === kinds()[channel.kind].name ? [] : [kinds()[channel.kind].name];
  if (channel.settings.chatId) parts.push(t("chat {chatId}", { chatId: channel.settings.chatId }));
  if (channel.credentialMask) parts.push(channel.kind === "telegram" ? t("token {credentialMask}", { credentialMask: channel.credentialMask }) : t("webhook {credentialMask}", { credentialMask: channel.credentialMask }));
  const last = channel.lastDelivery;
  if (last) parts.push(t("last: {status} {time}", { status: (statusLabels()[last.status as Delivery["status"]] ?? last.status).toLowerCase(), time: since(last.updatedAt) }));
  return parts.join(" · ");
}

/** Settings › Notifications: the phone channels on top, each module's "when to notify" below. */
export function NotificationsSection() {
  const runtime = platformRuntime(), kernel = runtime.kernel;
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ kind: Kind; channel: Channel | null } | null>(null);
  const [busy, setBusy] = useState<string | null>(null), [testing, setTesting] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, { tone: "muted" | "error"; text: string }>>({});
  const call = useCallback(<T,>(method: string, input: object) => runtime.bridge.call(`platform.notifications.${method}`, input as Json) as unknown as Promise<T>, [runtime]);

  const refresh = useCallback(async () => {
    try { setChannels(await call<Channel[]>("channels", {})); setLoadError(null); }
    catch (reason) { setLoadError(errorText(reason)); }
  }, [call]);
  useEffect(() => { void refresh(); }, [refresh]);

  const note = (id: string, value: { tone: "muted" | "error"; text: string } | null) =>
    setNotes(current => { const next = { ...current }; if (value) next[id] = value; else delete next[id]; return next; });

  const save = async (channel: Channel, enabled: boolean) => {
    setBusy(channel.id); note(channel.id, null);
    try {
      setChannels(await call<Channel[]>("configure", { operation: "save", channelId: channel.id, expectedRevision: channel.revision,
        kind: channel.kind, name: channel.name, enabled, settings: channel.settings } satisfies ChannelSave | ChannelDelete));
    } catch (reason) { note(channel.id, { tone: "error", text: errorText(reason) }); await refresh(); }
    finally { setBusy(null); }
  };
  const remove = async (channel: Channel) => {
    setBusy(channel.id); note(channel.id, null);
    try { setChannels(await call<Channel[]>("configure", { operation: "delete", channelId: channel.id, expectedRevision: channel.revision })); }
    catch (reason) { note(channel.id, { tone: "error", text: errorText(reason) }); await refresh(); }
    finally { setBusy(null); }
  };
  const test = async (channel: Channel) => {
    setBusy(channel.id); setTesting(channel.id); note(channel.id, null);
    try {
      const result = await call<Deliveries>("test", { channelId: channel.id });
      const delivery = result.deliveries[0];
      note(channel.id, delivery.status === "sent" ? { tone: "muted", text: t("Test sent. Check your phone.") }
        : delivery.status === "failed" ? { tone: "error", text: delivery.error ? t("Test failed: {error}", { error: delivery.error }) : t("Test failed.") }
        : { tone: "error", text: t("{value}: the service didn't confirm the test{value2}.", { value: statusLabels()[delivery.status], value2: delivery.error ? ` (${delivery.error})` : "" }) });
      await refresh();
    } catch (reason) { note(channel.id, { tone: "error", text: errorText(reason) }); }
    finally { setBusy(null); setTesting(null); }
  };

  const sections = kernel.contributions<PlatformTypes["point:notification_section"]>("platform.notification_section")
    .map(item => ({ moduleId: item.moduleId, key: `${item.moduleId}.${item.id}`, view: `${item.moduleId}.${item.descriptor.view}` }));

  return <TxSettingsPage>
    <TxSettingsGroup title={t("Phone channels")} action={<TxMenu label={t("Add a channel")} align="end" trigger={<TxButton size="sm"><TxIcon name="plus" />{t("Add channel")}</TxButton>}
      items={(Object.keys(kinds()) as Kind[]).map(kind => ({ id: kind, label: kinds()[kind].name, onSelect: () => setEditing({ kind, channel: null }) }))} />}>
      {loadError && <TxSettingsNote tone="error">{t("Couldn't read channels.")} {loadError}</TxSettingsNote>}
      {!loadError && !channels && <TxSettingsNote>{t("Loading…")}</TxSettingsNote>}
      {channels?.length === 0 && <TxEmpty title={t("No phone channels")} />}
      {channels?.map(channel => <div key={channel.id}>
        <TxRow title={channel.name} tone={channel.enabled ? "default" : "off"}
          // A test's own result shows under the row, so the last failure isn't repeated beside it.
          detail={channel.lastDelivery?.status === "failed" && !notes[channel.id] ? <>{channelDetail(channel)} · <span className="tx-error">{channel.lastDelivery.error}</span></> : channelDetail(channel)}>
          <div className="tx-row tx-nowrap">
            <TxButton size="sm" disabled={busy !== null || !channel.enabled} onClick={() => void test(channel)}>{testing === channel.id ? t("Sending…") : t("Send test")}</TxButton>
            <TxSwitch hideLabel label={t("{name} on", { name: channel.name })} checked={channel.enabled} disabled={busy !== null} onCheckedChange={next => void save(channel, next)} />
            <TxMenu label={t("{name} actions", { name: channel.name })} align="end" trigger={<TxIconButton size="sm" label={t("{name} actions", { name: channel.name })} icon="more" disabled={busy !== null} />} items={[
              { id: "edit", label: t("Edit…"), onSelect: () => setEditing({ kind: channel.kind, channel }) },
              { id: "remove", label: t("Remove channel"), tone: "danger", onSelect: () => void remove(channel) },
            ]} />
          </div>
        </TxRow>
        {notes[channel.id] && <TxSettingsNote tone={notes[channel.id].tone}>{notes[channel.id].text}</TxSettingsNote>}
      </div>)}
    </TxSettingsGroup>
    {sections.map(section => {
      const ctx = kernel.modules.get(section.moduleId)?.ctx;
      return ctx && <ModuleView key={section.key} id={section.view} props={{} as Json} context={ctx} />;
    })}
    {editing && <ChannelDialog kind={editing.kind} channel={editing.channel} call={call}
      onClose={() => setEditing(null)} onSaved={next => { setChannels(next); setEditing(null); }} />}
  </TxSettingsPage>;
}

function ChannelDialog({ kind, channel, call, onClose, onSaved }: {
  kind: Kind; channel: Channel | null;
  call: <T>(method: string, input: object) => Promise<T>;
  onClose: () => void; onSaved: (channels: Channel[]) => void;
}) {
  const [name, setName] = useState(channel?.name ?? kinds()[kind].name);
  const [chatId, setChatId] = useState(channel?.settings.chatId ?? "");
  const [token, setToken] = useState(""), [webhook, setWebhook] = useState(""), [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false), [error, setError] = useState<string | null>(null);
  const editing = channel !== null;
  // Credentials are replaced whole: leaving them empty while editing keeps the saved ones.
  const secretOnly = kind === "feishu" && secret.trim() !== "" && webhook.trim() === "";
  const credentials = kind === "telegram" ? (token.trim() ? { token: token.trim() } : null)
    : webhook.trim() ? { webhook: webhook.trim(), ...(kind === "feishu" && secret.trim() ? { secret: secret.trim() } : {}) } : null;
  const ready = name.trim() !== "" && (kind !== "telegram" || chatId.trim() !== "") && (editing || credentials !== null) && !secretOnly;
  // Editing leaves a credential empty to keep the saved one; the field says so in its placeholder.
  const kept = channel?.credentialMask ? t("Saved, ending {credentialMask}", { credentialMask: channel.credentialMask }) : undefined;

  const save = async () => {
    setSaving(true); setError(null);
    try {
      onSaved(await call<Channel[]>("configure", {
        operation: "save", channelId: channel?.id ?? null, expectedRevision: channel?.revision ?? null, kind, name: name.trim(),
        enabled: channel?.enabled ?? true, settings: kind === "telegram" ? { chatId: chatId.trim() } : {}, ...(credentials ? { credentials } : {}),
      } satisfies ChannelSave | ChannelDelete));
    } catch (reason) { setError(errorText(reason)); }
    finally { setSaving(false); }
  };

  return <TxDialog open onOpenChange={open => { if (!open) onClose(); }} title={editing ? t("Edit {name}", { name: channel.name }) : t("Add {value}", { value: kinds()[kind].name })} size="standard" footer={<>
    <TxButton variant="quiet" onClick={onClose}>{t("Cancel")}</TxButton>
    <TxButton variant="primary" disabled={!ready || saving} onClick={() => void save()}>{saving ? t("Saving…") : t("Save")}</TxButton>
  </>}>
    <div className="talyx-settings-form">
      <TxTooltip label={kinds()[kind].help}><span className="talyx-form-guide" tabIndex={0}><TxIcon name="info" />{t("Where to find these")}</span></TxTooltip>
      <TxField label={t("Name")}><TxInput value={name} onChange={event => setName(event.target.value)} /></TxField>
      {kind === "telegram" && <>
        <TxField label={t("Bot token")}>
          <TxInput type="password" autoComplete="off" value={token} placeholder={kept} onChange={event => setToken(event.target.value)} />
        </TxField>
        <TxField label={t("Chat ID")}><TxInput inputMode="numeric" value={chatId} placeholder="123456789" onChange={event => setChatId(event.target.value)} /></TxField>
      </>}
      {kind !== "telegram" && <TxField label={t("Webhook URL")}>
        <TxInput type="password" autoComplete="off" value={webhook} placeholder={kept} onChange={event => setWebhook(event.target.value)} />
      </TxField>}
      {kind === "feishu" && <TxField label={t("Signing secret (optional)")} error={secretOnly ? t("Enter the webhook again to change the secret.") : undefined}>
        <TxInput type="password" autoComplete="off" value={secret} onChange={event => setSecret(event.target.value)} />
      </TxField>}
      {error && <p className="tx-error" role="alert">{error}</p>}
    </div>
  </TxDialog>;
}
