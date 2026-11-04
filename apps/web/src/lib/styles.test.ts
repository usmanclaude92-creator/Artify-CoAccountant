import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../../../../packages/ui/src/styles.css", import.meta.url), "utf8");
const stack = (name: string) => css.match(new RegExp(`--${name}:\\s*([^;]+);`))?.[1] ?? "";

describe("font stacks", () => {
  it("keeps Arabic faces out of the Latin stack (they render Latin text in a serif style)", () => {
    expect(stack("font-sans")).not.toMatch(/arabic|naskh|kufi/i);
  });
  it("ends the Latin stack with the generic sans-serif and includes concrete sans faces first", () => {
    const parts = stack("font-sans").split(",").map((p) => p.trim().replace(/"/g, ""));
    expect(parts.at(-1)).toBe("sans-serif");
    expect(parts).toEqual(expect.arrayContaining(["system-ui", "Roboto", "Arial"]));
  });
  it("applies the Arabic stack only on Arabic pages", () => {
    expect(css).toMatch(/html\[lang="ar"\] body \{ font-family: var\(--font-arabic\); \}/);
  });
});
