import { describe, expect, it } from "vitest";
import { isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { installLanguage, intlLocale, language, translator } from "@talyx/sdk";

describe("the translator", () => {
  installLanguage("zh-CN", {
    words: { "Fetching prices": "正在获取价格", "{done} of {total}": "{total} 中的 {done}", "Close": "关闭",
      "{count} from {source}": "来自 {source} 的 {count}", "@context": { price: { Close: "收盘价" } } },
  });
  const t = translator("words");

  it("translates with its owner's dictionary and fills placeholders in the translation's order", () => {
    expect(language()).toBe("zh-CN");
    expect(intlLocale()).toBe("zh-CN");
    expect(t("Fetching prices")).toBe("正在获取价格");
    expect(t("{done} of {total}", { done: 3, total: 10 })).toBe("10 中的 3");
  });

  it("keeps English for a text the dictionary lacks, and for an owner without one", () => {
    expect(t("Saving the code")).toBe("Saving the code");
    expect(translator("other")("Fetching prices")).toBe("Fetching prices");
    expect(t("Use {symbol} here")).toBe("Use {symbol} here");
  });

  it("reads a text by its context", () => {
    expect(t("Close")).toBe("关闭");
    expect(t.c("price", "Close")).toBe("收盘价");
    expect(t.c("button", "Close")).toBe("Close");
  });

  it("puts elements in placeholders", () => {
    const node = t.rich("{count} from {source}", { count: <b>3</b>, source: "Market" });
    expect(isValidElement(node)).toBe(true);
    expect(renderToStaticMarkup(<>{node}</>)).toBe("来自 Market 的 <b>3</b>");
  });
});
