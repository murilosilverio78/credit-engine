import { NextRequest, NextResponse } from "next/server";

import { verifySessionJwt } from "@/lib/auth-jwt";

export const config = {
  matcher: [
    "/operations/:path*",
    "/overrides/:path*",
    "/components/:path*",
    "/escaladas/:path*",
    "/settings/:path*",
  ],
};

export async function middleware(req: NextRequest) {
  const session = await verifySessionJwt(req.cookies.get("session")?.value);

  if (!session) {
    const loginUrl = new URL("/login", req.url);
    loginUrl.searchParams.set("next", req.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }

  if (
    req.nextUrl.pathname.startsWith("/settings/alcadas") &&
    session.role !== "diretor"
  ) {
    return NextResponse.redirect(new URL("/forbidden", req.url));
  }

  return NextResponse.next();
}
