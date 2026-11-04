import "server-only";
import { cookies } from "next/headers";

export type Realm = "tenant" | "platform";
export const isRealm = (v: string): v is Realm => v === "tenant" || v === "platform";

const NAMES = {
  tenant: { access: "artify_at", refresh: "artify_rt" },
  platform: { access: "artify_pat", refresh: "artify_prt" },
} as const;

/** Backend base URL. A localhost default exists only outside production: a deployed build without a
 * configured backend (e.g. a UI-only preview) must say so, not silently call localhost. */
export const API_URL =
  process.env.ARTIFY_API_URL ?? (process.env.NODE_ENV !== "production" ? "http://localhost:8000/api/v1" : undefined);

/** fetch() against the API that never throws: an unconfigured or unreachable backend becomes a 503
 * problem document with code `api_unavailable`, which the UI translates. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const unavailable = () =>
    new Response(
      JSON.stringify({ code: "api_unavailable", status: 503, detail: "The backend is not connected to this deployment." }),
      { status: 503, headers: { "content-type": "application/problem+json" } },
    );
  if (!API_URL) return unavailable();
  try {
    return await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  } catch {
    return unavailable();
  }
}
const secure = process.env.NODE_ENV === "production";

export interface TokenBundle {
  access_token: string;
  refresh_token?: string;
  expires_in: number;
}

/** Tokens live only in HttpOnly, SameSite=Strict cookies: page JavaScript can never read them. */
export async function setTokens(realm: Realm, t: TokenBundle) {
  const jar = await cookies();
  const base = { httpOnly: true, secure, sameSite: "strict" as const, path: "/" };
  jar.set(NAMES[realm].access, t.access_token, { ...base, maxAge: t.expires_in });
  if (t.refresh_token) {
    jar.set(NAMES[realm].refresh, t.refresh_token, {
      ...base,
      maxAge: realm === "tenant" ? 30 * 24 * 3600 : 24 * 3600,
    });
  }
}

export async function getTokens(realm: Realm) {
  const jar = await cookies();
  return { access: jar.get(NAMES[realm].access)?.value, refresh: jar.get(NAMES[realm].refresh)?.value };
}

export async function clearTokens(realm: Realm) {
  const jar = await cookies();
  jar.delete(NAMES[realm].access);
  jar.delete(NAMES[realm].refresh);
}

/** Calls the API; on 401 rotates the refresh token once and retries. */
export async function apiWithSession(
  realm: Realm,
  path: string,
  init: { method: string; body?: string; headers?: Record<string, string> },
): Promise<Response> {
  const call = async (access: string | undefined) =>
    apiFetch(path, {
      method: init.method,
      body: init.body,
      headers: {
        "content-type": "application/json",
        ...(init.headers ?? {}),
        ...(access ? { authorization: `Bearer ${access}` } : {}),
      },
    });
  const { access, refresh } = await getTokens(realm);
  let res = await call(access);
  if (res.status === 401 && refresh) {
    const refreshPath = realm === "platform" ? "/platform/auth/refresh" : "/auth/refresh";
    const r = await apiFetch(refreshPath, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (r.ok) {
      const bundle = (await r.json()) as TokenBundle;
      await setTokens(realm, bundle);
      res = await call(bundle.access_token);
    } else {
      await clearTokens(realm);
    }
  }
  return res;
}
