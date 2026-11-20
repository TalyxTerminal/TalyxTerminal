import { installUiStyles } from "./inject";

/** Inert SDK fallback for a view whose renderer has already been disposed.
 * It uses the shared alert surface without starting another renderer or scope. */
export function createViewUnavailable(root: HTMLElement | ShadowRoot): HTMLElement {
  installUiStyles(root);
  const doc = root.ownerDocument ?? document;
  const marker = doc.createElement("div");
  marker.className = "tx-root tx-alert";
  marker.setAttribute("role", "alert");
  const content = doc.createElement("div"); content.className = "tx-alert-content";
  const title = doc.createElement("h4"); title.setAttribute("role", "status"); title.textContent = "View unavailable";
  const next = doc.createElement("p"); next.textContent = "Close and reopen this view to try again.";
  content.append(title, next); marker.append(content);
  return marker;
}
