import { cookies, headers } from "next/headers";
import { isLocale, type Locale } from "./messages";

export async function getLocale(): Promise<Locale> {
  const fromCookie = (await cookies()).get("artify_locale")?.value;
  if (isLocale(fromCookie)) return fromCookie;
  const accept = (await headers()).get("accept-language") ?? "";
  return accept.toLowerCase().startsWith("ar") ? "ar" : "en";
}
