import type { Instrument } from "@talyx/vocab";
import type { OverlayPoint, OverlaySettings } from "./types";

export interface PositionUnits { pointValue: number | null; currency: string | null; reason: string | null; }
export type PositionInstrument=Pick<Instrument,"marketType"|"assetClass"|"contractOrientation"|"quote"> & {contractSize?:string|number|null;currency?:string|null};

/** Quote units and explicit linear contract multipliers are authoritative. Pip,
 * tick and inverse/quanto valuation need metadata that Instrument does not carry. */
export function positionUnits(instrument?:PositionInstrument): PositionUnits {
  const currency=instrument?.quote??instrument?.currency??null;
  if (!instrument) return {pointValue:null,currency,reason:"Instrument units are unavailable."};
  if (instrument.contractOrientation==="linear") {
    const pointValue=Number(instrument.contractSize);
    if(Number.isFinite(pointValue)&&pointValue>0) return {pointValue,currency,reason:null};
  }
  if(instrument.marketType==="spot" && instrument.contractOrientation==="none" && ["crypto","equity","tokenized_equity","fund"].includes(instrument.assetClass)) return {pointValue:1,currency,reason:null};
  return {pointValue:null,currency,reason:"Position quantity needs the instrument's price unit value."};
}

export interface PositionMetrics {
  entry:number; target:number; stop:number; reward:number; risk:number; rewardRisk:number|null;
  riskAmount:number|null; quantity:number|null; profit:number|null; loss:number|null;
  targetBalance:number|null; stopBalance:number|null;
}
export function positionMetrics(type:"long_position"|"short_position",points:readonly OverlayPoint[],settings?:OverlaySettings,pointValue?:number|null):PositionMetrics {
  const entry=points[0]?.value??0,target=points[1]?.value??entry,stop=points[2]?.value??entry;
  const direction=type==="long_position"?1:-1,reward=(target-entry)*direction,risk=(entry-stop)*direction;
  const account=settings?.accountSize,lot=settings?.lotSize??1,leverage=settings?.leverage??1;
  const configuredRisk=settings?.risk??1;
  const riskAmount=account!==undefined?(settings?.riskMode==="amount"?configuredRisk:account*configuredRisk/100):null;
  let quantity:number|null=null;
  if(account!==undefined&&account>0&&riskAmount!==null&&riskAmount>=0&&risk>0&&reward>=0&&entry>0&&pointValue!=null&&Number.isFinite(pointValue)&&pointValue>0&&lot>0&&leverage>0) {
    const riskQuantity=riskAmount/(risk*pointValue*lot);
    const leverageQuantity=account*leverage/(entry*pointValue*lot);
    const scale=10**(settings?.qtyPrecision??2);
    quantity=Math.floor(Math.min(riskQuantity,leverageQuantity)*scale)/scale;
  }
  const profit=quantity===null||pointValue==null?null:reward*quantity*pointValue*lot;
  const loss=quantity===null||pointValue==null?null:risk*quantity*pointValue*lot;
  return {entry,target,stop,reward,risk,rewardRisk:risk>0?reward/risk:null,riskAmount,quantity,profit,loss,targetBalance:account!==undefined&&profit!==null?account+profit:null,stopBalance:account!==undefined&&loss!==null?account-loss:null};
}
