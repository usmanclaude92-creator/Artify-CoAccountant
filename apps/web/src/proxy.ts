import { NextResponse, type NextRequest } from "next/server";

/**
 * Edge gate (first line only; the API enforces real authorization):
 *  - APP_SURFACE=console  -> /platform/* does not exist on this deployment
 *  - APP_SURFACE=platform -> only /platform/*, /bff/platform/*, /session/platform/*
 *  - signed-out visitors are sent to the sign-in page for protected paths
 */
const surface = process.env.APP_SURFACE ?? "all";

export function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const isPlatformPath =
    pathname.startsWith("/platform") || pathname.startsWith("/bff/platform") || pathname.startsWith("/session/platform");
  if (surface === "console" && isPlatformPath) return new NextResponse("Not found", { status: 404 });
  if (surface === "platform" && !isPlatformPath && !pathname.startsWith("/_next")) {
    return new NextResponse("Not found", { status: 404 });
  }
  const hasAccess = req.cookies.has("artify_at") || req.cookies.has("artify_rt");
  const hasPlatformAccess = req.cookies.has("artify_pat") || req.cookies.has("artify_prt");
  const protectedTenant = pathname.startsWith("/dashboard") || pathname.startsWith("/security");
  // sign-in and invitation acceptance are the only platform pages reachable without a session
  const publicPlatform = pathname.startsWith("/platform/login") || pathname.startsWith("/platform/accept-invitation");
  const protectedPlatform = pathname.startsWith("/platform") && !publicPlatform;
  if (protectedTenant && !hasAccess) return NextResponse.redirect(new URL("/login", req.url));
  if (protectedPlatform && !hasPlatformAccess) return NextResponse.redirect(new URL("/platform/login", req.url));
  return NextResponse.next();
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
