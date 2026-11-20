import { expect, it } from "vitest";
import type { AgentCard, Turn } from "../data/types";
import { chatChoicesUpdate, chatItems, newChatChoices } from "./live";

it("shows one disclosure per turn and expands all of that turn's work in time order", () => {
  const turn = (id: string, at: number): Turn => ({id, createdAt:at, startedAt:at, finishedAt:at+12,
    state:"completed", conversationId:"chat", workspaceId:"ws", text:"Question", error:null, wake:{kind:"user"}, inbox:[]});
  const thought = (turnId: string, id: string, at: number): AgentCard => ({type:"thinking", turnId, id,
    text:id, startedAt:at, finishedAt:at+1, createdAt:at, updatedAt:at+1, orderKey:id});
  const tool: AgentCard = {id:"calls", type:"process_group", turnId:"one", title:"Working", createdAt:2,
    updatedAt:10, orderKey:"calls", steps:[2, 9].map(at=>({id:"step"+at, title:"Read", details:{},
      state:at === 9 ? "failed" : "completed", startedAt:at, finishedAt:at+1, orderKey:String(at), createdAt:at, updatedAt:at+1}))};
  const items = chatItems({turns:[turn("one",0),turn("two",20)],
    cards:[tool, thought("one","reason",5), thought("two","next",22)]});
  expect(items.map(item=>item.id)).toEqual(["work_one","work_two"]);
  const first = items[0];
  expect(first.kind === "work" && [first.seconds,first.running,first.steps.map(step=>step.id)]).toEqual([12,false,["step2","reason","step9"]]);
});

it("leaves the model's closing text to the answer and folds thinking to one line", () => {
  const turn: Turn = {id:"one", createdAt:0, startedAt:0, finishedAt:9, state:"completed", conversationId:"chat",
    workspaceId:"ws", text:"Question", error:null, wake:{kind:"user"}, inbox:[]};
  const model: AgentCard = {id:"model_one", type:"process_group", turnId:"one", title:"Working", createdAt:1, updatedAt:8,
    orderKey:"m", steps:[{id:"note", title:"Model response", details:{text:"See [the page](https://x.test)"},
      state:"completed", startedAt:8, finishedAt:8, orderKey:"8", createdAt:8, updatedAt:8}]};
  const thinking: AgentCard = {type:"thinking", turnId:"one", id:"t", text:"Long reasoning", startedAt:2, finishedAt:3,
    createdAt:2, updatedAt:3, orderKey:"t"};
  const answer = {id:"a", type:"answer", role:"assistant", turnId:"one", text:"See the page", modelStepId:"note", references:[], sources:[]} as unknown as AgentCard;
  const progress = {...model, id:"model_note", steps:[{...model.steps[0], id:"progress", details:{text:"I found two records."}, startedAt:1}]};
  for (const finalText of ["See the page", "See [the page](https://x.test)"]) {
    const [work] = chatItems({turns:[turn], cards:[progress, model, thinking, {...answer, text:finalText} as AgentCard]});
    expect(work.kind === "work" && work.steps.map(step => [step.id, step.note ?? false, step.detail]))
      .toEqual([["progress", true, undefined], ["t", false, "Long reasoning"]]);
  }
});

it("a new chat takes the thinking level the settings keep for its default model", () => {
  const models = [{id:"flash",name:"Research model",connection:"DeepSeek",window:128000,levels:["low","high"],defaultLevel:"high"},
    {id:"other",name:"other",connection:"X",window:8000,levels:[],defaultLevel:""}];
  const defaults = {modelId:"flash",defaultPermission:"read_only",summaryModelId:null,childModelId:null,childThinking:null,
    childrenEnabled:false,memoryEnabled:true,modelThinking:{flash:"low"}} as const;
  expect(newChatChoices({...defaults, modelThinking:{...defaults.modelThinking}}, models))
    .toEqual({model:"flash",thinking:"low",permission:"read_only",subagents:false,plan:false});
  // Nothing kept for the model: its own default level.
  expect(newChatChoices({...defaults, modelThinking:{}}, models).thinking).toBe("high");
  expect(chatChoicesUpdate(newChatChoices({...defaults, modelThinking:{flash:"low"}}, models)).thinking).toEqual({effort:"low"});
});
it("a message added to a running turn sits between the work before and after it; one the stopped turn never read says so", () => {
  const turn = (state: Turn["state"], inbox: string[]): Turn => ({id:"t", createdAt:0, startedAt:0, finishedAt:50, state, conversationId:"chat",
    workspaceId:"ws", text:"Monthly returns for five names", error:null, wake:{kind:"user"}, inbox:inbox.map(messageId => ({messageId}))});
  const user = (id: string, at: number, text: string) => ({id, type:"answer", role:"user", turnId:"t", text, references:[], sources:[],
    view:null, attachments:[], createdAt:at, updatedAt:at, orderKey:String(at)}) as unknown as AgentCard;
  const steps: AgentCard = {id:"process_t", type:"process_group", turnId:"t", title:"Working", createdAt:1, updatedAt:40, orderKey:"p",
    steps:[5, 30].map(at => ({id:"s"+at, title:"Read", details:{}, state:"completed", startedAt:at, finishedAt:at+1, orderKey:String(at), createdAt:at, updatedAt:at+1}))};
  const cards = [user("user_t", 0, "Monthly returns for five names"), steps, user("user_x", 12, "Only BTC and NVDA")];
  const items = chatItems({turns:[turn("cancelled", [])], cards});
  expect(items.map(item => item.id)).toEqual(["user_t", "work_t", "user_x", "work_t_1"]);
  const [, before, , after] = items;
  expect(before.kind === "work" && [before.steps.map(s => s.id), before.interrupted]).toEqual([["s5"], false]);
  expect(after.kind === "work" && [after.steps.map(s => s.id), after.interrupted]).toEqual([["s30"], true]);
  const unread = chatItems({turns:[turn("cancelled", ["user_x"])], cards}).find(item => item.id === "user_x");
  expect(unread?.kind === "user" && unread.undelivered).toBe(true);
});

it("marks a turn the chat's schedule started and says when a waiting approval expires", () => {
  const turn: Turn = {id:"t", createdAt:3600, startedAt:3600, finishedAt:null, state:"waiting_approval", conversationId:"chat",
    workspaceId:"ws", text:"Morning check", error:null, wake:{kind:"schedule", routine:"Morning check"}, inbox:[]};
  const approval = {id:"approval_1", type:"approval", turnId:"t", version:1, conversationId:"chat", childRunId:null, toolCallId:"c",
    callId:"c", action:"example.write_note", input:{}, proposal:{risks:["write"]}, state:"waiting", decision:null, reason:"",
    createdAt:3601, expiresAt:5400} as unknown as AgentCard;
  const items = chatItems({turns:[turn], cards:[approval]});
  expect(items.map(item => item.kind)).toEqual(["wake", "approval"]);
  expect(items[0].kind === "wake" && items[0].wake).toEqual({kind:"schedule", routine:"Morning check"});
  expect(items[1].kind === "approval" && items[1].expires).toBe(5400);
});
