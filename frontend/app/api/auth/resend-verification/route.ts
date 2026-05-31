import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));
  const backendUrl = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL;

  const res = await fetch(`${backendUrl}/api/v1/auth/resend-verification`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  const data = await res.json().catch(() => ({}));

  return NextResponse.json(data, { status: res.status });
}
