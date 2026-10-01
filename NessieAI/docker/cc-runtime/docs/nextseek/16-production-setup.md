# Production setup

A default install is a local demo: well-known passwords, no TLS and chat switched off. Anything that other people can reach needs item 1. Anything on the internet needs all of the items below. Each one says what to change, where, and how to apply it. Work in the folder where you ran [the install](installation.md).

!!! warning
    Rotating the demo passwords (item 1) is the minimum before anyone but you can reach the instance.

## Checklist

### 1. Rotate the demo passwords

Install creates two accounts, `demo` (administrator) and `user` (regular), with passwords that are published in the repository.

- Sign in to SEEK and choose **My Account**, then **Edit profile**, then **Change password**, for both accounts.
- To close sign-ups, set **Server Admin**, then **Configure instance**, then **Allow registration** to **No**.

### 2. Lock down Django

In `docker/nextseek.env`:

- Leave `DJANGO_DEBUG` unset. Debug turns on only for the values `1`, `true` or `yes`.
- Set `DJANGO_ALLOWED_HOSTS` to your host name only.
- Set `DJANGO_CSRF_TRUSTED_ORIGINS` to your `https://` address.

```ini
DJANGO_ALLOWED_HOSTS="nextseek.example.org"
DJANGO_CSRF_TRUSTED_ORIGINS="https://nextseek.example.org"
```

Apply: `docker compose up -d --no-deps --force-recreate nextseek`

### 3. Set the public SEEK address

SEEK is served on its own host name. Set it once at install time so that NExtSEEK's links and SEEK's own identifiers agree.

- Install with `./startup.sh install --seek-public-url https://seek.example.org`. Give the host only, with no path. On a laptop, leave the flag out.
- Do this at the first install, before items 2, 4 and 6. Running install again writes `docker/db.env`, `docker/nextseek.env` and `dmac/local_settings.py` again from their templates, which undoes those edits.
- Check with `./startup.sh doctor` (the "SEEK public URL" line).

### 4. Rotate credentials

- **MySQL.** Change the passwords inside the running database (`ALTER USER` for the root and app users), then edit `docker/db.env` to match and run `docker compose up -d --no-deps --force-recreate nextseek seek seek_workers` (all three read that file). Leave user names and database names alone. Do not use `reset --keep-config` for this: it writes the demo passwords back.
- **Neo4j.** The password is stored in the Neo4j volume after the first start. Change it with a Cypher `ALTER CURRENT USER`, then update both password variables in `docker/nextseek.env` and recreate `nextseek`. Steps: `docs/neo4j-programmatic-access.md` in the repository.
- **Django secret key.** Install generates one. If it was ever logged or shared, put a new random 64-character value in `DJANGO_SECRET_KEY` in `docker/nextseek.env` and recreate `nextseek`. Everyone is signed out and old password-reset links stop working.

### 5. Add TLS

The built-in nginx serves plain HTTP on localhost. Put a TLS-terminating reverse proxy in front of it, pointing your `https://` address at `http://localhost:8000`. Caddy (automatic certificates), nginx with certbot, or a Cloudflare Tunnel all work.

- Set `DJANGO_CSRF_TRUSTED_ORIGINS` to the `https://` address (item 2).

### 6. Add LLM keys for Nessie

The chat assistant stays off until you add at least one key in `docker/nextseek.env`.

- `GCP_API_KEY` for Google Gemini, `AWS_BEARER_TOKEN_BEDROCK` for AWS Bedrock, `FDH_API` for the FAIRDOMHub API.
- For AWS Bedrock, put the same token in `NessieAI/docker/bedrock-proxy/proxy-secret.env` too: the assistant's sandboxed side reads only that file. If you export `AWS_BEARER_TOKEN_BEDROCK` before you run install, install writes it there for you. Apply that file with `docker compose up -d --no-deps --force-recreate bedrock-proxy`.

Apply: `docker compose up -d --no-deps --force-recreate nextseek`. See [Nessie](nessie.md).

### 7. Set up backups

Back up three things on a schedule:

- The MySQL databases `dmac` and `seek_production` (`mysqldump`).
- The Neo4j graph.
- The SEEK file store (uploads and blobs).

The commands are in `NExtSTEPS.md` in the repository. Run `mysqldump` through `docker compose exec -T` so the dump is not corrupted.

### 8. Update safely

- Pull the new code, then run `./startup.sh rebuild`. It keeps your data.
- If CSS or JavaScript changed, also run `docker compose exec nextseek uv run manage.py collectstatic --noinput`.
- Check the instance with `./startup.sh doctor`.

!!! note
    A rebuild empties the app's media folder, which holds batch-upload jobs in progress. Do not rebuild while an upload is running.

## Where each setting lives

| Setting | File | Apply with |
|---|---|---|
| Demo user passwords | SEEK web interface | Immediate |
| MySQL passwords | `docker/db.env`, plus `ALTER USER` in the database | Recreate `nextseek`, `seek` and `seek_workers` |
| Neo4j password | `docker/nextseek.env`, plus Cypher in the database | Recreate `nextseek` |
| Django secret, allowed hosts, CSRF origins | `docker/nextseek.env` | Recreate `nextseek` |
| LLM keys | `docker/nextseek.env` | Recreate `nextseek` |
