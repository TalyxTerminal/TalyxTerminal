import { expect, it } from "vitest";
import { NA_COLOR, drawnColor, pineColor, scriptColor } from "./pinePrimitives";

const RED = "#FF0000FF", GREEN = "#00FF00FF";

it("draws a row in the user's colour, the script's when they chose none, and nothing where the script's is na", () => {
  // MACD's histogram: a colour per bar.
  const rows = [RED, GREEN, NA_COLOR, null];
  const scripts = rows.map(row => scriptColor(row, RED));
  expect(scripts).toEqual([RED, GREEN, NA_COLOR, RED]); // null: the meta's colour (before the colour varied)
  expect(scripts.map(script => drawnColor("#123456", script))).toEqual(["#123456", "#123456", null, "#123456"]);
  // Reset to defaults clears the user's colour: the script's, per bar, come back.
  expect(scripts.map(script => drawnColor(undefined, script))).toEqual([pineColor(RED), pineColor(GREEN), null, pineColor(RED)]);
  expect(drawnColor(null, undefined)).toBeUndefined(); // the script names none: the output's own colour
});

it("reads a part of a row with several colours, falling back to the meta's for a part the row leaves out", () => {
  expect(scriptColor([NA_COLOR, GREEN], RED, 0)).toBe(NA_COLOR);
  expect(scriptColor([null, GREEN], RED, 0)).toBe(RED);
  expect(scriptColor([1, 0, GREEN, RED], undefined, 2)).toBe(GREEN); // a gradient fill's top colour
  expect(scriptColor(undefined, NA_COLOR)).toBe(NA_COLOR); // plot(x, color=na): every bar
  expect(pineColor(NA_COLOR)).toBeNull();
});
