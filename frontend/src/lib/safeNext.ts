// Only same-app paths ("/x", never "//host", "/\host" or "https://…"): an open redirect
// would let a crafted link bounce a freshly 2FA-verified session to another site.
export function safeNext(search: string, fallback: string): string {
  const n = new URLSearchParams(search).get("next") ?? "";
  return n.startsWith("/") && !n.startsWith("//") && !n.startsWith("/\\") ? n : fallback;
}
