/* Injects the design system stylesheet into a document or a shadow root.
   Module views live inside shadow trees, so a single stylesheet in `document.head`
   would not reach them. Each root gets its own copy. */
import { talyxUiCss } from "./styles";
import { scopeCss } from "./scope-css";

export const TX_ROOT_CLASS = "tx-root";
export const TX_SCOPE_SELECTOR = ":host";

const elements = new WeakMap<Document | ShadowRoot, HTMLStyleElement>();
let scoped: string | null = null;

/**
 * The stylesheet for a shadow root, scoped to `:host` so it works whether or not
 * a module wraps its tree in a TxRoot element.
 *
 * The up/down colours and the user's choice of up colour are deliberately left out:
 * they are declared on the document root and inherited, so a user's choice reaches
 * module content. Declaring them here would pin every module to green-up.
 */
export function scopedUiCss(): string {
  scoped ??= scopeCss(stripInheritedPreference(talyxUiCss), TX_SCOPE_SELECTOR);
  return scoped;
}

/** Removes the rules a shadow root must inherit rather than redeclare. */
function stripInheritedPreference(css: string): string {
  return css
    .replace(/:root\[data-up-color="[^"]*"\]\s*\{[^}]*\}/g, "")
    .replace(/^\s*--tx-(?:up|down):[^;]*;\s*$/gm, "");
}

/**
 * Ensures the TALYX stylesheet is present in `root`. A module calling this with
 * a plain element installs it in that element's own root node, so the rules
 * follow the shadow boundary. Repeat calls are a no-op.
 *
 * Safe to call where there is no DOM (node tests, server-side render): it
 * simply does nothing there.
 */
export function installUiStyles(root: Document | ShadowRoot | HTMLElement): void {
  const target = typeof (root as Element).getRootNode === "function" ? (root as Element).getRootNode() : root;
  if (!isStylableRoot(target)) return;
  if (elements.has(target)) return;
  const style = (target instanceof Document ? target : target.ownerDocument).createElement("style");
  style.setAttribute("data-talyx-ui", "");
  style.textContent = target instanceof Document ? talyxUiCss : scopedUiCss();
  (target instanceof Document ? target.head : target).append(style);
  elements.set(target, style);
  installScrollHint(target);
}

/**
 * The only scroll cue in TALYX (native scrollbars are hidden everywhere): while something
 * scrolls, a thin dark thumb shows over its edge, then fades. It takes no room and never
 * catches the pointer. One listener per root, because scroll events do not cross a shadow
 * boundary; one thumb element per root, positioned in viewport coordinates.
 */
function installScrollHint(root: Document | ShadowRoot): void {
  const doc = root instanceof Document ? root : root.ownerDocument;
  const view = doc.defaultView;
  if (!view) return;
  let thumb: HTMLElement | null = null;
  let hide = 0, frame = 0, pending: Element | null = null;
  const last = new WeakMap<Element, { top: number; left: number }>();
  const draw = () => {
    frame = 0;
    const element = pending;
    pending = null;
    if (!element) return;
    const { scrollTop, scrollLeft, scrollHeight, scrollWidth, clientHeight, clientWidth } = element;
    const before = last.get(element) ?? { top: scrollTop, left: scrollLeft };
    last.set(element, { top: scrollTop, left: scrollLeft });
    const vertical = scrollHeight > clientHeight + 1 && (scrollTop !== before.top || scrollLeft === before.left);
    const horizontal = !vertical && scrollWidth > clientWidth + 1;
    if (!vertical && !horizontal) return;
    if (!thumb?.isConnected) {
      thumb = doc.createElement("div");
      thumb.className = "tx-scrollhint";
      thumb.setAttribute("aria-hidden", "true");
      (root instanceof Document ? doc.body : root).append(thumb);
    }
    const page = element === doc.scrollingElement;
    const box = page ? { left: 0, top: 0, right: view.innerWidth, bottom: view.innerHeight } : element.getBoundingClientRect();
    const styles = view.getComputedStyle(thumb);
    const width = parseFloat(styles.getPropertyValue("--tx-scroll-hint-width")) || 3;
    const shortest = parseFloat(styles.getPropertyValue("--tx-scroll-hint-min")) || 24;
    const fade = parseFloat(styles.getPropertyValue("--tx-scroll-hint-fade")) || 700;
    thumb.style.transform = "none";
    const origin = thumb.getBoundingClientRect();
    let x: number, y: number, w: number, h: number;
    if (vertical) {
      h = Math.max(shortest, clientHeight * clientHeight / scrollHeight);
      w = width;
      x = box.right - width - 2;
      y = box.top + (clientHeight - h) * scrollTop / (scrollHeight - clientHeight);
    } else {
      w = Math.max(shortest, clientWidth * clientWidth / scrollWidth);
      h = width;
      x = box.left + (clientWidth - w) * scrollLeft / (scrollWidth - clientWidth);
      y = box.bottom - width - 2;
    }
    thumb.style.width = `${w}px`;
    thumb.style.height = `${h}px`;
    thumb.style.transform = `translate(${x - origin.left}px, ${y - origin.top}px)`;
    thumb.setAttribute("data-on", "");
    view.clearTimeout(hide);
    hide = view.setTimeout(() => thumb?.removeAttribute("data-on"), fade);
  };
  root.addEventListener("scroll", event => {
    const target = event.target;
    pending = target === doc ? doc.scrollingElement : target instanceof view.Element ? target : null;
    if (!frame) frame = view.requestAnimationFrame(draw);
  }, { capture: true, passive: true });
}

/** Duck-typed so the check works without browser globals present. */
function isStylableRoot(value: unknown): value is Document | ShadowRoot {
  if (typeof Document !== "undefined" && value instanceof Document) return true;
  if (typeof ShadowRoot !== "undefined" && value instanceof ShadowRoot) return true;
  return false;
}

/** Test/teardown helper: removes the stylesheet from one root. */
export function removeUiStyles(root: Document | ShadowRoot): void {
  elements.get(root)?.remove();
  elements.delete(root);
}
