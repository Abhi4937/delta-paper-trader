// Auth helpers over the Supabase client. The backend enforces 2FA (it requires the
// `aal2` assurance level), so the UI must surface AAL to route users through TOTP.
import { supabase } from "./supabase";

export async function getAccessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export interface AAL {
  current: string | null; // "aal1" | "aal2"
  next: string | null;
}

export async function getAAL(): Promise<AAL> {
  const { data } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
  return { current: data?.currentLevel ?? null, next: data?.nextLevel ?? null };
}

export async function signOut(): Promise<void> {
  await supabase.auth.signOut();
}

// True once the user has a verified TOTP factor (2FA is set up). Independent of whether
// the current session has stepped up to aal2.
export async function hasTotp(): Promise<boolean> {
  if (process.env.NEXT_PUBLIC_E2E === "1") return true; // same compile-time bypass as AuthGate
  const { data } = await supabase.auth.mfa.listFactors();
  return (data?.totp ?? []).some((f) => f.status === "verified");
}

// Remove all TOTP factors (disable 2FA). Caller must ensure no live keys remain.
export async function unenrollTotp(): Promise<void> {
  const { data } = await supabase.auth.mfa.listFactors();
  for (const f of data?.totp ?? []) {
    await supabase.auth.mfa.unenroll({ factorId: f.id });
  }
}

// Live features (saving keys, /live) need THIS session to have passed 2FA — the server
// rejects aal1 with 403. If 2FA is set up but not yet entered this session, send the user
// to the code prompt and bring them back. Returns true when the session is already aal2.
export async function ensureStepUp(returnTo: string): Promise<boolean> {
  if (process.env.NEXT_PUBLIC_E2E === "1") return true;
  if ((await getAAL()).current === "aal2") return true;
  window.location.replace(`/enroll-2fa?next=${encodeURIComponent(returnTo)}`);
  return false;
}
