import { TxToastProvider } from "@talyx/sdk";
import { HomeWindow } from "./HomeWindow";
import { WorkspaceFrame } from "./WorkspaceFrame";
import { WorkspaceWindows, workspaceFrameId } from "./workspaceWindows";

/** The window is the home window with a frame per open workspace beside it, or one of those frames. */
export default function App() {
  return workspaceFrameId() ? <TxToastProvider><WorkspaceFrame /></TxToastProvider> : <WorkspaceWindows home={<HomeWindow />} />;
}
