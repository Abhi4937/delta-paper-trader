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
