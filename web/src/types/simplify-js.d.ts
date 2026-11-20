declare module "simplify-js" {
  export interface SimplifyPoint {
    x: number;
    y: number;
  }

  export default function simplify<T extends SimplifyPoint>(
    points: readonly T[],
    tolerance?: number,
    highestQuality?: boolean,
  ): T[];
}
