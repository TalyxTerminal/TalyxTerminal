/* Runs inside the output's isolated frame: draws one widget view with @jupyter-widgets/html-manager. The frame has no access to TALYX; the widget's messages
   (the kernel's comm traffic) come and go through postMessage to the panel, which relays them to the kernel. Only the controls and output libraries are
   bundled: another widget library says so instead of leaving an empty frame. */
import { HTMLManager } from "@jupyter-widgets/html-manager";
// The controls' styles with their variables (widgets.built.css has JupyterLab's light colours written in, black labels on the dark panel);
// the panel's page sets the variables to its own colours. Text imports do not follow @import, so the files are listed here in its order.
import materialColors from "@jupyter-widgets/controls/css/materialcolors.css";
import labVariables from "@jupyter-widgets/controls/css/labvariables.css";
import lumino from "@jupyter-widgets/controls/css/lumino.css";
import noUiSlider from "@jupyter-widgets/controls/css/nouislider.css";
import noUiSliderCustom from "@jupyter-widgets/controls/css/nouislider-custom.css";
import widgetsBase from "@jupyter-widgets/controls/css/widgets-base.css";

type Callback = (message: unknown) => void;
const post = (message: unknown) => parent.postMessage({source: "talyx-widget", ...(message as object)}, "*");

class Comm {
  private message: Callback = () => {};
  private close: Callback = () => {};
  constructor(readonly comm_id: string, readonly target_name: string) {}
  on_msg(callback: Callback) {this.message = callback;}
  on_close(callback: Callback) {this.close = callback;}
  /** A model holds its next change until the kernel is idle after the last one; the kernel's status does not come back through the panel, so the message
   *  counts as handled once it is on its way (the kernel takes them in order). Without this a slider reached the kernel once and never again. */
  send(data: unknown, callbacks?: {iopub?: {status?: (message: unknown) => void}}) {
    post({type: "comm_msg", commId: this.comm_id, data});
    const idle = callbacks?.iopub?.status;
    if (idle) setTimeout(() => idle({content: {execution_state: "idle"}, header: {msg_type: "status"}, parent_header: {}, metadata: {}}), 0);
    return "";
  }
  close_comm() {post({type: "comm_close", commId: this.comm_id});}
  hear(data: unknown) {this.message({content: {comm_id: this.comm_id, data}, buffers: [], metadata: {}});}
  gone() {this.close({content: {comm_id: this.comm_id, data: {}}});}
}

const style = document.createElement("style");
style.textContent = [materialColors, labVariables, lumino, noUiSlider, noUiSliderCustom, widgetsBase].map(text => (text as string).replace(/^@import .*$/gm, "")).join("\n");
document.head.prepend(style);  // before the page's own variables, which override these defaults
const manager = new HTMLManager();
const comms = new Map<string, Comm>();
const root = document.getElementById("widget") as HTMLElement;
let shown = false;

const open = async (commId: string, target: string, data: {state?: Record<string, unknown>}) => {
  if (comms.has(commId)) return;
  const comm = new Comm(commId, target);
  comms.set(commId, comm);
  (comm as unknown as {close: () => void}).close = () => comm.close_comm();
  try {
    await manager.handle_comm_open(comm as never, {content: {comm_id: commId, target_name: target, data}, buffers: [], metadata: {version: "2.1.0"}} as never);
  } catch (reason) {
    const module = (data.state?._model_module as string | undefined) ?? "this";
    console.error("widget", reason);
    post({type: "failed", message: String((reason as Error)?.stack ?? reason).slice(0, 400)});
    root.textContent = module.includes("plotly") ? "plotly widgets don't run here; use fig.show()" : `${module} widgets don't run here`;
  }
};

const show = async (modelId: string) => {
  if (shown) return;
  shown = true;
  try {
    const model = await manager.get_model(modelId);
    await manager.display_view(manager.create_view(model), root);
  } catch (reason) {
    const module = String((reason as Error).message ?? reason);
    root.textContent = /plotly/i.test(module) ? "plotly widgets don't run here; use fig.show()" : "This widget library doesn't run here";
  }
  post({type: "height", height: document.documentElement.scrollHeight});
};

window.addEventListener("message", event => {
  const message = event.data as {type: string; commId?: string; target?: string; data?: any; modelId?: string};
  if (message.type === "comm_open") void open(message.commId!, message.target ?? "jupyter.widget", message.data ?? {}).then(() => {if (message.commId === pending) void show(pending!);});
  else if (message.type === "comm_msg") comms.get(message.commId!)?.hear(message.data);
  else if (message.type === "comm_close") {comms.get(message.commId!)?.gone(); comms.delete(message.commId!);}
  else if (message.type === "view") {pending = message.modelId!; if (comms.has(pending)) void show(pending);}
});
let pending: string | null = null;
new ResizeObserver(() => post({type: "height", height: document.documentElement.scrollHeight})).observe(document.body);
post({type: "ready"});
