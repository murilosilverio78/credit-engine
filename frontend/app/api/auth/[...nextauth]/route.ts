import { randomUUID } from "crypto";

import nodemailer from "nodemailer";
import { NextRequest, NextResponse } from "next/server";

import { roleToAlcada, signSessionJwt, verifySessionJwt } from "@/lib/auth-jwt";
import type { UserRole } from "@/lib/types";

type RouteContext = { params: { nextauth?: string[] } };

interface DbUser {
  id: string;
  email: string;
  name: string | null;
  role: UserRole;
  active: boolean;
}

interface MagicToken {
  token: string;
  user_id: string;
  used: boolean;
  expires_at: string;
}

const cookieName = "session";

function supabaseConfig() {
  const url = process.env.SUPABASE_URL || process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key =
    process.env.SUPABASE_SERVICE_ROLE_KEY ||
    process.env.SUPABASE_SERVICE_KEY;

  if (!url || !key) {
    throw new Error("Supabase service credentials are not configured");
  }

  return { key, url };
}

async function supabaseRest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const { key, url } = supabaseConfig();
  const headers = new Headers(init.headers);
  headers.set("apikey", key);
  headers.set("Authorization", `Bearer ${key}`);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }

  const response = await fetch(`${url}/rest/v1/${path}`, {
    ...init,
    headers,
  });

  if (!response.ok) {
    throw new Error(await response.text());
  }

  if (response.status === 204) {
    return null as T;
  }

  return response.json() as Promise<T>;
}

async function findUserByEmail(email: string) {
  const users = await supabaseRest<DbUser[]>(
    `users?email=eq.${encodeURIComponent(email)}&select=id,email,name,role,active&limit=1`,
  );
  return users[0] ?? null;
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
    text: `Acesse o Credit Engine por este link: ${link}`,
    to: email,
  });
}

export async function POST(req: NextRequest, context: RouteContext) {
  const action = context.params.nextauth?.[0];

  if (action === "logout") {
    const response = NextResponse.json({ ok: true });
    response.cookies.set(cookieName, "", {
      httpOnly: true,
      maxAge: 0,
      path: "/",
      sameSite: "lax",
      secure: process.env.NODE_ENV === "production",
    });
    return response;
  }

  if (action !== "magic-link") {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const body = (await req.json().catch(() => ({}))) as { email?: string };
  const email = body.email?.trim().toLowerCase();
  if (!email) {
    return NextResponse.json({ error: "Email obrigatório" }, { status: 400 });
  }

  const user = await findUserByEmail(email);
  if (!user || !user.active) {
    return NextResponse.json({ error: "Usuário não autorizado" }, { status: 403 });
  }

  const token = randomUUID();
  const expiresAt = new Date(Date.now() + 15 * 60 * 1000).toISOString();
  await supabaseRest("magic_link_tokens", {
    body: JSON.stringify({
      expires_at: expiresAt,
      token,
      user_id: user.id,
      used: false,
    }),
    headers: { Prefer: "return=minimal" },
    method: "POST",
  });

  const baseUrl = process.env.NEXT_PUBLIC_APP_URL || req.nextUrl.origin;
  await sendMagicLink(email, `${baseUrl}/api/auth/verify?token=${token}`);

  return NextResponse.json({ ok: true });
}

export async function GET(req: NextRequest, context: RouteContext) {
  const action = context.params.nextauth?.[0];

  if (action === "me") {
    const payload = await verifySessionJwt(req.cookies.get(cookieName)?.value);
    if (!payload) {
      return NextResponse.json({ session: null }, { status: 401 });
    }

    return NextResponse.json({
      session: {
        user: {
          alcada: payload.alcada,
          email: payload.email,
          id: payload.id,
          name: payload.name,
          role: payload.role,
        },
      },
    });
  }

  if (action !== "verify") {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const token = req.nextUrl.searchParams.get("token");
  if (!token) {
    return NextResponse.redirect(new URL("/login?error=token", req.url));
  }

  const rows = await supabaseRest<MagicToken[]>(
    `magic_link_tokens?token=eq.${encodeURIComponent(token)}&used=eq.false&select=token,user_id,used,expires_at&limit=1`,
  );
  const magic = rows[0];
  if (!magic || new Date(magic.expires_at).getTime() < Date.now()) {
    return NextResponse.redirect(new URL("/login?error=expired", req.url));
  }

  const users = await supabaseRest<DbUser[]>(
    `users?id=eq.${encodeURIComponent(magic.user_id)}&active=eq.true&select=id,email,name,role,active&limit=1`,
  );
  const user = users[0];
  if (!user) {
    return NextResponse.redirect(new URL("/login?error=forbidden", req.url));
  }

  const sessionToken = randomUUID();
  await supabaseRest(`magic_link_tokens?token=eq.${encodeURIComponent(token)}`, {
    body: JSON.stringify({ used: true }),
    headers: { Prefer: "return=minimal" },
    method: "PATCH",
  });
  await supabaseRest("user_sessions", {
    body: JSON.stringify({
      expires_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
      ip_address: req.ip,
      session_token: sessionToken,
      user_agent: req.headers.get("user-agent"),
      user_id: user.id,
    }),
    headers: { Prefer: "return=minimal" },
    method: "POST",
  });
  await supabaseRest(`users?id=eq.${encodeURIComponent(user.id)}`, {
    body: JSON.stringify({ last_login_at: new Date().toISOString() }),
    headers: { Prefer: "return=minimal" },
    method: "PATCH",
  });

  const jwt = await signSessionJwt({
    alcada: roleToAlcada(user.role),
    email: user.email,
    id: user.id,
    name: user.name || user.email,
    role: user.role,
    session_token: sessionToken,
  });
  const response = NextResponse.redirect(new URL("/operations", req.url));
  response.cookies.set(cookieName, jwt, {
    httpOnly: true,
    maxAge: 60 * 60 * 24 * 7,
    path: "/",
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
  });
  return response;
}
