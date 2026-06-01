import { NextRequest, NextResponse } from "next/server";

import { verifySessionJwt } from "@/lib/auth-jwt";

const DIRETOR_ONLY_PREFIXES = [
  "/settings/alcadas",
  "/settings/pricing",
  "/settings/users",
];

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
    DIRETOR_ONLY_PREFIXES.some((prefix) => req.nextUrl.pathname.startsWith(prefix)) &&
    session.role !== "diretor"
  ) {
    return NextResponse.redirect(new URL("/forbidden", req.url));
  }

  return NextResponse.next();
}
