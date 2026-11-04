"use client";
import { useState } from "react";
import { Button, Panel } from "@artify/ui";
import { useCan } from "@/components/platform/shell";
import { ActionDialog, DataTable, Field, PageHeader, useToast } from "@/components/kit";
import { fmtDateTime, papi, useApi, type S } from "@/lib/platform";

export default function Settings() {
  const can = useCan();
  const notify = useToast();
  const { data, error, loading, reload } = useApi<S["SettingOut"][]>("/settings");
  const [editing, setEditing] = useState<S["SettingOut"] | null>(null);
  const [value, setValue] = useState("");
  return (
    <>
      <PageHeader title="Platform Settings" crumbs={[{ label: "Platform Settings" }]} />
      <p className="a-hint">Defaults that change platform behaviour without a deployment. Every change needs a reason and is audited.</p>
      <Panel>
        <DataTable caption="Platform settings" loading={loading} error={error} onRetry={reload} rows={data ?? undefined} empty="No settings." rowKey={(s) => s.key}
          columns={[
            { key: "k", header: "Setting", render: (s) => <><strong>{s.key}</strong><span className="a-hint" style={{ display: "block" }}>{s.description}</span></> },
            { key: "v", header: "Value", render: (s) => s.value ?? <span className="a-hint">default ({String(s.default)})</span> },
            { key: "u", header: "Updated", render: (s) => fmtDateTime(s.updated_at) },
            { key: "a", header: "", render: (s) => can("platform.settings.manage") ? <Button variant="secondary" className="a-btn--sm" onClick={() => { setValue(String(s.value ?? s.default ?? "")); setEditing(s); }}>Change</Button> : null },
          ]} />
      </Panel>
      <ActionDialog open={!!editing} title={`Change ${editing?.key ?? ""}`} submitLabel="Save setting"
        onSubmit={async ({ reason }) => { await papi(`/settings/${editing!.key}`, { method: "PUT", body: { value: editing!.kind === "integer" ? Number(value) : value, reason } }); notify("Setting saved."); setEditing(null); reload(); }}
        onClose={() => setEditing(null)}>
        <Field label="New value" hint={editing?.kind === "integer" ? `Whole number between ${editing.minimum} and ${editing.maximum}.` : undefined}>
          {(i) => <input id={i} value={value} onChange={(e) => setValue(e.target.value)} required />}
        </Field>
      </ActionDialog>
    </>
  );
}
