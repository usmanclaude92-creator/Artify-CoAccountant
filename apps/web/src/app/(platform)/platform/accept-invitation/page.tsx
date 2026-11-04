"use client";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Alert, Button, Panel, TextField } from "@artify/ui";
import { bff } from "@/lib/client";
import { errorText } from "@/lib/platform";

function Form() {
  const token = useSearchParams().get("token") ?? "";
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [err, setErr] = useState<string | null>(null);
  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-6 px-4 py-8">
      <span className="text-lg font-semibold">Artify Super Admin</span>
      <Panel>
        <h1 className="mb-4 text-xl font-semibold">Accept your invitation</h1>
        {state === "done" ? (
          <div className="grid gap-3">
            <Alert tone="success">Your account is ready. At your first sign-in you will be asked to set up two-factor authentication.</Alert>
            <a className="a-link" href="/platform/login">Go to sign-in</a>
          </div>
        ) : !token ? <Alert tone="danger">This link is incomplete. Open the invitation e-mail again.</Alert> : (
          <form className="grid gap-4" onSubmit={async (e) => {
            e.preventDefault(); setState("busy"); setErr(null);
            try { await bff("/bff/platform/auth/invitations/accept", { method: "POST", body: { token, display_name: name, password } }); setState("done"); }
            catch (ex) { setErr(errorText(ex)); setState("idle"); }
          }}>
            <TextField label="Your name" value={name} onChange={(e) => setName(e.target.value)} required minLength={2} autoComplete="name" />
            <TextField label="Choose a password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required autoComplete="new-password" hint="Use a long, unique password." />
            {err ? <Alert tone="danger">{err}</Alert> : null}
            <Button type="submit" disabled={state === "busy"}>{state === "busy" ? "Creating account…" : "Create account"}</Button>
          </form>
        )}
      </Panel>
    </main>
  );
}
export default function AcceptPlatformInvitation() {
  return <Suspense><Form /></Suspense>;
}
