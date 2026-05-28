import { createClient } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";

interface MagicTokenRow {
  token: string;
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

export async function GET(req: NextRequest) {
  const token = req.nextUrl.searchParams.get("token");
  const next = req.nextUrl.searchParams.get("next") || "/operations";

  if (!token) {
    return NextResponse.redirect(new URL("/login?error=token", req.url));
  }

  const supabase = supabaseAdmin();
  const { data: magic, error } = await supabase
    .from("magic_link_tokens")
    .select("token")
    .eq("token", token)
    .eq("used", false)
    .gt("expires_at", new Date().toISOString())
    .maybeSingle<MagicTokenRow>();

  if (error || !magic) {
    return NextResponse.redirect(new URL("/login?error=expired", req.url));
  }

  const confirmUrl = new URL("/auth/confirm", req.url);
  confirmUrl.searchParams.set("token", magic.token);
  confirmUrl.searchParams.set("next", next.startsWith("/") ? next : "/operations");
  return NextResponse.redirect(confirmUrl);
}
