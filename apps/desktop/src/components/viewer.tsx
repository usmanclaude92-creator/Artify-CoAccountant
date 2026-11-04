import { useEffect, useMemo, useState } from "react";
import { Alert, Button } from "@artify/ui";
import type { DocumentRow, Preview } from "../bridge/types";
import { useApp } from "../state/app";
import { describe } from "../lib/errors";
import { viewerKind } from "../lib/documents";

function toUrl(p: Preview): string {
  const bin = atob(p.data_base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: p.mime }));
}

/** In-app viewer. The bytes come from the Rust side, which has already verified them against the stored SHA-256; images and
 *  PDFs are shown here, everything else is opened with the default application. */
export function Viewer({ doc, version }: { doc: Pick<DocumentRow, "id" | "mime_type" | "extension" | "display_name">; version?: number | null }) {
  const { bridge, org, company, fail } = useApp();
  const kind = viewerKind(doc.mime_type, doc.extension);
  const [url, setUrl] = useState<string | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error">(kind === "none" || !org || !company ? "idle" : "loading");
  const [message, setMessage] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [fit, setFit] = useState(true);
  const [turn, setTurn] = useState(0);

  useEffect(() => {
    // the caller keys this component by document and version, so every state starts fresh
    if (kind === "none" || !org || !company) return;
    let live = true;
    let made: string | null = null;
    bridge.documentPreview(org.id, company.id, doc.id, version)
      .then((p) => { if (!live) return; made = toUrl(p); setUrl(made); setState("idle"); })
      .catch((e) => { if (!live) return; setState("error"); setMessage(fail(e) || describe(e)); });
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [bridge, org, company, doc.id, kind, version, fail]);

  const open = async () => {
    if (!org || !company) return;
    try { await bridge.documentOpen(org.id, company.id, doc.id, version); } catch (e) { setMessage(fail(e) || describe(e)); }
  };
  const full = () => { void document.getElementById("viewer-stage")?.requestFullscreen?.(); };

  const bar = useMemo(() => (
    <div className="d-actions" role="toolbar" aria-label="Viewer controls">
      {kind === "image" ? (
        <>
          <Button variant="secondary" className="a-btn--sm" onClick={() => { setFit(false); setZoom((z) => Math.max(0.25, +(z - 0.25).toFixed(2))); }} aria-label="Zoom out">−</Button>
          <span className="a-hint" aria-live="polite">{fit ? "Fit" : `${Math.round(zoom * 100)}%`}</span>
          <Button variant="secondary" className="a-btn--sm" onClick={() => { setFit(false); setZoom((z) => Math.min(6, +(z + 0.25).toFixed(2))); }} aria-label="Zoom in">+</Button>
          <Button variant="secondary" className="a-btn--sm" onClick={() => { setFit(true); setZoom(1); }}>Fit</Button>
          <Button variant="secondary" className="a-btn--sm" onClick={() => setTurn((t) => (t + 90) % 360)} aria-label="Rotate">Rotate</Button>
        </>
      ) : null}
      {url ? <Button variant="secondary" className="a-btn--sm" onClick={full}>Full screen</Button> : null}
      {url && kind === "image" ? <Button variant="secondary" className="a-btn--sm" onClick={() => window.print()}>Print</Button> : null}
      <Button variant="secondary" className="a-btn--sm" onClick={() => void open()}>Open in default app</Button>
    </div>
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ), [kind, url, fit, zoom]);

  if (kind === "none") {
    return (
      <div className="d-viewer">
        <Alert tone="info">{doc.display_name} cannot be previewed inside the app. Open it with the application installed on this computer.</Alert>
        {bar}
        {message ? <Alert tone="danger">{message}</Alert> : null}
      </div>
    );
  }
  return (
    <div className="d-viewer">
      {bar}
      {state === "loading" ? <p role="status">Loading the preview…</p> : null}
      {state === "error" ? <Alert tone="danger">{message ?? "The preview could not be loaded."}</Alert> : null}
      {message && state !== "error" ? <Alert tone="danger">{message}</Alert> : null}
      {url ? (
        <div id="viewer-stage" className="d-viewer__stage">
          {kind === "image" ? (
            <img src={url} alt={doc.display_name} className={fit ? "d-viewer__fit" : ""} style={fit ? { transform: `rotate(${turn}deg)` } : { transform: `scale(${zoom}) rotate(${turn}deg)`, transformOrigin: "top left" }} />
          ) : (
            <iframe title={`Preview of ${doc.display_name}`} src={url} className="d-viewer__pdf" />
          )}
        </div>
      ) : null}
      {kind === "pdf" && url ? <p className="a-hint">PDF pages, zoom and printing use the viewer built into the app’s web view. If it does not show, use “Open in default app”.</p> : null}
    </div>
  );
}
