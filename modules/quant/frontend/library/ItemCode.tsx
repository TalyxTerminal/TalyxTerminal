/* The frozen code beside an item (library.js devPanel): read-only, with line numbers; the line that failed is marked and the reason under it. A model or an allocation has no file, its settings are shown. */
import { useEffect, useRef } from "react";
import { TxIcon } from "@talyx/sdk";

export function CodePane({title, file, lines, errorLine, error, frozen, from, onClose}: {title: string; file: string; lines: string[]; errorLine?: number | null; error?: string | null; frozen: string; from: string; onClose?: () => void}) {
  const bad = useRef<HTMLDivElement>(null);
  useEffect(() => {bad.current?.scrollIntoView({block: "center"});}, [errorLine, file]);
  return <div className="lb-code">
    <div className="lb-code-tabs"><span><TxIcon name="code" />{title}</span>{onClose && <button type="button" aria-label="Close code" onClick={onClose}><TxIcon name="close" /></button>}</div>
    <div className="lb-code-file">{file}</div>
    <div className="lb-code-body" role="region" aria-label="Frozen code">{lines.map((line, index) => <div key={index} className="lb-code-line" data-bad={index + 1 === errorLine ? "" : undefined} ref={index + 1 === errorLine ? bad : undefined}>
      <span className="lb-code-n">{index + 1}</span><span className="lb-code-t">{line || " "}</span>{index + 1 === errorLine && error && <div className="lb-code-peek">{error}</div>}</div>)}</div>
    <div className="lb-code-foot"><span>{frozen}</span><span>{from}</span></div>
  </div>;
}
