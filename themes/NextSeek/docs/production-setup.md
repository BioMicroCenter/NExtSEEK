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

In `docker/nextseek.env`, leave `DJANGO_DEBUG` unset, set `DJANGO_ALLOWED_HOSTS` to your host name only, and set `DJANGO_CSRF_TRUSTED_ORIGINS` to your `https://` address. Examples and the apply command: [NExtSTEPS 1b](https://github.com/BioMicroCenter/NExtSEEK/blob/main/NExtSTEPS.md#1b-make-sure-django_debug-is-unset-for-anything-internet-facing) and [1c](https://github.com/BioMicroCenter/NExtSEEK/blob/main/NExtSTEPS.md#1c-tighten-django_allowed_hosts-and-django_csrf_trusted_origins).

### 3. Set the public SEEK address

SEEK is served on its own host name. Set it once, at the first install, with `./startup.sh install --seek-public-url https://seek.example.org` (host only, no path; leave it out on a laptop), so that NExtSEEK's links and SEEK's own identifiers agree. Do this before items 2, 4 and 6: running install again writes the config files again from their templates, which undoes those edits. Details: [NExtSTEPS 1d](https://github.com/BioMicroCenter/NExtSEEK/blob/main/NExtSTEPS.md#1d-set-the-browser-reachable-seek-url---seek-public-url).

### 4. Rotate credentials

- **MySQL.** Change the passwords inside the running database, then in `docker/db.env`, and recreate the services that read it. Leave user names and database names alone, and do not use `reset --keep-config` for this: it writes the demo passwords back. Commands: [NExtSTEPS 2a](https://github.com/BioMicroCenter/NExtSEEK/blob/main/NExtSTEPS.md#2a-mysql-dockerdbenv).
- **Neo4j.** The password lives in the Neo4j volume after the first start, so editing files alone does nothing. Steps: [NExtSTEPS 2b](https://github.com/BioMicroCenter/NExtSEEK/blob/main/NExtSTEPS.md#2b-neo4j).
- **Django secret key.** Install generates one. If it was ever logged or shared, replace it; everyone is signed out and old password-reset links stop working. Steps: [NExtSTEPS 3](https://github.com/BioMicroCenter/NExtSEEK/blob/main/NExtSTEPS.md#3-django-secret-key).

### 5. Add TLS

The built-in nginx serves plain HTTP. Put a TLS-terminating reverse proxy (Caddy, nginx with certbot, or a Cloudflare Tunnel) in front of `http://localhost:8000`, and set `DJANGO_CSRF_TRUSTED_ORIGINS` to the `https://` address (item 2). Options: [NExtSTEPS 5](https://github.com/BioMicroCenter/NExtSEEK/blob/main/NExtSTEPS.md#5-tls--https).

### 6. Add LLM keys for Nessie

The chat assistant stays off until you add at least one key in `docker/nextseek.env`.

- `GCP_API_KEY` for Google Gemini, `AWS_BEARER_TOKEN_BEDROCK` for AWS Bedrock, `FDH_API` for the FAIRDOMHub API.
- For AWS Bedrock, put the same token in `NessieAI/docker/bedrock-proxy/proxy-secret.env` too: the assistant's sandboxed side reads only that file. If you export `AWS_BEARER_TOKEN_BEDROCK` before you run install, install writes it there for you. Apply that file with `docker compose up -d --no-deps --force-recreate bedrock-proxy`.

Apply: `docker compose up -d --no-deps --force-recreate nextseek`. See [Nessie](nessie.md).

### 7. Set up backups

Back up three things on a schedule: the MySQL databases `dmac` and `seek_production`, the Neo4j graph, and the SEEK file store (uploads and blobs). The commands, including the `-T` flag that keeps a MySQL dump from being corrupted: [NExtSTEPS 6](https://github.com/BioMicroCenter/NExtSEEK/blob/main/NExtSTEPS.md#6-backups).

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
