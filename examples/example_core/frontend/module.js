// Example core's view: other modules mount it in their own panels (ctx.views.mount("example_core.text_card", ...)).
// Plain JavaScript: TALYX loads this file as it is (React and @talyx/sdk come from the window).
import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import { TxButton } from "@talyx/sdk";

const h = React.createElement;

function Card({ text, onSelect }) {
  const [selected, setSelected] = useState(false);
  return h("section", { "aria-label": "Text card", style: { padding: 12, display: "grid", gap: 8 } },
    h("p", { style: { margin: 0 } }, text),
    h(TxButton, { onClick: () => { setSelected(!selected); onSelect(text); } }, selected ? "Selected" : "Select"));
}

export function activate(ctx) {
  ctx.views.provide("text_card", (element, view) => {
    const root = createRoot(element);
    const render = props => root.render(h(Card, { text: props.text, onSelect: text => view.emit("selected", { text }) }));
    render(view.props);
    return { update: render, dispose: () => root.unmount() };
  });
}
