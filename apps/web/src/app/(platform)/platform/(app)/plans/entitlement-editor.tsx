"use client";
import { useEffect, useState } from "react";
import { Field } from "@/components/kit";
import { papi, type S } from "@/lib/platform";

export type EntState = Record<string, { mode: "unset" | "number" | "unlimited" | "flag"; value: string; flag: boolean }>;

export const toBody = (s: EntState): Record<string, unknown> => {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(s)) {
    if (v.mode === "number") out[k] = { value: Number(v.value) };
    else if (v.mode === "unlimited") out[k] = { unlimited: true };
    else if (v.mode === "flag") out[k] = { enabled: v.flag };
  }
  return out;
};
export const fromVersion = (e: S["VersionOut"]["entitlements"] | undefined, cat: S["CatalogEntry"][]): EntState => {
  const s: EntState = {};
  for (const c of cat) {
    const v = e?.[c.key];
    s[c.key] = !v ? { mode: "unset", value: "", flag: false } : v.unlimited ? { mode: "unlimited", value: "", flag: false }
      : v.enabled !== null && v.enabled !== undefined ? { mode: "flag", value: "", flag: v.enabled } : { mode: "number", value: String(v.value ?? ""), flag: false };
  }
  return s;
};

/** One row per catalog key: numbers, unlimited, or feature flags. The catalog comes from the API, never from this file. */
export function EntitlementEditor({ value, onChange }: { value: EntState; onChange: (s: EntState) => void }) {
  const [cat, setCat] = useState<S["CatalogEntry"][]>([]);
  useEffect(() => { papi<S["CatalogEntry"][]>("/entitlements").then(setCat).catch(() => setCat([])); }, []);
  return (
    <fieldset className="grid gap-3">
      <legend className="font-semibold">Limits, allowances and features</legend>
      {cat.map((c) => {
        const v = value[c.key] ?? { mode: "unset", value: "", flag: false };
        const set = (p: Partial<typeof v>) => onChange({ ...value, [c.key]: { ...v, ...p } });
        const isFlag = c.value_type === "bool";
        return (
          <div key={c.key} className="a-toolbar" style={{ borderBlockEnd: "1px solid var(--color-border)", paddingBlockEnd: "0.5rem" }}>
            <div style={{ flex: "1 1 16rem" }}>
              <strong>{c.key}</strong>{c.unit ? ` (${c.unit})` : ""}
              <span className="a-hint" style={{ display: "block" }}>{c.description}{c.enforced_now ? "" : " — stored now, enforced when the feature ships."}</span>
            </div>
            <Field label={`${c.key} setting`}>
              {(id) => (
                <select id={id} value={v.mode} onChange={(e) => set({ mode: e.target.value as typeof v.mode })}>
                  <option value="unset">Not included (denied)</option>
                  {isFlag ? <option value="flag">Feature flag</option> : <><option value="number">Limit</option><option value="unlimited">Unlimited</option></>}
                </select>
              )}
            </Field>
            {v.mode === "number" ? <Field label={`${c.key} limit`}>{(id) => <input id={id} type="number" min={0} value={v.value} onChange={(e) => set({ value: e.target.value })} required />}</Field> : null}
            {v.mode === "flag" ? <label className="flex items-center gap-2"><input type="checkbox" checked={v.flag} onChange={(e) => set({ flag: e.target.checked })} /> Enabled</label> : null}
          </div>
        );
      })}
    </fieldset>
  );
}
