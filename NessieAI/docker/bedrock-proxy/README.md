# `NessieAI/docker/bedrock-proxy/`

## What this is

The Bedrock auth proxy: a small FastAPI relay that holds the institutional Bedrock bearer token and
attaches it on the way upstream, so the Container-CC agent carries no AWS credential. Compose
service `bedrock-proxy`, image `nextseek-bedrock-proxy:latest`.

## Surface

| Path | What it is |
|---|---|
| `Dockerfile` | the image build; the build context is this folder |
| `app/proxy.py` | the relay: exact-match allow list of model paths, bounded request bodies, no client `Authorization` header passed on, a redacting logger, `/healthz` |
| `app/config.py` | `ProxyConfig`, read from env at start (region, allowed models, timeouts, size cap) |
| `proxy-secret.env.example` | the key names of the secret file; copy to `proxy-secret.env` (untracked) and fill in `AWS_BEARER_TOKEN_BEDROCK` and `AWS_REGION` |
| `PORT-EVIDENCE.json` | the record of the port from the source repo, pinned by `NessieAI/tests/cc/test_step7_proxy_port.py` |

The real `proxy-secret.env` is never committed and never baked into the image; compose injects it at run time.

## Running and testing

```bash
./startup.sh rebuild --component bedrock-proxy
```

(`DEPLOYMENT.md` §3.2.) The allow list, failover and port guards are tested in `NessieAI/tests/cc/`
(`test_cc_proxy_allow_list.py`, `test_bedrock_proxy_failover.py`); lanes are in `NessieAI/tests/README.md`.

## Depends on / depended on by

The CC agent container reaches it by the compose service name `bedrock-proxy`; `NessieAI/cc/cc_engine.py`
sets that up per turn. Parent doc: `NessieAI/docker/README.md`.
