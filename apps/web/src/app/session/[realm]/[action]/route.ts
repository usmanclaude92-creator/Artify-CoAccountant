import { NextResponse, type NextRequest } from "next/server";
import { API_URL, apiWithSession, clearTokens, getTokens, isRealm, setTokens, type TokenBundle } from "@/lib/server-session";

/** Session endpoints that set/clear the token cookies: login, mfa, confirm-mfa, logout. */
export async function POST(req: NextRequest, ctx: { params: Promise<{ realm: string; action: string }> }) {
  const { realm, action } = await ctx.params;
  if (!isRealm(realm)) return NextResponse.json({ code: "not_found" }, { status: 404 });
  const origin = req.headers.get("origin");
  if (req.headers.get("x-artify-csrf") !== "1" || (origin && origin !== req.nextUrl.origin)) {
    return NextResponse.json({ code: "csrf", detail: "Cross-site request rejected." }, { status: 403 });
  }
  const base = realm === "platform" ? "/platform/auth" : "/auth";
  const raw = await req.text();
  const forward = (path: string, body?: string, access?: string) =>
    fetch(`${API_URL}${path}`, {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json", ...(access ? { authorization: `Bearer ${access}` } : {}) },
      body,
    });
  const passthrough = async (res: Response) =>
    new NextResponse(await res.text(), { status: res.status, headers: { "content-type": "application/json" } });

  if (action === "login") {
    const res = await forward(`${base}/login`, raw);
    if (!res.ok) return passthrough(res);
    const data = (await res.json()) as { status: string; tokens?: TokenBundle; mfa_token?: string; mfa_enrollment_required?: boolean };
    if (data.tokens) await setTokens(realm, data.tokens);
    return NextResponse.json({ status: data.status, mfa_token: data.mfa_token, mfa_enrollment_required: data.mfa_enrollment_required ?? false });
  }
  if (action === "mfa") {
    const res = await forward(`${base}/mfa/verify`, raw);
    if (!res.ok) return passthrough(res);
    await setTokens(realm, (await res.json()) as TokenBundle);
    return NextResponse.json({ status: "ok" });
  }
  if (action === "confirm-mfa") {
    const { access } = await getTokens(realm);
    const res = await forward(`${base}/mfa/confirm`, raw, access);
    if (!res.ok) return passthrough(res);
    const data = (await res.json()) as { recovery_codes: string[]; access_token: string; expires_in: number };
    await setTokens(realm, { access_token: data.access_token, expires_in: data.expires_in });
    return NextResponse.json({ recovery_codes: data.recovery_codes });
  }
  if (action === "logout") {
    const path = realm === "platform" ? "/platform/auth/logout" : "/auth/logout";
    await apiWithSession(realm, path, { method: "POST" });
    await clearTokens(realm);
    return new NextResponse(null, { status: 204 });
  }
  return NextResponse.json({ code: "not_found" }, { status: 404 });
}
