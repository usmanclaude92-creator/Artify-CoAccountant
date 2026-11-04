"use client";

export interface Problem {
  code?: string;
  detail?: string;
  status?: number;
  errors?: { loc: string[]; msg: string }[];
  [k: string]: unknown;
}

export class ApiProblem extends Error {
  constructor(public problem: Problem, public status: number) {
    super(problem.detail ?? problem.code ?? "request failed");
  }
}

/** Calls the same-origin BFF. The CSRF header is mandatory for non-GET requests. */
export async function bff<T = unknown>(path: string, init: { method?: string; body?: unknown; org?: string } = {}): Promise<T> {
  const method = init.method ?? (init.body === undefined ? "GET" : "POST");
  const res = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      "content-type": "application/json",
      ...(method !== "GET" ? { "x-artify-csrf": "1" } : {}),
      ...(init.org ? { "x-organization-id": init.org } : {}),
    },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiProblem((data ?? {}) as Problem, res.status);
  return data as T;
}

export function problemMessage(err: unknown, errors: Record<string, string>): string {
  if (err instanceof ApiProblem) return errors[err.problem.code ?? ""] ?? err.problem.detail ?? errors.generic ?? "Error";
  return errors.generic ?? "Error";
}
