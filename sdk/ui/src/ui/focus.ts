import { useRef, type KeyboardEvent } from "react";

function activeElement(document: Document): Element | null {
  let active = document.activeElement;
  while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
  return active;
}

function controls(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(
    'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]',
  )).filter(element => !element.closest('[hidden],[inert],[aria-hidden="true"]')
    && element.tabIndex >= 0 && element.getClientRects().length > 0);
}

/** Radix owns modality, dismissal and layers. Its document.activeElement checks
 * cannot see focus inside Shadow DOM, so autofocus and Tab boundaries use the
 * actual focused element. Capture prevents Radix moving that focus a second time. */
export function useDialogFocus() {
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  return {
    ref,
    onOpenAutoFocus(event: Event) {
      const root = ref.current;
      if (!root) return;
      event.preventDefault();
      opener.current = activeElement(root.ownerDocument) as HTMLElement | null;
      // A confirmation can mark its least destructive control with data-autofocus.
      (controls(root).find(element => element.hasAttribute("data-autofocus")) ?? controls(root)[0] ?? root).focus({ preventScroll: true });
    },
    onCloseAutoFocus(event: Event) {
      event.preventDefault();
      if (opener.current?.isConnected) opener.current.focus({ preventScroll: true });
    },
    onKeyDownCapture(event: KeyboardEvent<HTMLDivElement>) {
      if (event.key !== "Tab" || !ref.current) return;
      const root = ref.current;
      // A nested dialog is portalled into a sibling root; never trap its keys.
      if (!root.contains(event.target as Node)) return;
      const items = controls(root);
      const active = activeElement(root.ownerDocument);
      const index = items.indexOf(active as HTMLElement);
      if (!items.length || index < 0 || (event.shiftKey ? index === 0 : index === items.length - 1)) {
        event.preventDefault();
        event.stopPropagation();
        (items.length ? items[event.shiftKey ? items.length - 1 : 0] : root).focus({ preventScroll: true });
      }
    },
  };
}
