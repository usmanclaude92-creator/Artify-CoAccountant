import type { WsField } from "../bridge/types";

/** What the user typed, per field key. Amounts stay strings all the way to the server (money is never a JS number). */
export type Raw = { text?: string; currency?: string; picked?: string[]; bool?: "yes" | "no" | "" };
export type RawValues = Record<string, Raw>;

export interface Payload {
  values: Record<string, unknown>;
  /** required fields with no value, by label; the upload is not queued while any are missing */
  missing: string[];
  /** typed values that cannot be sent as written, by label */
  invalid: string[];
}

const NUMBER = /^-?\d+$/;
const DECIMAL = /^-?\d+(\.\d+)?$/;

/** Turn typed values into the JSON the backend validates. Blank stays absent: missing data is never invented or defaulted here. */
export function toPayload(fields: WsField[], raw: RawValues): Payload {
  const values: Record<string, unknown> = {};
  const missing: string[] = [];
  const invalid: string[] = [];
  for (const f of fields) {
    const r = raw[f.key] ?? {};
    const t = (r.text ?? "").trim();
    let value: unknown;
    switch (f.data_type) {
      case "short_text": case "long_text": case "date": case "dropdown": case "reference":
        if (t) value = t;
        break;
      case "number":
        if (t) { if (NUMBER.test(t)) value = Number.parseInt(t, 10); else invalid.push(f.label); }
        break;
      case "decimal":
        if (t) { if (DECIMAL.test(t)) value = t; else invalid.push(f.label); }
        break;
      case "currency": {
        const c = (r.currency ?? "").trim().toUpperCase();
        if (t || c) { if (DECIMAL.test(t) && /^[A-Z]{3}$/.test(c)) value = { amount: t, currency: c }; else invalid.push(f.label); }
        break;
      }
      case "multi_select":
        if ((r.picked ?? []).length) value = r.picked;
        break;
      case "boolean":
        if (r.bool === "yes") value = true; else if (r.bool === "no") value = false;
        break;
      default:
        break;
    }
    if (value === undefined) { if (f.required) missing.push(f.label); } else values[f.key] = value;
  }
  return { values, missing, invalid };
}
