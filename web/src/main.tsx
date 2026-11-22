import { t } from "./shell/i18n";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import {
  ApplicationErrorBoundary,
  ApplicationFailure,
  renderStaticApplicationFailure,
} from "./ApplicationErrorBoundary";
import {
  installWindowErrorDiagnostics,
  reportFrontendError,
} from "./applicationErrorDiagnostics";
import { platformRuntime } from "./kernel/runtime";
import { installDesignSystem } from "./design/installDesignSystem";
import { followUpColor } from "./design/upColor";
import { workspaceFrameId } from "./shell/workspaceWindows";

const App = lazy(() => import("./shell/App"));

const DesignSystemPage = lazy(() => import("./shell/DesignSystemPage"));

const rootElement = document.getElementById("root");
if (!(rootElement instanceof HTMLElement)) {
  throw new Error("TALYX root element is unavailable.");
}

// Core assembly belongs to one page lifetime. A bootstrap dependency update
// needs a fresh module graph, while ordinary component updates keep using HMR.
const bootstrapStarted = import.meta.hot?.data.bootstrapStarted === true;

let root: ReturnType<typeof createRoot> | null = null;

try {
  if (!bootstrapStarted) {
    installWindowErrorDiagnostics();
    root = createRoot(rootElement);
  }
} catch (error) {
  reportFrontendError("bootstrap", error);
  renderStaticApplicationFailure(rootElement);
}

function CompatibilityFailure() {
  return (
    <ApplicationFailure
      title={t("TALYX can’t start")}
      message={t("TALYX could not reach its Host. Start it, then reload.")}
    />
  );
}

/** The design system sample page is reachable only by its explicit URL. */
function designSystemRequested(): boolean {
  return window.location.pathname.replace(/\/+$/, "") === "/design-system";
}

async function startTalyx(): Promise<void> {
  if (root === null) return;
  installDesignSystem();
  if (designSystemRequested()) {
    root.render(
      <StrictMode>
        <ApplicationErrorBoundary>
          <Suspense fallback={null}>
            <DesignSystemPage />
          </Suspense>
        </ApplicationErrorBoundary>
      </StrictMode>,
    );
    return;
  }
  try {
    await platformRuntime().ready();
  } catch {
    root.render(<CompatibilityFailure />);
    return;
  }
  void followUpColor(platformRuntime().bridge, workspaceFrameId()).catch((error: unknown) => reportFrontendError("bootstrap", error));
  root.render(
    <StrictMode>
      <ApplicationErrorBoundary>
        <Suspense fallback={null}>
          <App />
        </Suspense>
      </ApplicationErrorBoundary>
    </StrictMode>,
  );
}

if (!bootstrapStarted) {
  if (import.meta.hot) import.meta.hot.data.bootstrapStarted = true;
  void startTalyx().catch((error: unknown) => {
    reportFrontendError("bootstrap", error);
    renderStaticApplicationFailure(rootElement);
  });
}

if (import.meta.hot) {
  import.meta.hot.accept(() => window.location.reload());
}
