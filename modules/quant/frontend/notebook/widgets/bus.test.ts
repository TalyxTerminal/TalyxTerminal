import { describe, expect, it } from "vitest";
import { WidgetBus } from "./bus";

describe("WidgetBus", () => {
  it("keeps a model's open with its later updates folded in, so a frame that starts late builds it as it is now", () => {
    const bus = new WidgetBus(() => undefined);
    bus.receive({type: "comm", msgType: "comm_open", commId: "box", data: {state: {_model_name: "VBoxModel", children: []}}, target: "jupyter.widget"});
    bus.receive({type: "comm", msgType: "comm_msg", commId: "box", data: {method: "update", state: {children: ["IPY_MODEL_slider", "IPY_MODEL_out"]}}});
    bus.receive({type: "comm", msgType: "comm_msg", commId: "box", data: {method: "custom", content: {}}});
    expect(bus.opened.get("box")?.data).toEqual({state: {_model_name: "VBoxModel", children: ["IPY_MODEL_slider", "IPY_MODEL_out"]}});
  });
});
