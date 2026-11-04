import { NextResponse, type NextRequest } from "next/server";
import { apiWithSession } from "@/lib/server-session";

/**
 * Backend-for-frontend proxy. The browser never sees API tokens: this handler attaches them from
 * HttpOnly cookies. Tenant routes: /bff/<api path>; platform routes: /bff/platform/<api path>.
 * Mutating requests must carry the custom header below (a cross-site form post cannot) and a
 * same-origin Origin; together with SameSite=Strict cookies this is the CSRF defence.
 */
async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const mutating = !["GET", "HEAD"].includes(req.method);
  if (mutating) {
    const origin = req.headers.get("origin");
    if (req.headers.get("x-artify-csrf") !== "1" || (origin && origin !== req.nextUrl.origin)) {
      return NextResponse.json({ code: "csrf", detail: "Cross-site request rejected." }, { status: 403 });
    }
  }
  const apiPath = "/" + path.join("/") + req.nextUrl.search;
  const realm = path[0] === "platform" ? "platform" : "tenant";
  const headers: Record<string, string> = {};
  const org = req.headers.get("x-organization-id");
  if (org) headers["x-organization-id"] = org;
  const idem = req.headers.get("idempotency-key");
  if (idem) headers["idempotency-key"] = idem;
  const body = mutating ? await req.text() : undefined;
  const res = await apiWithSession(realm, apiPath, { method: req.method, body: body || undefined, headers });
  const text = await res.text();
  return new NextResponse(text || null, {
    status: res.status,
    headers: { "content-type": res.headers.get("content-type") ?? "application/json", "cache-control": "no-store" },
  });
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
