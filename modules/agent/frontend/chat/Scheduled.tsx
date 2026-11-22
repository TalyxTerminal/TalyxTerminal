import { t } from "../i18n";
import { TxButton, TxIcon, TxSwitch } from "@talyx/sdk";
import type { Schedule } from "./model";

/* Every chat's schedule in one list, opened from "Scheduled" at the top of
   the chat list Scheduled page: name, when in words
   with its time zone, the chat it runs in, the next run, on or paused. A row
   opens its chat; the switch pauses it. */

export function ScheduledPage({ schedules, titles, onToggle, onOpen }: {
  schedules: Schedule[]; titles: Record<string, string>; onToggle(id: string, on: boolean): void; onOpen(chat: string): void;
}) {
  return <div className="chat-stream"><div className="chat-column chat-schedules">
    {schedules.length === 0 && <p className="chat-list-none">{t("No scheduled chats")}</p>}
    {schedules.map(schedule => <div key={schedule.id} className="chat-schedule" data-off={!schedule.on || undefined}>
      <TxButton variant="row" className="chat-schedule-open" onClick={() => onOpen(schedule.chat)}>
        <TxIcon name="clock" className="chat-schedule-icon" />
        <span className="chat-schedule-copy">
          <span className="chat-schedule-name">{schedule.name}</span>
          <span className="chat-schedule-sub">{schedule.when} · {titles[schedule.chat]}</span>
        </span>
        <span className="chat-schedule-next"><span className="chat-schedule-sub">{schedule.on ? t("Next run") : t("Status")}</span><span>{schedule.on ? schedule.next : t("Paused")}</span></span>
      </TxButton>
      <TxSwitch hideLabel label={t("{name} on", { name: schedule.name })} checked={schedule.on} onCheckedChange={on => onToggle(schedule.id, on)} />
    </div>)}
  </div></div>;
}
