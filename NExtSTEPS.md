# NExtSTEPS: what to change after `./startup.sh install`

The default install is wired for **localhost demo**: well-known passwords, no
TLS, no real API keys, public-facing logins disabled. Anything beyond "running
on my laptop to try things out" needs at minimum the steps in §1 below.
Anything internet-facing needs §1 + §2 + §3.

This doc is meant to be skimmed. Each item shows what to change, where, and
what to run to apply.

---

## 1. Anything beyond a private localhost demo

### 1a. Rotate the demo user passwords

`./startup.sh install` seeds two accounts:

| Username | Password | Role |
|---|---|---|
| `demo` | `demopassword` | Admin |
| `user` | `userpassword` | Regular |

Anyone who can `git clone` this repo knows those credentials. The moment your
install is reachable by anyone you don't trust, rotate them via SEEK's admin
UI:

1. Log in as `demo` at `http://<your-host>:<seek-port>/login`
2. **My Account → Edit profile → Change password** for both `demo` and `user`
3. If you want SEEK closed to new signups: **Server Admin → Configure
   instance → Allow registration → No**

### 1b. Make sure `DJANGO_DEBUG` is **unset** for anything internet-facing

`docker/nextseek.env` does not set `DJANGO_DEBUG` by default, which Django
interprets as production mode, as it should be.

The settings code is
`DEBUG = (os.getenv("DJANGO_DEBUG") or "").strip().lower() in ("1", "true", "yes")`,
so debug turns **on** only for the explicit values `1`, `true` or `yes`
(case-insensitive, surrounding whitespace stripped). Absent, empty, `0`,
`False`/`false`, `no` and `off` all leave debug **off**. Deleting the line
entirely is still the clearest production state:

```ini
# docker/nextseek.env: absent, empty, or an explicitly falsy value
# (0 / false / no / off) all keep debug off. Deleting the line is clearest.
```

Apply: `docker compose up -d --force-recreate nextseek`

### 1c. Tighten `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`

Startup writes localhost values:

```ini
DJANGO_ALLOWED_HOSTS="127.0.0.1 localhost"
DJANGO_CSRF_TRUSTED_ORIGINS="http://127.0.0.1:8000 http://localhost:8000"
```

For a real hostname (e.g., `nextseek.example.com`):

```ini
DJANGO_ALLOWED_HOSTS="nextseek.example.com"
DJANGO_CSRF_TRUSTED_ORIGINS="https://nextseek.example.com"
```

Apply: `docker compose up -d --force-recreate nextseek`

### 1d. Set the browser-reachable SEEK URL (`--seek-public-url`)

SEEK is served on its own hostname in a real deployment (NExtSEEK and SEEK are
two different sites). Two settings must name that hostname, and a localhost
default is wrong for both:

| Setting | Owner | What it breaks if wrong |
|---|---|---|
| `SEEK_PUBLIC_URL` (`docker/nextseek.env`) | NExtSEEK | SOP / data-file / sample / project links point somewhere unreachable |
| `site_base_host` (SEEK's own DB setting) | SEEK | the displayed "SEEK ID", JSON-LD `@id` identifiers, the sitemap, and SEEK **rejects** pasted SEEK IDs that don't match it |

Set both from one place, at install time:

```bash
./startup.sh install --seek-public-url https://seek.example.com
```

Startup stores it in `startup/.instance.json`, renders `SEEK_PUBLIC_URL` from it,
and applies SEEK's `site_base_host` before SEEK first boots (so the sitemap is
built correctly and no restart is needed). `reset` carries the value across.

Notes:

- **Host only, no path** (`https://seek.example.com`, not `.../seek`).
- **Omit it on a laptop**: it defaults to `http://localhost:<seek port>`.
- **A hand-edited `SEEK_PUBLIC_URL` in `docker/nextseek.env` is preserved**: a
  re-run of `install` reads it back rather than resetting it to the default.
- **An existing `site_base_host` is never overwritten** by startup. If SEEK
  already has one (e.g. an admin set it in *Server admin → Settings → Site base
  Hostname*), startup reports the mismatch and leaves SEEK's value alone.
- Check both agree at any time with `./startup.sh doctor` ("SEEK public URL").

---

## 2. MySQL + Neo4j credentials

### 2a. MySQL: `docker/db.env`

```ini
MYSQL_ROOT_PASSWORD="<strong-random>"
MYSQL_PASSWORD="<strong-random>"          # app user (seek_db_user)
```

`MYSQL_USER` and database names (`dmac`, `seek_production`) are referenced in
many places; only rotate the **passwords**, leave names alone.

**Apply**: rotate **in place**; this is the only route that preserves your
edited credentials.

> ⚠ **`./startup.sh reset --keep-config` does NOT preserve rotated
> passwords.** `--keep-config` only skips *deleting* the files; the
> re-install phase then unconditionally re-renders `docker/db.env` from the
> template with the well-known defaults (`seek_root` / `seek_db_password`),
> silently reverting your rotation while everything keeps working. Only
> `SEEK_PUBLIC_URL` and the Bedrock proxy token have read-back preservation.

To rotate passwords on the EXISTING populated DB, update the MySQL user
grants in place:

```bash
docker compose exec db mysql -uroot -p<old-root-pw> \
  -e "ALTER USER 'root'@'%' IDENTIFIED BY '<new-root-pw>'; \
      ALTER USER 'seek_db_user'@'%' IDENTIFIED BY '<new-app-pw>'; \
      FLUSH PRIVILEGES;"
# then edit docker/db.env to match, then recreate the three services that read it:
docker compose up -d --no-deps --force-recreate nextseek seek seek_workers
```

### 2b. Neo4j

`NEO4J_PASSWORD` in the repo-root `.env` seeds the credential when the
`neo4j-data` volume is first created; the app reads `NEO4J_PASSWORD` and
`NEXTSEEK_NEO4J_PASSWORD` in `docker/nextseek.env`. After first start the
credential lives in the volume, so changing the files alone does nothing.
Rotate with the two steps in
[`docs/neo4j-programmatic-access.md`](docs/neo4j-programmatic-access.md),
"Passwords": a cypher `ALTER CURRENT USER`, then update both variables and
recreate `nextseek`.

---

## 3. Django secret key

Startup auto-generates a 64-character secret on every install. If the
current key was ever logged / committed / shared, rotate it:

```bash
python -c 'import secrets, string; print("".join(secrets.choice(string.ascii_letters + string.digits + "!@%^&*()-_=+:.<>?") for _ in range(64)))'
```

Paste the output into `docker/nextseek.env`:

```ini
DJANGO_SECRET_KEY="<paste here>"
```

Apply: `docker compose up -d --force-recreate nextseek`. **All existing
sessions and password-reset links will invalidate**: users will need to log
in again.

---

## 4. LLM API keys (chat features)

`docker/nextseek.env` ships these as `SET_IN_LOCAL_ENV` placeholders. The
chat assistant stays inert until at least one is filled in:

```ini
GCP_API_KEY="..."                    # Google Gemini (cheapest tier)
AWS_BEARER_TOKEN_BEDROCK="..."       # AWS Bedrock (Anthropic Claude via AWS)
FDH_API="..."                        # FairDOMHub API token (NExtSEEK-specific)
```

Apply: `docker compose up -d --force-recreate nextseek`

The chat panel's PROD toggle (admin-only) uses a separate `_PROD_OVERRIDES`
block in `dmac/local_settings.py`; fill that in only if you want admins to
be able to switch between dev and prod credential sets at runtime.

---

## 5. TLS / HTTPS

Startup's nginx config terminates plain HTTP. For anything internet-facing,
front the stack with a TLS-terminating reverse proxy:

- **Caddy** (easiest, automatic Let's Encrypt) → reverse-proxy
  `https://nextseek.example.com` → `http://localhost:8000`
- **nginx** in front of the docker-compose nginx → manual certs or certbot
- **Cloudflare Tunnel** → free, no port exposure needed

Whichever path you pick, also set `DJANGO_CSRF_TRUSTED_ORIGINS` to the
`https://` URL (§1c above).

---

## 6. Backups

Three things to back up:

```bash
# MySQL: both schemas. -T is REQUIRED: without it `docker compose exec`
# allocates a TTY, which rewrites LF -> CRLF and corrupts the dump.
docker compose exec -T db mysqldump -uroot -p<root-pw> \
  --single-transaction --routines --triggers \
  --databases dmac seek_production | gzip > nextseek-mysql-$(date +%F).sql.gz

# Neo4j: use the repo's own bolt-driver exporter (the same mechanism the
# maintainer `./startup.sh dump-db` flow uses; APOC is NOT enabled in the
# shipped stack, so apoc.export.* procedures are unavailable):
#   startup/seed/regenerate/dump_neo4j.py; see startup/README.md
#   ("dump-db") for the required dump-source.env credentials file.

# SEEK filestore (user uploads, blobs)
docker run --rm -v <prefix>seek-filestore:/data -v "$(pwd):/backup" alpine \
  tar czf /backup/seek-filestore-$(date +%F).tar.gz -C /data .
```

Where `<prefix>` is the value from `startup/.instance.json`'s `prefix`
field (empty for default install, `dev-` / `test-` / etc. for named
instances).

To restore: same commands in reverse, or use `./startup.sh reset` with the
new dumps dropped into `startup/seed/` (you'd be replacing the shipped
seed snapshots; see [`startup/README.md`](startup/README.md) for the
maintainer regen workflow).

---

## 7. Updates

Follow [`DEPLOYMENT.md`](DEPLOYMENT.md) §3, the redeploy procedure: fast-forward
the deploy clone, take a rollback tag, run the mysqldump gate if the range has
migrations, rebuild, and run the §6 checklist.

---

## 8. Known limitations / future improvements

- **No per-service `--*-port` flags in the startup CLI yet**:
  `--port-offset N` is the only way to shift all ports together.
- **`docker compose up -d` output is captured, not streamed**: long
  rebuilds appear silent until they finish. Worth adding a `--verbose`
  startup flag.
- **No automated TLS startup**: TLS is a manual outside-the-startup
  step. Caddy or Cloudflare Tunnel are the lowest-friction paths.

---

## Quick-reference: where each setting lives

| Setting | File | Apply with |
|---|---|---|
| Demo user passwords | SEEK admin UI (web) | (immediate) |
| MySQL passwords | `docker/db.env` | in-place ALTER USER only (§2a; reset re-renders db.env to defaults) |
| Neo4j password | root `.env` (first start) + `docker/nextseek.env` | in-place cypher `ALTER`, then recreate `nextseek` (§2b) |
| Django secret | `docker/nextseek.env` | `docker compose up -d --force-recreate nextseek` |
| ALLOWED_HOSTS / CSRF | `docker/nextseek.env` | `docker compose up -d --force-recreate nextseek` |
| LLM API keys | `docker/nextseek.env` | `docker compose up -d --force-recreate nextseek` |
| PROD ChatConfig overrides | `dmac/local_settings.py` | `docker compose up -d --force-recreate nextseek` |
