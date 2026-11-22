import { t } from "../../i18n";
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { WidgetBusContext, type CommMessage } from "./bus";

let bundle: Promise<string> | null = null;
/** The widget frame's code (about 1.7 MB) is fetched the first time a widget is shown, never with the page. */
const frameCode = () => bundle ??= import("./frame.bundle.js?raw").then(module => (module.default as string).replace(/<\/script/gi, "<\\/script"));

/** A widget (slider, dropdown, button, `interact`) in an isolated frame; its messages go to the kernel through the panel. */
export function WidgetOutput({modelId}: {modelId: string}) {
  const bus = useContext(WidgetBusContext);
  const frame = useRef<HTMLIFrameElement>(null);
  const [code, setCode] = useState<string | null>(null);
  const [height, setHeight] = useState(32);
  const [gone, setGone] = useState(false);
  useEffect(() => {void frameCode().then(setCode);}, []);
  const content = useMemo(() => code === null ? null : `<!doctype html><html><head><meta charset="utf-8"><style>html{color-scheme:dark}html:root{--jp-content-font-color1:#c9ced6;--jp-widgets-color:#c9ced6;--jp-ui-font-color0:#e6e8eb;--jp-ui-font-color1:#c9ced6;--jp-layout-color0:#15171b;--jp-layout-color1:#1d2026;--jp-layout-color2:#2a2e35;--jp-layout-color3:#3a3f47;--jp-border-color0:#4a505a;--jp-border-color1:#3a3f47;--jp-border-color2:#2a2e35;--jp-widgets-input-color:#e6e8eb;--jp-widgets-input-background-color:#1d2026;--jp-widgets-input-border-color:#3a3f47;--jp-widgets-slider-handle-background-color:#c9ced6;--jp-widgets-slider-handle-border-color:#c9ced6;--jp-widgets-slider-active-handle-color:#e6e8eb}body{margin:0;padding:2px 0;background:transparent;color:#c9ced6;font:12px/1.5 system-ui,sans-serif}</style></head><body><div id="widget"></div><script>${code}</script></body></html>`, [code]);
  useEffect(() => {
    if (bus === null) return;
    const post = (message: unknown) => frame.current?.contentWindow?.postMessage(message, "*");
    const unsubscribe = bus.subscribe((message: CommMessage) => post({type: message.msgType, commId: message.commId, data: message.data, target: message.target}));
    let waited: ReturnType<typeof setTimeout> | null = null;
    const receive = (event: MessageEvent) => {
      if (frame.current === null || event.source !== frame.current.contentWindow || event.data === null || typeof event.data !== "object" || event.data.source !== "talyx-widget") return;
      const message = event.data as {type: string; commId?: string; data?: unknown; height?: number; plotly?: boolean; library?: string};
      if (message.type === "ready") {
        const start = () => {
          if (!bus.opened.has(modelId)) return false;
          for (const opened of bus.opened.values()) post({type: "comm_open", commId: opened.commId, data: opened.data, target: opened.target});
          post({type: "view", modelId});
          return true;
        };
        // The kernel's open messages arrive with the connection, a moment after the output itself.
        if (!start()) waited = setTimeout(() => {if (!start()) setGone(true);}, 1500);
      } else if (message.type === "unsupported") {
        post({type: "notice", text: message.plotly ? t("plotly widgets don't run here; use fig.show()") : message.library
          ? t("{library} widgets don't run here", { library: message.library }) : t("This widget library doesn't run here")});
      } else if (message.type === "comm_msg") bus.send(message.commId!, message.data);
      else if (message.type === "height" && Number.isFinite(message.height)) setHeight(Math.min(2000, Math.max(24, Number(message.height))));
    };
    window.addEventListener("message", receive);
    return () => {unsubscribe(); window.removeEventListener("message", receive); if (waited !== null) clearTimeout(waited);};
  }, [bus, modelId, content]);
  if (gone) return <p className="qx-muted">{t("Widget from before the restart")}</p>;
  return <div className="qx-nb-html">{content !== null && <iframe ref={frame} title={t("Widget")} srcDoc={content} sandbox="allow-scripts" style={{height}} />}</div>;
}
