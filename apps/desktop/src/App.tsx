import { useCallback, useEffect, useState } from "react";
import type { AppInfo, Bridge, Identity } from "./bridge/types";
import { describe } from "./lib/errors";
import { Booting, Login } from "./screens/auth";
import { OrgPicker } from "./screens/companies";
import { Shell } from "./shell";
import { Session, useApp } from "./state/app";

function Gate() {
  const { ready, org } = useApp();
  return !ready ? <Booting error={null} onRetry={() => undefined} /> : org ? <Shell /> : <OrgPicker />;
}

type Phase = { name: "booting"; error: string | null } | { name: "login"; notice: string | null } | { name: "in"; identity: Identity };

export function App({ bridge }: { bridge: Bridge }) {
  const [phase, setPhase] = useState<Phase>({ name: "booting", error: null });
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    bridge.appInfo().then((i) => live && setInfo(i)).catch(() => undefined);
    bridge.authRestore()
      .then((id) => live && setPhase(id ? { name: "in", identity: id } : { name: "login", notice: null }))
      .catch((e) => live && setPhase({ name: "booting", error: describe(e) }));
    return () => { live = false; };
  }, [bridge, attempt]);

  const signedOut = useCallback((message?: string) => setPhase({ name: "login", notice: message ?? null }), []);

  if (phase.name === "booting") return <Booting error={phase.error} onRetry={() => { setPhase({ name: "booting", error: null }); setAttempt((a) => a + 1); }} />;
  if (phase.name === "login") return <Login bridge={bridge} info={info} notice={phase.notice} onSignedIn={(identity) => setPhase({ name: "in", identity })} />;
  return (
    <Session bridge={bridge} identity={phase.identity} onSignedOut={signedOut}>
      <Gate />
    </Session>
  );
}
