import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  const backendUrl = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL;

  const res = await fetch(`${backendUrl}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await res.json().catch(() => ({}));

  if (!res.ok) {
    return NextResponse.json(data, { status: res.status });
  }

  const setCookie = res.headers.get("set-cookie");
  const response = NextResponse.json({ ok: true });
  if (setCookie) {
    response.headers.set("set-cookie", setCookie);
  }
  return response;
}
