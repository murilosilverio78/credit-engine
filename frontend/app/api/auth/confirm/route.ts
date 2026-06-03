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

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => ({}))) as {
    token?: string;
    next?: string;
  };
  const token = body.token;

  if (!token) {
    return NextResponse.json({ error: "Token obrigatório" }, { status: 400 });
  }

  const supabase = supabaseAdmin();
  const { data: magic, error: magicError } = await supabase
    .from("magic_link_tokens")
    .select("token,user_id")
    .eq("token", token)
    .eq("used", false)
    .gt("expires_at", new Date().toISOString())
    .maybeSingle<MagicTokenRow>();

  if (magicError || !magic) {
    return NextResponse.json({ error: "Token inválido ou expirado" }, { status: 400 });
  }

  const { data: user, error: userError } = await supabase
    .from("users")
    .select("id,email,name,role,active")
    .eq("id", magic.user_id)
    .eq("active", true)
    .maybeSingle<UserRow>();

  if (userError || !user) {
    return NextResponse.json({ error: "Usuário não autorizado" }, { status: 403 });
  }

  const { error: updateError } = await supabase
    .from("magic_link_tokens")
    .update({ used: true })
    .eq("token", token)
    .eq("used", false);

  if (updateError) {
    return NextResponse.json({ error: "Erro ao consumir token" }, { status: 500 });
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

  const next = body.next?.startsWith("/") ? body.next : "/operations";
  return NextResponse.json({
    access_token: jwt,
    expires_at: expiresAt.toISOString(),
    next,
    ok: true,
    token_type: "bearer",
  });
}
