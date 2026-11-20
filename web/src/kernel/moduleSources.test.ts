import { describe, expect, it } from "vitest";
import { fsAddress } from "../../scripts/module-sources.mjs";

describe("dev module sources", () => {
  it("percent-encodes a repository path with non-ASCII characters and spaces", () => {
    const address = fsAddress("Z:\\项目\\Talyx Terminal\\modules\\chart\\frontend\\index.tsx", "?v=1");
    expect(address).toBe("/@fs/Z:/%E9%A1%B9%E7%9B%AE/Talyx%20Terminal/modules/chart/frontend/index.tsx?v=1");
    expect(/^[\x21-\x7e]+$/.test(address)).toBe(true);
    expect(decodeURI(address.split("?")[0])).toBe("/@fs/Z:/项目/Talyx Terminal/modules/chart/frontend/index.tsx");
  });

  it("keeps a POSIX path absolute under /@fs/", () => {
    expect(fsAddress("/home/user/talyx/modules/agent/frontend/a.ts")).toBe("/@fs/home/user/talyx/modules/agent/frontend/a.ts");
  });
});
