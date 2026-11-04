import { describe, expect, it } from "vitest";
import type { WsField } from "../bridge/types";
import { toPayload } from "./fields";

const f = (key: string, data_type: string, required = false): WsField => ({ id: key, key, label: key.toUpperCase(), data_type, required, is_active: true, archived_at: null, options: [], category_ids: [], node_ids: [] });

describe("field payloads", () => {
  it("keeps money as strings and never as numbers", () => {
    const p = toPayload([f("total", "decimal"), f("fee", "currency")], { total: { text: "1250.500" }, fee: { text: "12.50", currency: "omr" } });
    expect(p.values).toEqual({ total: "1250.500", fee: { amount: "12.50", currency: "OMR" } });
    expect(typeof p.values.total).toBe("string");
  });
  it("leaves blanks absent and reports required ones as missing", () => {
    const p = toPayload([f("a", "short_text", true), f("b", "short_text"), f("c", "boolean", true), f("d", "multi_select", true)], { a: { text: "  " }, c: { bool: "" } });
    expect(p.values).toEqual({});
    expect(p.missing).toEqual(["A", "C", "D"]);
  });
  it("flags values that cannot be sent as written", () => {
    const p = toPayload([f("n", "number"), f("d", "decimal"), f("m", "currency")], { n: { text: "1.5" }, d: { text: "abc" }, m: { text: "5", currency: "OM" } });
    expect(p.invalid).toEqual(["N", "D", "M"]);
    expect(p.values).toEqual({});
  });
  it("sends yes/no, picked options and plain text", () => {
    const p = toPayload([f("b", "boolean"), f("o", "multi_select"), f("t", "short_text"), f("n", "number")], { b: { bool: "no" }, o: { picked: ["x", "y"] }, t: { text: " hi " }, n: { text: "42" } });
    expect(p.values).toEqual({ b: false, o: ["x", "y"], t: "hi", n: 42 });
  });
});
