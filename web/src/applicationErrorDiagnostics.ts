export type FrontendErrorSource =
  | "bootstrap"
  | "react"
  | "unhandled-rejection"
  | "window-error";

export type FrontendErrorReporter = (
  source: FrontendErrorSource,
  error: unknown,
  details?: Readonly<Record<string, unknown>>,
) => void;

export const reportFrontendError: FrontendErrorReporter = (
  source,
  error,
  details,
) => {
  console.error(`[TALYX] Frontend ${source}`, error, details ?? {});
};

export function installWindowErrorDiagnostics(
  target: Window = window,
  report: FrontendErrorReporter = reportFrontendError,
): void {
  target.addEventListener("error", (event) => {
    report("window-error", event.error ?? event.message);
  });
  target.addEventListener("unhandledrejection", (event) => {
    report("unhandled-rejection", event.reason);
  });
}
