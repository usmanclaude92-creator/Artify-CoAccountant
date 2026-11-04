export type Realm = "tenant" | "platform";
/** API path prefix used through the BFF for each realm. */
export const authPrefix = (realm: Realm) => (realm === "platform" ? "/bff/platform/auth" : "/bff/auth");
