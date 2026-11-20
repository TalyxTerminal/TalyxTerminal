/* Multi-chart layouts, 1 to 8 charts, each drawn as a CSS grid (TradingView's layout menu, its arrangements per count). */

export interface Arrangement { id: string; count: number; columns: string; rows: string; areas: string[] }

const grid = (id: string, count: number, columns: number, rows: number, areas?: string[]): Arrangement => ({
  id, count, columns: `repeat(${columns}, minmax(0, 1fr))`, rows: `repeat(${rows}, minmax(0, 1fr))`,
  areas: areas ?? Array.from({ length: count }, (_, index) => `${Math.floor(index / columns) + 1} / ${(index % columns) + 1}`),
});

export const ARRANGEMENTS: Arrangement[] = [
  grid("1", 1, 1, 1),
  grid("2h", 2, 2, 1), grid("2v", 2, 1, 2),
  grid("3h", 3, 3, 1), grid("3v", 3, 1, 3),
  grid("3l", 3, 2, 2, ["1 / 1 / 3 / 2", "1 / 2", "2 / 2"]), grid("3r", 3, 2, 2, ["1 / 1", "2 / 1", "1 / 2 / 3 / 3"]),
  grid("3t", 3, 2, 2, ["1 / 1 / 2 / 3", "2 / 1", "2 / 2"]), grid("3b", 3, 2, 2, ["1 / 1", "1 / 2", "2 / 1 / 3 / 3"]),
  grid("4", 4, 2, 2), grid("4h", 4, 4, 1), grid("4v", 4, 1, 4),
  grid("4l", 4, 2, 3, ["1 / 1 / 4 / 2", "1 / 2", "2 / 2", "3 / 2"]), grid("4t", 4, 3, 2, ["1 / 1 / 2 / 4", "2 / 1", "2 / 2", "2 / 3"]),
  grid("5", 5, 3, 2, ["1 / 1 / 2 / 2", "1 / 2 / 2 / 4", "2 / 1", "2 / 2", "2 / 3"]), grid("5h", 5, 5, 1),
  grid("5l", 5, 2, 4, ["1 / 1 / 5 / 2", "1 / 2", "2 / 2", "3 / 2", "4 / 2"]),
  grid("6", 6, 3, 2), grid("6v", 6, 2, 3), grid("6h", 6, 6, 1),
  grid("7", 7, 4, 2, ["1 / 1 / 3 / 2", "1 / 2", "1 / 3", "1 / 4", "2 / 2", "2 / 3", "2 / 4"]),
  grid("8", 8, 4, 2), grid("8v", 8, 2, 4),
];

export const arrangement = (id: string): Arrangement => ARRANGEMENTS.find(item => item.id === id)!;
