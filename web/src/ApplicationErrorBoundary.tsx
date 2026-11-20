import { Component, type ErrorInfo, type ReactNode } from "react";
import { reportFrontendError, type FrontendErrorReporter } from "./applicationErrorDiagnostics";

interface ApplicationFailureProps {
  message?: string;
  onReload?: () => void;
  title?: string;
}

export function ApplicationFailure({
  message = "The workspace stopped before it could finish loading. Reload TALYX to try again.",
  onReload = () => window.location.reload(),
  title = "TALYX encountered a problem",
}: ApplicationFailureProps) {
  return (
    <main className="application-failure" aria-live="assertive" role="alert">
      <section className="application-failure__panel">
        <h1>{title}</h1>
        <p>{message}</p>
        <button type="button" onClick={onReload}>
          Reload TALYX
        </button>
      </section>
    </main>
  );
}

interface ApplicationErrorBoundaryProps {
  children: ReactNode;
  onError?: FrontendErrorReporter;
  onReload?: () => void;
}

interface ApplicationErrorBoundaryState {
  failed: boolean;
}

export class ApplicationErrorBoundary extends Component<
  ApplicationErrorBoundaryProps,
  ApplicationErrorBoundaryState
> {
  state: ApplicationErrorBoundaryState = { failed: false };

  static getDerivedStateFromError(_error: unknown): ApplicationErrorBoundaryState {
    return { failed: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    (this.props.onError ?? reportFrontendError)("react", error, {
      componentStack: info.componentStack ?? "",
    });
  }

  render(): ReactNode {
    if (this.state.failed) {
      return <ApplicationFailure onReload={this.props.onReload} />;
    }
    return this.props.children;
  }
}

export function renderStaticApplicationFailure(
  container: HTMLElement,
  onReload: () => void = () => window.location.reload(),
): void {
  const document = container.ownerDocument;
  const main = document.createElement("main");
  const panel = document.createElement("section");
  const title = document.createElement("h1");
  const message = document.createElement("p");
  const reload = document.createElement("button");

  main.className = "application-failure";
  main.setAttribute("aria-live", "assertive");
  main.setAttribute("role", "alert");
  panel.className = "application-failure__panel";
  title.textContent = "TALYX encountered a problem";
  message.textContent =
    "The workspace stopped before it could finish loading. Reload TALYX to try again.";
  reload.type = "button";
  reload.textContent = "Reload TALYX";
  reload.addEventListener("click", onReload);

  panel.append(title, message, reload);
  main.append(panel);
  container.replaceChildren(main);
}
