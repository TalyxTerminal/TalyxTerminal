import type { ReactNode } from "react";
import { TxIcon } from "@talyx/sdk";
import type { BotState, Look, Shape, Tint } from "./model";

/* TALYX's bot pictures, after Grok Bot's: a shape, a colour, two eyes. No
   orange: in TALYX orange means something needs you. The
   colours are the user palette tokens (bot.css); the eyes sit a little high
   and to the right, as Grok's do. */

export const SHAPES: Shape[] = ["circle", "blob", "square", "pill", "triangle", "hexagon", "cloud", "drop"];
export const TINTS: Tint[] = ["brown", "red", "yellow", "green", "teal", "blue", "purple", "pink", "grey"];

const BODY: Record<Shape, ReactNode> = {
  circle: <circle cx="20" cy="20" r="18" />,
  blob: <ellipse cx="20" cy="21" rx="18.5" ry="14.5" transform="rotate(-10 20 21)" />,
  square: <rect x="3" y="3" width="34" height="34" rx="11" />,
  pill: <rect x="1.5" y="9" width="37" height="22" rx="11" />,
  triangle: <path d="M20 4c2 0 3.4 1.1 4.5 3.1L37 30c1.6 3-.2 6-3.6 6H6.6C3.2 36 1.4 33 3 30L15.5 7.1C16.6 5.1 18 4 20 4Z" />,
  hexagon: <path d="M20 3.5 34.5 11.8v16.4L20 36.5 5.5 28.2V11.8Z" strokeWidth="4" strokeLinejoin="round" className="bot-avatar-edge" />,
  cloud: <><circle cx="12.5" cy="22" r="9" /><circle cx="21.5" cy="15.5" r="10" /><circle cx="29" cy="22.5" r="8.5" /><rect x="8" y="21" width="25" height="10" rx="5" /></>,
  drop: <path d="M20 2.5s14 13.6 14 22.6a14 14 0 0 1-28 0C6 16.1 20 2.5 20 2.5Z" />,
};

/** Where the eyes go, as the shapes' visual centres differ. */
const EYES: Record<Shape, [number, number]> = {
  circle: [0, 0], blob: [0, 1], square: [0, 0], pill: [0, 1.5], triangle: [0, 6], hexagon: [0, 1], cloud: [1, 2], drop: [0, 6],
};

/** `dot`: unread (quiet) or needs you (orange) on the top corner, like Grok Bot's blue dot, so it stays when the sidebar folds to pictures. */
export type Dot = "unread" | "act" | undefined;

export function Avatar({ look, size = "md", state, dot, className }: { look: Look; size?: "xs" | "sm" | "md" | "lg" | "xl"; state?: BotState; dot?: Dot; className?: string }) {
  const [x, y] = EYES[look.shape];
  return <span className={className ? `bot-avatar ${className}` : "bot-avatar"} data-size={size} data-tint={look.tint} data-state={state} aria-hidden="true">
    {look.photo
      ? <img className="bot-avatar-photo" src={look.photo.data} alt="" />
      : <svg viewBox="0 0 40 40">
        <g className="bot-avatar-body">{BODY[look.shape]}</g>
        <g className="bot-avatar-eyes" transform={`translate(${x} ${y})`}><path d="M18.2 13.6 17.4 18.4" /><path d="M24.4 13.2 25.2 18" /></g>
      </svg>}
    {state === "running" && <span className="bot-badge" data-state="running"><TxIcon name="loader" className="bot-spin" /></span>}
    {state === "paused" && <span className="bot-badge" data-state="paused"><TxIcon name="pause" /></span>}
    {dot && <span className="bot-avatar-dot" data-tone={dot} />}
  </span>;
}

/** A group's picture, after Grok Bot's: its first members, overlapping. */
export function GroupAvatar({ looks, size = "md", dot }: { looks: Look[]; size?: "sm" | "md"; dot?: Dot }) {
  return <span className="bot-group-avatar" data-size={size} aria-hidden="true">
    {looks.slice(0, 3).map((look, index) => <Avatar key={index} look={look} size="xs" className={`bot-group-avatar-${index + 1}`} />)}
    {dot && <span className="bot-avatar-dot" data-tone={dot} />}
  </span>;
}
