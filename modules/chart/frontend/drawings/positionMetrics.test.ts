import {describe,it,expect} from "vitest";
import {positionMetrics,positionUnits} from "./positionMetrics";
import type {Instrument} from "@talyx/vocab";
const points=[{timestamp:1,value:100},{timestamp:2,value:120},{timestamp:3,value:90}];
describe("position sizing uses explicit price units and keeps risk limits",()=>{
 it("sizes spot risk and rounds down without exceeding exposure",()=>{
  expect(positionMetrics("long_position",points,{accountSize:10000,risk:1,lotSize:1,leverage:1,qtyPrecision:2},1)).toMatchObject({quantity:10,profit:200,loss:100,targetBalance:10200,stopBalance:9900,rewardRisk:2});
  expect(positionMetrics("long_position",points,{accountSize:100,risk:100,riskMode:"amount",leverage:1,qtyPrecision:0},1)).toMatchObject({quantity:1,loss:10});
 });
 it("handles short direction, real contract multipliers and absent metadata",()=>{
  expect(positionMetrics("short_position",[points[0],points[2],points[1]],{accountSize:10000,risk:1,leverage:2,lotSize:10,qtyPrecision:3},5)).toMatchObject({quantity:.1,profit:50,loss:100,rewardRisk:.5});
  expect(positionMetrics("long_position",points,{accountSize:10000,risk:1},null)).toMatchObject({quantity:null,profit:null,loss:null,riskAmount:100});
  expect(positionMetrics("long_position",[points[0],points[1],points[0]],{accountSize:10000},1).quantity).toBeNull();
  const instrument={marketType:"spot",assetClass:"equity",contractOrientation:"none",quote:"USD"} as Instrument;
  expect(positionUnits(instrument)).toEqual({pointValue:1,currency:"USD",reason:null});
  expect(positionUnits({...instrument,marketType:"future",contractOrientation:"linear",contractSize:"50"}).pointValue).toBe(50);
  expect(positionUnits({...instrument,marketType:"future",contractOrientation:"inverse",contractSize:"50"}).pointValue).toBeNull();
  expect(positionUnits({...instrument,assetClass:"index"}).pointValue).toBeNull();
 });
});
