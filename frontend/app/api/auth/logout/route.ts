import { NextResponse } from "next/server";
import { clearSessionCookie } from "@/lib/auth";

/** 登出：清除 session cookie */
export async function POST() {
  return clearSessionCookie(NextResponse.json({ ok: true }));
}
