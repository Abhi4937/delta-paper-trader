import { describe, expect, it } from "vitest";
import { safeNext } from "./safeNext";

describe("safeNext", () => {
  it("returns in-app paths", () => {
    expect(safeNext("?next=%2Fsettings%2Fkeys", "/chain")).toBe("/settings/keys");
    expect(safeNext("?next=/live", "/chain")).toBe("/live");
  });
  it("rejects anything that could leave the app", () => {
    for (const bad of ["https://evil.example", "//evil.example", "/\\evil.example", "evil", "javascript:alert(1)", ""]) {
      expect(safeNext(`?next=${encodeURIComponent(bad)}`, "/chain")).toBe("/chain");
    }
    expect(safeNext("", "/chain")).toBe("/chain");
  });
});
