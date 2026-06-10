"""Authentication + per-user secret vault.

Supabase is used for identity only (login + TOTP 2FA + JWTs); this package verifies
those tokens server-side and stores each user's Delta credentials encrypted at rest.
Plaintext secrets NEVER leave the server.
"""
