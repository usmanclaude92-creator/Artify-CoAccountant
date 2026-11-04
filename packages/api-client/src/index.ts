/**
 * Typed API client. `schema.d.ts` is GENERATED from openapi.json (itself generated from the FastAPI
 * app); never edit either by hand — run `make openapi && pnpm --filter @artify/api-client generate`.
 */
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type { components, paths };
export type Schemas = components["schemas"];

export interface ApiClientOptions {
  baseUrl: string;
  /** Returns the bearer token for the current session, if any. */
  getAccessToken?: () => string | undefined | Promise<string | undefined>;
  /** Active organization, sent as X-Organization-Id on company-scoped routes. */
  getOrganizationId?: () => string | undefined;
  fetch?: typeof fetch;
}

export function createApiClient(opts: ApiClientOptions) {
  const client = createClient<paths>({ baseUrl: opts.baseUrl, fetch: opts.fetch });
  const auth: Middleware = {
    async onRequest({ request }) {
      const token = await opts.getAccessToken?.();
      if (token) request.headers.set("Authorization", `Bearer ${token}`);
      const org = opts.getOrganizationId?.();
      if (org) request.headers.set("X-Organization-Id", org);
      return request;
    },
  };
  client.use(auth);
  return client;
}

/** RFC 9457 problem document as returned by every non-2xx API response. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code: string;
  detail: string;
  correlation_id?: string;
  [extra: string]: unknown;
}
