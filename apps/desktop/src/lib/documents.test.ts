import { describe, expect, it } from "vitest";
import type { WsField, WsNode } from "../bridge/types";
import { applicableFields, importProblem, nodeChain, uploadTargets, viewerKind, fmtPct } from "./documents";

const f = (key: string, extra: Partial<WsField> = {}): WsField => ({ id: key, key, label: key, data_type: "text", required: false, is_active: true, archived_at: null, options: [], category_ids: [], node_ids: [], ...extra });
const n = (id: string, parent: string | null, extra: Partial<WsNode> = {}): WsNode => ({ id, parent_id: parent, node_type: "folder", key: id, name: id, display_name: null, description: "", position: 0, depth: 1, is_active: true, archived_at: null, ...extra });

describe("document helpers", () => {
  it("applies fields everywhere, to a category, or to a folder and its subfolders", () => {
    const fields = [f("everywhere"), f("only_cat", { category_ids: ["c1"] }), f("only_folder", { node_ids: ["a"] }), f("off", { is_active: false }), f("gone", { archived_at: "2026-01-01" })];
    expect(applicableFields(fields, null, ["z"]).map((x) => x.key)).toEqual(["everywhere"]);
    expect(applicableFields(fields, "c1", ["z"]).map((x) => x.key)).toEqual(["everywhere", "only_cat"]);
    expect(applicableFields(fields, null, ["a", "a-child"]).map((x) => x.key)).toEqual(["everywhere", "only_folder"]);
    expect(applicableFields(undefined, null, [])).toEqual([]);
  });
  it("walks a node's chain root-first and survives a broken parent link", () => {
    const nodes = [n("root", null), n("mid", "root"), n("leaf", "mid"), n("orphan", "missing")];
    expect(nodeChain(nodes, "leaf")).toEqual(["root", "mid", "leaf"]);
    expect(nodeChain(nodes, "orphan")).toEqual(["orphan"]);
    expect(nodeChain(nodes, "nope")).toEqual([]);
    const loop = [n("a", "b"), n("b", "a")];
    expect(nodeChain(loop, "a").length).toBe(2);
  });
  it("offers only folders the server says the user may upload to", () => {
    const nodes = [n("a", null, { can: { upload: true } }), n("b", null, { can: { upload: false } }), n("c", null), n("d", null, { can: { upload: true }, archived_at: "2026-01-01" })];
    expect(uploadTargets(nodes).map((x) => x.id)).toEqual(["a"]);
  });
  it("shows only images and PDFs in the app", () => {
    expect(viewerKind("application/pdf", "pdf")).toBe("pdf");
    expect(viewerKind("image/png", "png")).toBe("image");
    expect(viewerKind("image/tiff", "tif")).toBe("none");
    expect(viewerKind("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")).toBe("none");
    expect(viewerKind("text/html", "html")).toBe("none");
  });
  it("explains failures in plain language and falls back to the server text", () => {
    expect(importProblem("network_unavailable", null)).toMatch(/kept/);
    expect(importProblem("storage_quota", null)).toMatch(/nothing was deleted/);
    expect(importProblem("something_new", "Server said this")).toBe("Server said this");
    expect(importProblem(null, null)).toMatch(/did not finish/);
  });
  it("clamps progress", () => {
    expect(fmtPct(50, 100)).toBe(50);
    expect(fmtPct(500, 100)).toBe(100);
    expect(fmtPct(1, 0)).toBe(0);
  });
});
