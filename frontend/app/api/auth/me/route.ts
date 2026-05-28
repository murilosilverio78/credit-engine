import { jwtVerify } from "jose";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

function secretKey() {
  const secret = process.env.NEXTAUTH_SECRET;
  if (!secret) {
    throw new Error("NEXTAUTH_SECRET não configurado");
  }
  return new TextEncoder().encode(secret);
}

export async function GET(req: NextRequest) {
  const token = req.cookies.get("session")?.value;

  if (!token) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  try {
    const { payload } = await jwtVerify(token, secretKey());
    return NextResponse.json({
      email: payload.email,
      id: payload.id,
      name: payload.name,
      role: payload.role,
    });
  } catch {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
}
