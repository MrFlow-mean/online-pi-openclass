import { NextResponse, type NextRequest } from "next/server";

const AUTH_COOKIE_NAME = "openclass.auth.token";
const GUEST_AUTH_COOKIE_NAME = "openclass.guest.auth.token";
const publicRoutes = [
  "/login",
  "/register",
  "/forgot-password",
  "/reset-password",
  "/auth/callback",
  "/trending",
  "/community",
  "/privacy",
  "/terms",
  "/security",
];
const publicRoutePrefixes = ["/courses"];
const productLandingRedirects = ["/admin", "/following", "/profile", "/studio"];

function isPublicRoute(pathname: string) {
  const contributionDetail = pathname.match(/^\/contributions\/([^/]+)$/)?.[1];
  return (
    Boolean(contributionDetail) ||
    publicRoutes.some((route) => pathname === route || pathname.startsWith(`${route}/`)) ||
    publicRoutePrefixes.some((route) => pathname === route || pathname.startsWith(`${route}/`))
  );
}

function safeNextPath(value: string | null) {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    /[\u0000-\u001f\u007f]/.test(value)
  ) {
    return "/";
  }
  if (value === "/login" || value.startsWith("/login?") || value === "/register" || value.startsWith("/register?")) {
    return "/";
  }
  if (productLandingRedirects.some((route) => value === route || value.startsWith(`${route}?`) || value.startsWith(`${route}/`))) {
    return "/";
  }
  return value;
}

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const token = request.cookies.get(AUTH_COOKIE_NAME)?.value;
  const guestToken = request.cookies.get(GUEST_AUTH_COOKIE_NAME)?.value;
  const hasSession = Boolean(token || guestToken);

  if (!token && pathname.startsWith("/admin")) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(loginUrl);
  }

  if (!hasSession && !isPublicRoute(pathname)) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", `${pathname}${search}`);
    return NextResponse.redirect(loginUrl);
  }

  if (token && (pathname === "/login" || pathname === "/register")) {
    return NextResponse.redirect(new URL(safeNextPath(request.nextUrl.searchParams.get("next")), request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
