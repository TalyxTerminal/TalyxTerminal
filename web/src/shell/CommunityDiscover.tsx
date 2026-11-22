import { t } from "./i18n";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TxButton, TxIcon, TxSearch, type IconName } from "@talyx/sdk";
import type { ModuleEntry } from "./communityData";
import { Byline, Cover, Linked, matches } from "./communityParts";

/* Discover: the official modules, and what people will share (modules, discussions).
   A rail of sections on the left. All opens on a front page of the official modules; an official module's page
   opens in place. The shared sections have no community service behind them yet and show that they are empty. */

type Section = "all" | "official" | Shared;
type Shared = "module" | "forum";

const titles = (): Record<Section, string> => ({
  all: t("All"), official: t("Official"), module: t("Modules"), forum: t("Discussions"),
});
const ICONS: Record<Section, IconName> = {
  all: "grid", official: "verified", module: "package", forum: "messages",
};
const empty = (): Record<Shared, string> => ({
  module: t("No shared modules yet"),
  forum: t("No discussions yet"),
});

export interface DiscoverActions {
  onOpenOfficial: (id: string) => void;
  onManage: (id: string) => void;
}

export function Discover({ modules, ...actions }: DiscoverActions & { modules: ModuleEntry[] }) {
  const official = useMemo(() => modules.filter(item => item.official).sort((a, b) => a.name.localeCompare(b.name)), [modules]);
  const [section, setSection] = useState<Section>("all");
  const [opened, setOpened] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const main = useRef<HTMLDivElement>(null);
  // Typing a search on All swaps the front page for results (and back); the box keeps the focus across the swap.
  const typed = useRef(false);
  const search = (value: string) => { typed.current = true; setQuery(value); };
  const go = (next: Section) => { typed.current = false; setSection(next); setOpened(null); setQuery(""); };
  // Every change of page starts at the top.
  useEffect(() => { main.current?.scrollTo({ top: 0 }); }, [section, opened]);

  const railItem = (value: Section, count?: number) => <TxButton key={value} variant="row" className="cm-rail-item"
    aria-current={section === value && !opened ? "page" : undefined} onClick={() => go(value)}>
    <TxIcon name={ICONS[value]} /><span>{titles()[value]}</span>{count !== undefined && <span className="cm-rail-count">{count}</span>}
  </TxButton>;
  const item = opened ? official.find(entry => entry.id === opened) ?? null : null;

  return <div className="cm-discover">
    <nav className="cm-rail" aria-label={t("Sections")}>
      {railItem("all")}
      {railItem("module")}
      {railItem("official", official.length)}
      <div className="cm-rail-gap" />
      {railItem("forum")}
    </nav>

    <div className="cm-main" ref={main}>
      {item
        ? <ModulePage key={item.id} item={item} back={query.trim() ? t("Results for “{query}”", { query: query.trim() }) : titles()[section]} onBack={() => setOpened(null)} {...actions} />
        : section === "all" && !query.trim()
          ? <Front official={official} query={query} onQuery={search} focus={typed.current} onOpen={setOpened} onSection={go} />
          : section === "all" || section === "official"
            ? <ModuleGrid key={section} title={section === "all" ? t("Results for “{query}”", { query: query.trim() }) : titles().official} official={official}
              query={query} onQuery={search} focus={typed.current} onOpen={setOpened} />
            : <EmptySection section={section} />}
    </div>
  </div>;
}

/* ── Pieces of the lists ────────────────────────────────────────────────── */

function Head({ title, count, children }: { title: string; count?: number; children?: ReactNode }) {
  return <header className="cm-list-head">
    <h1>{title}</h1>
    {count !== undefined && <span className="cm-count">{count}</span>}
    <div className="cm-list-tools">{children}</div>
  </header>;
}

/** One official module in a grid: cover, name, publisher. */
function Card({ item, onOpen }: { item: ModuleEntry; onOpen: (id: string) => void }) {
  return <button type="button" className="cm-card" onClick={() => onOpen(item.id)}>
    <Cover seed={item.id} module={item.id}><span className="cm-cover-label">{t("Official")}</span></Cover>
    <span className="cm-card-name" title={item.name}>{item.name}</span>
    <span className="cm-card-summary">{item.summary}</span>
    <span className="cm-card-foot"><Byline /></span>
  </button>;
}

/* ── All: the front page ────────────────────────────────────────────────── */

function Front({ official, query, onQuery, focus, onOpen, onSection }: {
  official: ModuleEntry[];
  query: string;
  focus: boolean;
  onQuery: (query: string) => void;
  onOpen: (id: string) => void;
  onSection: (section: Section) => void;
}) {
  // The featured places go to official modules, those with panels first: they open in a workspace.
  const featured = [...official].sort((a, b) => Number(b.panels.length > 0) - Number(a.panels.length > 0)).slice(0, 3);
  const [main, ...picks] = featured;
  return <div className="cm-front">
    <Head title={t("All")}>
      <TxSearch value={query} onValueChange={onQuery} className="cm-search" aria-label={t("Search")} placeholder={t("Search everything")} autoFocus={focus} />
    </Head>

    {main && <div className="cm-stage">
      <section className="cm-featured" aria-label={t("Featured")}>
        <button type="button" className="cm-feature" onClick={() => onOpen(main.id)}>
          <Cover seed={main.id} module={main.id} size="feature" />
          <span className="cm-feature-text">
            <span className="cm-kicker">{t("Featured · Official")}</span>
            <span className="cm-feature-name">{main.name}</span>
            <span className="cm-feature-summary">{main.summary}</span>
            <span className="cm-feature-meta"><Byline size="sm" /></span>
          </span>
          <span className="cm-feature-go" aria-hidden="true"><TxIcon name="arrow-right" /></span>
        </button>
        <div className="cm-picks">
          {picks.map(item => <button key={item.id} type="button" className="cm-pick" onClick={() => onOpen(item.id)}>
            <Cover seed={item.id} module={item.id} size="thumb" />
            <span className="cm-pick-text">
              <span className="cm-kicker">{t("Official")}</span>
              <span className="cm-pick-name">{item.name}</span>
              <span className="cm-pick-meta"><Byline /></span>
            </span>
          </button>)}
        </div>
      </section>
    </div>}

    <section className="cm-shelf" aria-label={t("Official modules")}>
      <header className="cm-shelf-head">
        <h2>{t("Official modules")}</h2>
        <TxButton size="sm" variant="quiet" className="cm-see-all" onClick={() => onSection("official")}>{t("See all")}<TxIcon name="arrow-right" /></TxButton>
      </header>
      <div className="cm-grid">{official.slice(0, 4).map(item => <Card key={item.id} item={item} onOpen={onOpen} />)}</div>
    </section>
  </div>;
}

/* ── Official, or a search on All: a grid of covers ─────────────────────── */

function ModuleGrid({ title, official, query, onQuery, focus, onOpen }: {
  title: string;
  official: ModuleEntry[];
  query: string;
  focus: boolean;
  onQuery: (query: string) => void;
  onOpen: (id: string) => void;
}) {
  const shown = official.filter(item => matches(query, item.name, item.summary));
  return <div className="cm-list-page">
    <Head title={title} count={shown.length}>
      <TxSearch value={query} onValueChange={onQuery} className="cm-search" aria-label={t("Search")} placeholder={t("Search everything")} autoFocus={focus} />
    </Head>
    <div className="cm-grid" aria-label={title}>{shown.map(item => <Card key={item.id} item={item} onOpen={onOpen} />)}</div>
    {!shown.length && <div className="cm-nothing">{t("Nothing matches “{query}”", { query: query.trim() })}</div>}
  </div>;
}

/* ── A shared section with nothing in it yet ────────────────────────────── */

function EmptySection({ section }: { section: Shared }) {
  return <div className="cm-list-page">
    <Head title={titles()[section]} />
    <div className="cm-empty" role="status">
      <Cover seed={`empty-${section}`} icon={ICONS[section]} size="thumb" />
      <span className="cm-empty-title">{empty()[section]}</span>
    </div>
  </div>;
}

/* ── One official module ────────────────────────────────────────────────── */

function ModulePage({ item, back, onBack, onOpenOfficial, onManage }: DiscoverActions & {
  item: ModuleEntry;
  back: string;
  onBack: () => void;
}) {
  const facts: [string, ReactNode][] = [
    ...(item.version ? [[t("Version"), <span key="version" className="cm-mono">{item.version}</span>] as [string, ReactNode]] : []),
    [t("Panels"), item.panels.length ? item.panels.join(", ") : t("None")],
    [t("Works with"), item.requires.length ? item.requires.join(", ") : t("Nothing else")],
  ];
  return <div className="cm-item-page">
    <TxButton size="sm" variant="quiet" className="cm-back" onClick={onBack}><TxIcon name="arrow-left" />{back}</TxButton>
    <Cover seed={item.id} module={item.id} size="banner"><span className="cm-cover-label">{t("Official")}</span></Cover>
    <header className="cm-item-head">
      <div className="cm-item-title">
        <h1>{item.name}</h1>
        <div className="cm-item-meta"><Byline size="sm" /></div>
      </div>
      <div className="cm-item-actions">
        <TxButton onClick={() => onManage(item.id)}><TxIcon name="settings" />{t("Manage")}</TxButton>
        {item.panels.length > 0 && <TxButton variant="primary" onClick={() => onOpenOfficial(item.id)}><TxIcon name="external" />{t("Open in a new workspace")}</TxButton>}
      </div>
    </header>

    <div className="cm-item-body">
      <div className="cm-item-main">
        <div className="cm-doc"><p><Linked text={item.summary} /></p></div>
      </div>
      <aside className="cm-item-side" aria-label={t("About")}>
        <dl className="cm-facts">
          <div><dt>{t("Publisher")}</dt><dd>TALYX</dd></div>
          {facts.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
        </dl>
      </aside>
    </div>
  </div>;
}
