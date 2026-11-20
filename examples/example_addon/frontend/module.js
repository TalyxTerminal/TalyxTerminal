// Example add-on: a panel that calls example_core's action and mounts its view, a settings section and a status item.
// Plain JavaScript: TALYX loads this file as it is (React and @talyx/sdk come from the window).
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { TxButton, TxInput, TxRoot } from "@talyx/sdk";

const h = React.createElement;

function Panel({ ctx, initial, onText }) {
  const [text, setText] = useState(initial);
  const [counted, setCounted] = useState("");
  const [picked, setPicked] = useState("");
  const card = useRef(null);
  const mounted = useRef(null);
  useEffect(() => {
    // example_core's view, inside this panel; its "selected" event comes back here.
    mounted.current = ctx.views.mount("example_core.text_card", card.current, { text }, { selected: payload => setPicked(payload.text) });
    return () => mounted.current.dispose();
  }, []);
  useEffect(() => { mounted.current?.update({ text }); onText(text); }, [text]);
  const count = async () => {
    try {
      const stats = await ctx.actions.call("example_core.text_stats", { text });
      setCounted(`${stats.characters} characters, ${stats.words} words`);
    } catch (error) { setCounted(error.message); }
  };
  return h("section", { style: { padding: 16, display: "grid", gap: 8, maxWidth: 420 } },
    h(TxInput, { "aria-label": "Text", value: text, onChange: event => setText(event.target.value) }),
    h(TxButton, { variant: "primary", onClick: () => void count() }, "Count"),
    h("output", { "aria-label": "Text statistics" }, counted),
    h("div", { ref: card }),
    picked ? h("p", null, `Picked: ${picked}`) : null);
}

export function activate(ctx) {
  ctx.status.set("state", { level: "ok", tooltip: "Example add-on is running" });
  ctx.panels.provide("main", (element, panel) => {
    let text = panel.initialState?.text ?? "Hello from the add-on";
    const root = createRoot(element);
    root.render(h(TxRoot, { overlay: panel.overlayRoot }, h(Panel, { ctx: panel, initial: text, onText: value => {
      if (value !== text) { text = value; panel.panel?.stateChanged(); }
    } })));
    return { captureState: () => ({ text }), dispose: () => root.unmount() };
  });
  ctx.views.provide("settings", element => {
    const root = createRoot(element);
    root.render(h("p", null, "The example add-on has nothing to set."));
    return { dispose: () => root.unmount() };
  });
}
