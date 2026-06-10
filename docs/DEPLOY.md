# Deploy — single free VM (Oracle Cloud Always Free), full stack

One always-free VM runs the whole stack via `docker-compose.prod.yml`:

```
Caddy (auto-HTTPS)  ──/───────────────→  frontend (Next.js)
                    └─/api, /ws, /health → backend (FastAPI, single process)
backend ─────────────────────────────────→ db (Postgres + TimescaleDB)
```

Supabase (auth) stays in the cloud. You need a **hostname** (the auth flow needs HTTPS) — a
**free DuckDNS subdomain** works (step 2); Caddy gets a free Let's Encrypt cert for it.

> **Single process:** the backend runs **one** uvicorn worker on purpose (the 1-second tick
> loop + in-memory series ring are per-process). Don't scale it to multiple workers without
> first moving the ticker out and the ring to Redis.

---

## 1. Create the VM (Oracle Cloud Always Free)
1. Oracle Cloud → **Compute → Instances → Create**.
2. Image **Ubuntu 22.04**, shape **Ampere A1 (ARM)** — the free tier gives up to 4 OCPU / 24 GB
   (plenty; the Docker images are all arm64). *(An AMD micro 1 GB also works thanks to the
   lightweight ring, but ARM is roomier.)*
3. Add your SSH key, create.
4. **Networking → open ports 80 and 443** in BOTH layers (Oracle has two firewalls):
   - **VCN security list:** add ingress rules for TCP 80 and 443 from `0.0.0.0/0`.
   - **On the VM** (Oracle Ubuntu ships restrictive iptables):
     ```
     sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
     sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
     sudo netfilter-persistent save
     ```

## 2. Get a free hostname with DuckDNS (no domain purchase needed)
Oracle gives you a public **IP**, not a hostname — and HTTPS needs a hostname. Use a free
[DuckDNS](https://www.duckdns.org) subdomain:
1. duckdns.org → sign in (GitHub/Google).
2. Add a subdomain, e.g. `paper-trader-abhi` → you now own `paper-trader-abhi.duckdns.org`.
3. Put the VM's **public IP** in the box for that subdomain → **update ip**.
4. Confirm it resolves: `ping paper-trader-abhi.duckdns.org` (should show your VM IP).

That hostname is your `DOMAIN` everywhere below. Caddy gets a real Let's Encrypt cert for it
automatically via the standard HTTP challenge — no DuckDNS token or DNS plugin needed (the
included `Caddyfile` works as-is). *(A paid domain later works the same way — just swap `DOMAIN`.)*

## 3. Install Docker on the VM
```
ssh ubuntu@<vm-ip>
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER && newgrp docker
```

## 4. Get the code + configure
```
git clone https://github.com/Abhi4937/delta-paper-trader.git
cd delta-paper-trader
cp .env.prod.example .env                       # compose vars
cp backend/.env.prod.example backend/.env.prod  # backend secrets
```
Edit **`.env`** — set `DOMAIN`, a strong `POSTGRES_PASSWORD`, and your `SUPABASE_URL` +
`SUPABASE_ANON_KEY` (publishable key).
Edit **`backend/.env.prod`** — set `DELTA_API_KEY`/`SECRET`, `SUPABASE_URL`, `ADMIN_EMAIL`, and a
fresh `SECRET_VAULT_KEY`:
```
python3 -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```
> ⚠️ Use a **fresh** `SECRET_VAULT_KEY` for prod and keep it safe — if it changes, stored keys
> can't be decrypted. (Different from your dev key is fine; just never lose the prod one.)

## 5. Point Supabase + Google at the prod domain
- Supabase → **Authentication → URL Configuration**: Site URL `https://paper-trader-abhi.duckdns.org`,
  and add `https://paper-trader-abhi.duckdns.org/**` to **Redirect URLs**.
- (Google OAuth redirect URI stays the Supabase callback — unchanged.)

## 6. Launch
```
docker compose -f docker-compose.prod.yml up -d --build
```
Caddy fetches HTTPS automatically (first request may take ~30 s). Migrations run on backend
start. Check:
```
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f backend caddy
```
Open **https://paper-trader-abhi.duckdns.org** → sign in with `ADMIN_EMAIL` → you're the admin.

## 7. Updates
```
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

## Notes / ops
- **Backups:** the DB volume is `pgdata`. Back it up with
  `docker compose -f docker-compose.prod.yml exec db pg_dump -U paper paper_trader > backup.sql`.
- **Logs:** `docker compose -f docker-compose.prod.yml logs -f <service>`.
- **Production hardening already in place:** `APP_ENV=production` + `ALLOW_DEV_NO_AUTH=false`
  (no auth bypass), CORS locked to your domain, per-IP rate limiting, WS token in the
  handshake header, encrypted vault.
- **Scaling past one VM** (later): move the tick loop to its own process and the 1-second ring
  to Redis, then the backend can run multiple replicas behind a load balancer.
