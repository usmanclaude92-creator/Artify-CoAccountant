import type { AppError } from "../bridge/types";

/** Anything thrown by `invoke` is the serialized Rust AppError; normalise odd failures into the same shape. */
export function toAppError(e: unknown): AppError {
  if (e && typeof e === "object" && "code" in e && "message" in e) return e as AppError;
  return { code: "unexpected", message: typeof e === "string" ? e : "Something unexpected went wrong.", retryable: false };
}

/** Plain-language text for the codes users can actually meet. Falls back to the message from the Rust side. */
export function describe(e: unknown): string {
  const a: AppError = toAppError(e);
  switch (a.code) {
    case "network_unavailable": return "The server cannot be reached. Check your internet connection; saved data is shown where available.";
    case "timeout": return "The server took too long to answer. Please try again.";
    case "session_expired": return "Your session has ended. Please sign in again.";
    case "device_revoked": return "This device has been revoked. Contact your administrator.";
    case "api_not_configured": return a.message;
    case "entitlement_exceeded": return `${a.message} Contact your administrator about your plan limits.`;
    default: return a.message || "Something went wrong.";
  }
}
export const isSessionExpired = (e: unknown): boolean => toAppError(e).code === "session_expired";
