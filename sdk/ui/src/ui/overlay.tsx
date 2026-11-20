/* Overlay host.
   Popups (menus, dialogs, sheets, tooltips, toasts) must be rendered into the
   module's own overlay root: the kernel gives every module a
   `[data-talyx-overlays]` element next to its `[data-talyx-container]`, inside
   the same shadow root, carrying the scoped stylesheet and the forwarded
   `--tx-*` variables. React portals do not follow the shadow boundary on their
   own, so a portal that falls back to `document.body` loses all of that.

   What a module has to do: render its tree inside <TxRoot> within the element
   the kernel passed to `mount`. TxRoot finds the sibling overlay root itself.
   A module that renders somewhere else (for example into a detached node)
   passes `ctx.overlayRoot` explicitly: <TxRoot overlay={ctx.overlayRoot}>, or
   wraps its tree in <TxOverlayProvider container={ctx.overlayRoot}>.
   Platform surfaces outside any module container keep the document body. */
import { createContext, useContext, useLayoutEffect, useState, type ReactNode } from "react";

const OverlayContext = createContext<HTMLElement | null>(null);

export function TxOverlayProvider({ container, children }: { container: HTMLElement | null; children: ReactNode }) {
  return <OverlayContext.Provider value={container}>{children}</OverlayContext.Provider>;
}

/** The element popups are portalled into. `null` means "use document.body". */
export function useOverlayContainer(): HTMLElement | null {
  return useContext(OverlayContext);
}

/** A body-level module host may already be hidden by a surrounding modal.
 * Give an open CM modal its own styled host, created after that surrounding
 * layer. Radix can then manage the new layer without a hidden ancestor.
 * Shadow-root overlays retain their isolated host and stylesheet. */
export function useModalContainer(open: boolean) {
  const container = useOverlayContainer();
  const separate = !!container && container.parentElement === container.ownerDocument.body
    && container.hasAttribute("data-talyx-overlays");
  const [modalHost, setModalHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!open || !separate || !container) return;
    const host = container.ownerDocument.createElement("div");
    host.setAttribute("data-talyx-overlays", "");
    host.setAttribute("data-talyx-modal-host", "");
    const syncTheme = () => { host.className = container.className; host.style.cssText = container.style.cssText; };
    syncTheme();
    const changes = new MutationObserver(syncTheme);
    changes.observe(container, {attributes: true, attributeFilter: ["style", "class"]});
    container.ownerDocument.body.append(host);
    setModalHost(host);
    return () => {changes.disconnect(); host.remove(); setModalHost(null);};
  }, [container, open, separate]);
  return {container: separate ? modalHost ?? undefined : container ?? undefined,
    ready: !open || !separate || !!modalHost};
}

/** The kernel-provided overlay root for the module container `element` sits in,
    or null when `element` is not inside a module container. */
export function findOverlayRoot(element: Element | null): HTMLElement | null {
  const container = element?.closest("[data-talyx-container]");
  const sibling = container?.nextElementSibling;
  return sibling?.hasAttribute("data-talyx-overlays") ? sibling as HTMLElement : null;
}
