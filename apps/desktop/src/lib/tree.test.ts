import { describe, expect, it } from "vitest";
import { node } from "../bridge/mock";
import { buildTree, countNodes, filterTree, findNode, labelOf } from "./tree";

const sample = [
  node("s1", null, "Finance", { position: 1 }), node("s0", null, "Sales", { position: 0 }),
  node("f1", "s1", "Bank Statements"), node("f2", "f1", "Bank of Muscat"), node("old", "s1", "Old", { archived_at: "2026-01-01T00:00:00Z" }),
  node("orphan", "hidden-parent", "Visible child"),
];

describe("workspace tree", () => {
  it("orders by position then label and never invents nodes", () => {
    const t = buildTree(sample, false);
    expect(t.map((x) => labelOf(x.node))).toEqual(["Sales", "Visible child", "Finance"]);
    expect(countNodes(t)).toBe(5);
    expect(t[2]?.children.map((c) => c.node.name)).toEqual(["Bank Statements"]);
  });
  it("hides archived items unless asked, and keeps stable ids", () => {
    expect(countNodes(buildTree(sample, true))).toBe(6);
    expect(findNode(buildTree(sample, true), "old")?.node.archived_at).toBeTruthy();
    expect(findNode(buildTree(sample, false), "old")).toBeNull();
  });
  it("roots nodes whose parent was not delivered (permission-hidden parent)", () => {
    expect(buildTree(sample, false).some((t) => t.node.id === "orphan")).toBe(true);
  });
  it("search keeps ancestors of matches and reports what to expand", () => {
    const { forest, expand } = filterTree(buildTree(sample, false), "muscat");
    expect(forest.map((t) => t.node.id)).toEqual(["s1"]);
    expect([...expand].sort()).toEqual(["f1", "s1"]);
    expect(filterTree(buildTree(sample, false), "zzz").forest).toEqual([]);
    expect(filterTree(buildTree(sample, false), "  ").forest.length).toBe(3);
  });
  it("uses the display name when there is one", () => {
    expect(labelOf(node("x", null, "name", { display_name: "Shown" }))).toBe("Shown");
  });
});
