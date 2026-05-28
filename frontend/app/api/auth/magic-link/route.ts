import { createClient } from "@supabase/supabase-js";
import nodemailer from "nodemailer";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

interface UserRow {
  id: string;
  email: string;
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

async function sendMagicLink(email: string, link: string) {
  const transporter = nodemailer.createTransport({
    auth: {
      pass: process.env.SMTP_PASS,
      user: process.env.SMTP_USER,
    },
    host: process.env.SMTP_HOST,
    port: Number(process.env.SMTP_PORT || 587),
    secure: Number(process.env.SMTP_PORT || 587) === 465,
  });

  await transporter.sendMail({
    from: process.env.EMAIL_FROM || process.env.SMTP_USER,
    subject: "Seu link de acesso ao Credit Engine",
    text: `Acesse o Credit Engine AntecipaGov por este link: ${link}`,
    to: email,
  });
}

export async function POST(req: NextRequest) {
  const body = (await req.json().catch(() => ({}))) as {
    email?: string;
    next?: string;
  };
  const email = body.email?.trim().toLowerCase();

  if (!email) {
    return NextResponse.json({ error: "Email obrigatório" }, { status: 400 });
  }

  const supabase = supabaseAdmin();
  const { data: user, error } = await supabase
    .from("users")
    .select("id,email,active")
    .eq("email", email)
    .maybeSingle<UserRow>();

  if (error) {
    return NextResponse.json({ error: "Erro ao consultar usuário" }, { status: 500 });
  }

  if (!user || !user.active) {
    return NextResponse.json({ error: "Usuário não autorizado" }, { status: 403 });
  }

  const token = crypto.randomUUID();
  const expiresAt = new Date(Date.now() + 10 * 60 * 1000).toISOString();
  const { error: insertError } = await supabase.from("magic_link_tokens").insert({
    expires_at: expiresAt,
    token,
    user_id: user.id,
    used: false,
  });

  if (insertError) {
    return NextResponse.json({ error: "Erro ao gerar token" }, { status: 500 });
  }

  const baseUrl = process.env.NEXTAUTH_URL || req.nextUrl.origin;
  const verifyUrl = new URL("/api/auth/verify", baseUrl);
  verifyUrl.searchParams.set("token", token);
  if (body.next) {
    verifyUrl.searchParams.set("next", body.next);
  }

  await sendMagicLink(user.email, verifyUrl.toString());

  return NextResponse.json({ ok: true });
}
