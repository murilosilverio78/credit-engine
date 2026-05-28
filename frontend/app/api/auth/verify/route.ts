import { createClient } from "@supabase/supabase-js";
import { SignJWT } from "jose";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

interface MagicTokenRow {
  token: string;
  user_id: string;
}

interface UserRow {
  id: string;
  email: string;
  name: string | null;
  role: string;
  active: boolean;
}

function supabaseAdmin() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!url || !key) {
    throw new Error("SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY não configurados");
  }

  return createClient(url, key, {
    auth: {
      autoRefreshToken: false,
      persistSession: false,
    },
  });
}

function secretKey() {
  const secret = process.env.NEXTAUTH_SECRET;
  if (!secret) {
    throw new Error("NEXTAUTH_SECRET não configurado");
  }
  return new TextEncoder().encode(secret);
}

export async function GET(req: NextRequest) {
  const token = req.nextUrl.searchParams.get("token");
  const next = req.nextUrl.searchParams.get("next") || "/operations";

  if (!token) {
    return NextResponse.redirect(new URL("/login?error=token", req.url));
  }

  const supabase = supabaseAdmin();
  const now = new Date().toISOString();
  const { data: magic, error: magicError } = await supabase
    .from("magic_link_tokens")
    .select("token,user_id")
    .eq("token", token)
    .eq("used", false)
    .gt("expires_at", now)
    .maybeSingle<MagicTokenRow>();

  if (magicError || !magic) {
    return NextResponse.redirect(new URL("/login?error=expired", req.url));
  }

  const { error: updateError } = await supabase
    .from("magic_link_tokens")
    .update({ used: true })
    .eq("token", token);

  if (updateError) {
    return NextResponse.redirect(new URL("/login?error=token", req.url));
  }

  const { data: user, error: userError } = await supabase
    .from("users")
    .select("id,email,name,role,active")
    .eq("id", magic.user_id)
    .eq("active", true)
    .maybeSingle<UserRow>();

  if (userError || !user) {
    return NextResponse.redirect(new URL("/login?error=forbidden", req.url));
  }

  const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
  const jwt = await new SignJWT({
    email: user.email,
    id: user.id,
    name: user.name || user.email,
    role: user.role,
  })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setIssuedAt()
    .setExpirationTime("7d")
    .sign(secretKey());

  const redirectUrl = new URL(next.startsWith("/") ? next : "/operations", req.url);
  const response = NextResponse.redirect(redirectUrl);
  response.cookies.set("session", jwt, {
    expires: expiresAt,
    httpOnly: true,
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });
  return response;
}
