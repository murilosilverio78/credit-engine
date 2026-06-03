import { NextResponse } from "next/server";

export async function POST() {
  const response = NextResponse.json({ ok: true });
  response.cookies.set("session", "", {
    httpOnly: true,
    maxAge: 0,
    path: "/",
    sameSite: "none",
    secure: true,
  });
  return response;
}
