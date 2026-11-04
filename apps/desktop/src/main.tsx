import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { App } from "./App";
import { tauriBridge } from "./bridge/tauri";
import type { Bridge } from "./bridge/types";

// `VITE_BRIDGE=mock pnpm dev` previews the UI in a normal browser with DEMO data. Production builds never include it.
async function pickBridge(): Promise<{ bridge: Bridge; demo: string | null }> {
  if (import.meta.env.DEV && import.meta.env.VITE_BRIDGE === "mock") {
    const { createMockBridge, DEMO_BANNER } = await import("./bridge/mock");
    return { bridge: createMockBridge({ orgs: [{ id: "org-1", display_name: "Gulf Accounting Partners" }, { id: "org-2", display_name: "Independent Practice" }] }), demo: DEMO_BANNER };
  }
  return { bridge: tauriBridge, demo: null };
}

function Theme({ bridge }: { bridge: Bridge }) {
  useEffect(() => {
    const apply = (t: string) => { if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme; };
    const poll = () => bridge.settingsGet().then((s) => apply(s.theme)).catch(() => undefined);
    void poll();
    const t = setInterval(poll, 2000);
    return () => clearInterval(t);
  }, [bridge]);
  return null;
}

void pickBridge().then(({ bridge, demo }) => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      {demo ? <div className="d-demo" role="note">{demo}</div> : null}
      <Theme bridge={bridge} />
      <App bridge={bridge} />
    </StrictMode>,
  );
});
