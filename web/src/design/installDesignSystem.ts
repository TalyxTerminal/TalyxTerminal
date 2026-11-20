/* Boots the design system for the whole document.
   Modules get their stylesheet from the kernel when their container is created;
   this covers the shell itself, and sets the user's up colour
   before anything renders so values never flash the wrong colour. */
import { installUiStyles } from "@talyx/sdk";
import { initUpColor } from "./upColor";

export function installDesignSystem(): void {
  installUiStyles(document);
  initUpColor();
}
