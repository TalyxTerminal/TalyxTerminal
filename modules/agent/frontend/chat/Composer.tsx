import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { TxButton, TxIcon, TxIconButton, TxMenu, TxPopover, TxSegmented, TxSwitch, TxTooltip, useTxToast } from "@talyx/sdk";
import type { Choices, Command, FileChoice, ModelChoice, Permission } from "./model";

/* The message box, in the chat panel: what goes with the message in a
   strip on top of the box, the chat's choices in one row under the text. Enter sends (while
   it works, Enter adds to the running task and Tab queues for after). */

const PERMISSIONS: { id: Permission; label: string; detail: string }[] = [
  { id: "read_only", label: "Read only", detail: "Asks before changing, deleting, running code or sending anything" },
  { id: "auto", label: "Auto", detail: "Asks before deleting something you own" },
  { id: "full", label: "Full access", detail: "Never asks" },
];

/** Someone "@" can name instead of a file: a bot in a group. */
export interface Mention { id: string; name: string; detail: string; lead: ReactNode }

export interface ComposerProps {
  placeholder?: string;
  /** Shown instead of any placeholder: a question card is waiting, and a message skips it. */
  notice?: string;
  /** Text put in the box from outside (a routine's "+" or Edit); `n` changes on every put. */
  prefill?: { text: string; n: number };
  /** Something more in the strip over the box: what the message replies to. */
  extra?: ReactNode;
  /** "@" lists these instead of files. */
  mentions?: Mention[];
  /** Only "+" and send, like Grok Bot's box: a bot's model and permission live in its settings. */
  bare?: boolean;
  choices: Choices;
  /** Tokens in the chat's context and the model's window, for the ring. */
  context?: { used: number; window: number };
  models: ModelChoice[];
  commands: Command[];
  findFiles(query: string, signal: AbortSignal): Promise<FileChoice[]>;
  view: string | null;
  running: boolean;
  queued: string[];
  allowedCount: number;
  onAllowed?(): void;
  onChoices(changes: Partial<Choices>): void;
  /** Rejects when the message did not go out: the box gets its text and files back. */
  onSend(text: string, includeView?: boolean, paths?: string[], uploads?: File[]): Promise<unknown>;
  onQueue(text: string): void;
  onQueued(queued: string[]): void;
  /** A queued message sent into the running turn now. */
  onSteer?(text: string): void;
  onStop(): void;
  onManageModels(): void;
}

export function Composer(props: ComposerProps) {
  const { choices, models, running, queued } = props;
  const [text, setText] = useState("");
  const [viewOn, setViewOn] = useState(true);
  const upload = useRef<HTMLInputElement>(null);
  const [uploads,setUploads] = useState<File[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [pick, setPick] = useState(0);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const element = input.current!;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [text]);
  useEffect(() => {
    if (!props.prefill) return;
    setText(props.prefill.text);
    const element = input.current!;
    requestAnimationFrame(() => { element.focus(); element.setSelectionRange(element.value.length, element.value.length); });
  }, [props.prefill?.n]);

  const toast = useTxToast();
  const [fileChoices, setFileChoices] = useState<FileChoice[]>([]);
  // "/" at the start lists commands and skills; "@" before a word lists files.
  const slash = /^\/([\w-]*)$/.exec(text);
  const at = /(?:^|\s)@([^\s@]*)$/.exec(text);
  const fileQuery = props.mentions ? undefined : at?.[1];
  useEffect(() => {
    setFileChoices([]);
    if (fileQuery === undefined) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      void props.findFiles(fileQuery, controller.signal).then(items => {
        if (!controller.signal.aborted) setFileChoices(items);
      }).catch(error => { if (!controller.signal.aborted) toast({title:String(error),tone:"failed"}); });
    }, 150);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [fileQuery, props.findFiles, toast]);
  // the chat panel's slash menu: an icon per row, commands then skills under their own headings.
  const suggestions: { key: string; label: string; detail: string; icon: Parameters<typeof TxIcon>[0]["name"] | null; lead?: ReactNode; group?: string; apply: () => void }[] = slash
    ? (["command", "skill"] as const).flatMap(kind => props.commands.filter(command => command.kind === kind && command.name.startsWith(slash[1])).map((command, index) => ({
      key: command.name, label: command.name, detail: command.detail, icon: command.icon, group: index === 0 ? (kind === "command" ? "Commands" : "Skills") : undefined,
      apply: () => { if (command.name === "plan") { props.onChoices({ plan: true }); setText(""); } else setText(`/${command.name} `); } })))
    : at && props.mentions ? props.mentions.filter(mention => mention.name.toLowerCase().includes(at[1].toLowerCase())).map(mention => ({ key: mention.id, label: mention.name, detail: mention.detail,
      icon: null, lead: mention.lead, apply: () => setText(`${text.slice(0, text.length - at[1].length)}${mention.name} `) }))
    : at ? fileChoices.filter(file => file.path.toLowerCase().includes(at[1].toLowerCase())).map(file => ({ key: file.path, label: file.path, detail: file.place === "knowledge" ? "Knowledge" : "Workspace",
      icon: (file.place === "knowledge" ? "book" : "file") as "book" | "file", apply: () => { setText(text.slice(0, text.length - at[1].length - 1)); setFiles(current => [...current, file.path]); } }))
    : [];
  useEffect(() => setPick(0), [slash?.[1], at?.[1]]);

  const ready = text.trim() !== "";
  const send = () => {
    if (!ready) return;
    const sent = { text, files, uploads };
    setUploads([]);
    setText(""); setFiles([]);
    props.onSend(text.trim(), viewOn, files, uploads).catch(() => {
      // Nothing typed since: put the message back as it was, so no words are lost.
      setText(now => now || sent.text); setFiles(now => now.length ? now : sent.files); setUploads(now => now.length ? now : sent.uploads);
    });
  };


  return <div className="chat-composer">
    <input hidden ref={upload} type="file" multiple onChange={event => {
      setUploads(items => [...items, ...Array.from(event.target.files ?? [])]); event.target.value="";
    }} />
    {queued.length > 0 && <div className="chat-queue">{queued.map((item, index) => <div key={index} className="chat-queued">
      <span className="chat-queued-text">{item}</span>
      {/* the chat panel's Steer: into the running turn now instead of after it */}
      {props.onSteer && <TxButton size="sm" variant="quiet" className="chat-steer" onClick={() => { props.onSteer!(item); props.onQueued(queued.filter((_, at) => at !== index)); }}>
        <TxIcon name="arrow-up" />Send now</TxButton>}
      <TxIconButton size="sm" icon="edit" label="Edit" onClick={() => { setText(item); props.onQueued(queued.filter((_, at) => at !== index)); input.current!.focus(); }} />
      <TxIconButton size="sm" icon="trash" label="Remove" onClick={() => props.onQueued(queued.filter((_, at) => at !== index))} />
    </div>)}</div>}
    {(props.view || files.length > 0 || uploads.length > 0 || props.extra) && <div className="chat-strip">
      {props.extra}
      {uploads.map((file,index) => <span key={index} className="chat-strip-item"><TxIcon name="file"/>{file.name}
        <TxIconButton size="sm" icon="close" label="Remove attachment" onClick={()=>setUploads(items=>items.filter((_,i)=>i!==index))}/></span>)}
      {props.view && <span className="chat-strip-item" data-off={!viewOn || undefined}>
        <TxIcon name="chart" /><span>{props.view}</span>
        <TxIconButton size="sm" icon={viewOn ? "close" : "plus"} label={viewOn ? "Don't send with this message" : "Send with this message"} onClick={() => setViewOn(!viewOn)} />
      </span>}
      {files.map(file => <span key={file} className="chat-strip-item"><TxIcon name="file" />{file}
        <TxIconButton size="sm" icon="close" label="Remove" onClick={() => setFiles(files.filter(item => item !== file))} /></span>)}
    </div>}
    <div className="chat-box" onPointerDown={event => { if (event.target === event.currentTarget) input.current!.focus(); }}>
      {suggestions.length > 0 && <div className="chat-suggest" role="listbox">{suggestions.slice(0, 9).map((item, index) => <Fragment key={item.key}>
        {item.group && <div className="chat-suggest-group" role="presentation">{item.group}</div>}
        <TxButton variant="row" size="sm" role="option" aria-selected={index === pick} className="chat-suggestion"
          onMouseDown={event => event.preventDefault()} onClick={item.apply}>
          {item.lead}{item.icon && <TxIcon name={item.icon} />}<span className="chat-suggestion-label">{item.label}</span><span className="chat-meta">{item.detail}</span>
        </TxButton></Fragment>)}</div>}
      <textarea ref={input} className="chat-input" rows={1} value={text} aria-label="Message"
        placeholder={props.notice ?? (running ? "Add to this task" : props.placeholder ?? "Ask anything · @ files · / commands")}
        onChange={event => setText(event.target.value)}
        onKeyDown={event => {
          if (suggestions.length && (event.key === "ArrowDown" || event.key === "ArrowUp")) { event.preventDefault(); setPick(index => (index + (event.key === "ArrowDown" ? 1 : suggestions.length - 1)) % suggestions.length); return; }
          if (suggestions.length && (event.key === "Tab" || event.key === "Enter")) { event.preventDefault(); suggestions[pick].apply(); return; }
          if (event.key === "Tab" && running && ready) { event.preventDefault(); props.onQueue(text.trim()); setText(""); return; }
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(); }
        }} />
      <div className="chat-tools">
        <TxMenu label="Add" items={[
          { id: "files", label: "Files or images…", icon: "paperclip", onSelect: () => upload.current?.click() },
          ...props.bare ? [] : [{ id: "plan", label: "Plan mode", icon: "list" as const, checked: choices.plan, onSelect: () => props.onChoices({ plan: !choices.plan }) }],
        ]} trigger={<TxIconButton size="sm" variant="tool" icon="plus" label="Add" />} />
        {!props.bare && <PermissionButton value={choices.permission} onChange={permission => props.onChoices({ permission })}
          allowed={props.onAllowed ? { label: "Always allowed in this chat…", count: props.allowedCount, open: props.onAllowed } : undefined} />}
        {choices.plan && !props.bare && <TxButton size="sm" variant="tool" aria-pressed="true" className="chat-tool" onClick={() => props.onChoices({ plan: false })}><TxIcon name="list" />Plan</TxButton>}
        <span className="chat-spacer" />
        {props.context && !props.bare && <ContextRing used={props.context.used} window={props.context.window} />}
        {/* Model and thinking in one button "GPT-5.6 Sol Medium ⌄": the models, the levels and the subagent switch in one popover. */}
        {!props.bare && <ModelButton models={models} choices={choices} onChoices={props.onChoices} onManageModels={props.onManageModels} />}
        {running && !ready
          ? <TxIconButton size="sm" variant="outline" icon="stop" label="Stop" onClick={props.onStop} />
          : <TxButton size="sm" variant="primary" className="chat-send" disabled={!ready} aria-label="Send" onClick={send}><TxIcon name="arrow-up" /></TxButton>}
      </div>
    </div>
  </div>;
}

/** The permission button and menu of the message box; a bot's settings use the same one. `allowed`: the way to the list
    of things always allowed, at the menu's foot. */
export function PermissionButton({ value, onChange, allowed }: { value: Permission; onChange(permission: Permission): void; allowed?: { label: string; count: number; open(): void } }) {
  const permission = PERMISSIONS.find(item => item.id === value)!;
  return <TxMenu label="Permissions" items={[
    ...PERMISSIONS.map(item => ({ id: item.id, label: item.label, detail: item.detail, checked: item.id === value, onSelect: () => onChange(item.id) })),
    ...allowed ? [{ id: "allowed", label: allowed.label, hint: allowed.count ? String(allowed.count) : undefined, separated: true, onSelect: allowed.open }] : [],
  ]} trigger={<TxButton size="sm" variant="tool" className="chat-tool" data-tone={value === "full" ? "warn" : undefined}><TxIcon name="shield" />{permission.label}<TxIcon name="chevron-down" /></TxButton>} />;
}

/** Model and thinking in one button "GPT-5.6 Sol Medium ⌄": the models by connection, the levels, the
    subagent switch (left out where subagents are set elsewhere: a bot's settings) and the way to add models. */
export function ModelButton({ models, choices, onChoices, onManageModels, subagents = true, align = "end" }: {
  models: ModelChoice[]; choices: Pick<Choices, "model" | "thinking" | "subagents">; onChoices(changes: Partial<Choices>): void; onManageModels(): void;
  subagents?: boolean; align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  const model = models.find(item => item.id === choices.model) ?? models[0];
  if (!model) return <TxButton size="sm" variant="tool" className="chat-tool chat-model" onClick={onManageModels}>Connect a model</TxButton>;
  return <TxPopover open={open} onOpenChange={setOpen} label="Model and thinking" align={align} className="chat-model-pop"
    trigger={<TxButton size="sm" variant="tool" className="chat-tool chat-model"><span className="chat-model-name">{model.name}</span><span className="chat-model-level">{choices.thinking}</span><TxIcon name="chevron-down" /></TxButton>}>
    <div className="chat-pop-label">Model</div>
    <div className="chat-pop-models">{models.map((item, index) => <div key={item.id} className="chat-pop-model">
      {(index === 0 || models[index - 1].connection !== item.connection) && <div className="chat-pop-group">{item.connection}</div>}
      <TxButton variant="row" size="sm" className="chat-level" aria-checked={item.id === choices.model}
        onClick={() => onChoices({ model: item.id, thinking: item.levels.includes(choices.thinking) ? choices.thinking : item.defaultLevel })}>
        <span className="chat-level-label">{item.name}</span><span className="chat-meta">{compact(item.window)}</span>
        {item.id === choices.model ? <TxIcon name="check" /> : <span className="chat-check-gap" />}
      </TxButton>
    </div>)}</div>
    <div className="chat-pop-section">
      <div className="chat-pop-label">Thinking</div>
      <TxSegmented label="Thinking" size="sm" value={choices.thinking} onChange={thinking => onChoices({ thinking })} options={model.levels.map(level => ({ value: level, label: level }))} />
    </div>
    {subagents && <div className="chat-pop-section chat-pop-switch"><TxSwitch label="Subagents" checked={choices.subagents} onCheckedChange={value => onChoices({ subagents: value })} /></div>}
    <div className="chat-pop-section"><TxButton variant="row" size="sm" className="chat-level" onClick={() => { setOpen(false); onManageModels(); }}>
      <span className="chat-level-label">Add or manage models…</span></TxButton></div>
  </TxPopover>;
}

/** How full the context is, as the ring chat clients put in the message box; the numbers on hover. */
function ContextRing({ used, window }: { used: number; window: number }) {
  const share = Math.min(used / window, 1), circumference = 2 * Math.PI * 7;
  const label = `${Math.max(1, Math.round(share * 100))}% of context used · ${compact(used)} of ${compact(window)}`;
  return <TxTooltip label={label}>
    <TxButton size="sm" variant="tool" className="chat-ring" data-level={share > 0.8 ? "high" : undefined} aria-label={label}>
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <circle className="chat-ring-track" cx="10" cy="10" r="7" />
        <circle className="chat-ring-fill" cx="10" cy="10" r="7" strokeDasharray={circumference} strokeDashoffset={circumference * (1 - share)} />
      </svg>
    </TxButton>
  </TxTooltip>;
}

export function compact(tokens: number): string {
  return tokens >= 1_000_000 ? `${tokens / 1_000_000}M` : `${Math.round(tokens / 1000)}k`;
}

