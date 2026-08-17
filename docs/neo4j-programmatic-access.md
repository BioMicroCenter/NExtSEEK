# Querying the graph programmatically

Three ways to reach Neo4j. Pick by what your client speaks.

| | endpoint | tunnel? | for |
|---|---|---|---|
| **HTTP Query API** | `https://<host>/db/neo4j/query/v2` | no | scripts, any language |
| **Neo4j Browser** | `https://<host>/browser/` | no | humans, ad-hoc Cypher |
| **Bolt drivers** | `neo4j://localhost:7687` over SSH | **yes** | the official `neo4j` driver |

By default **none of these is exposed**. `docker-compose.yml` binds Neo4j to
`127.0.0.1` only, and the two HTTP paths ship disabled. Enable them with:

```bash
cp docker/nginx-optional/neo4j.conf.example docker/nginx-optional/neo4j.conf
docker compose restart nextseek_nginx
```

**Read that file's header before you do.** It has no access control of its own,
by design, and it exposes a console and an API that run arbitrary Cypher against
your graph. Neo4j Community has no read-only role, so the gate has to be an IP
allow-list or basic auth at your edge proxy.

## HTTP Query API

```bash
curl -u neo4j:"$NEO4J_PASSWORD" -H 'Content-Type: application/json' \
  -d '{"statement":"MATCH (s:Sample) RETURN count(s) AS n"}' \
  https://<host>/db/neo4j/query/v2
```

```json
{"data":{"fields":["n"],"values":[[12345]]},"bookmarks":["FB:kcw..."]}
```

Python, reshaping the columnar response into the dicts you probably want:

```python
import os, requests

URL = "https://<host>/db/neo4j/query/v2"
AUTH = ("neo4j", os.environ["NEO4J_PASSWORD"])

def cypher(statement, params=None):
    r = requests.post(URL, auth=AUTH, timeout=120,
                      json={"statement": statement, "parameters": params or {}},
                      headers={"Accept": "application/json"})
    r.raise_for_status()
    d = r.json()["data"]
    return [dict(zip(d["fields"], row)) for row in d["values"]]

rows = cypher(
    "MATCH (a:Sample)-[r:DERIVED_FROM]->(b:Sample) "
    "WHERE a.type = $t "
    "RETURN a.UID AS child, r.internal_assay_title AS assay, b.UID AS parent "
    "SKIP $skip LIMIT $lim",
    {"t": "CEL", "skip": 0, "lim": 1000},
)
```

Whole nodes and relationships keep full fidelity — labels, properties, type, and
both endpoints:

```json
{"elementId":"4:...:0","labels":["Sample"],"properties":{"UID":"...","type":"CEL"}}
{"elementId":"5:...:1","type":"DERIVED_FROM","startNodeElementId":"4:...:0",
 "endNodeElementId":"4:...:9","properties":{"internal_assay_title":"..."}}
```

### Two things that will bite you

- **No streaming**, and a **120s transaction timeout** is enforced (it exists
  because an unbounded generated query wedged the stack twice). The whole result
  is materialized, so page large pulls with `SKIP` / `LIMIT`.
- **Writes are not prevented.** Nothing server-side stops a `DELETE`. Keep write
  statements out of scripts pointed at a production instance.

## Neo4j Browser

Open `https://<host>/browser/`. Discovery at `/` returns the Django app rather
than Neo4j's JSON, so the connect URL does not autofill and the console logs
`SSO provider discovery attempt failed ... not valid JSON`. That message is
harmless. Enter the connection URL by hand; the browser remembers it.

Browser needs bolt, which is **not** covered by the drop-in above (see below).
Without a bolt route it will load and then fail to connect.

## Bolt drivers need a direct route

The official Python/Java/Go drivers speak **raw bolt over TCP**, not websockets,
so they cannot be reverse proxied under an HTTP path. Only Browser's JS driver
can, because it wraps bolt in a websocket.

Simplest option, no server change:

```bash
ssh -N -L 7687:127.0.0.1:7687 <your-host>
```

```python
from neo4j import GraphDatabase
# neo4j:// not neo4j+s:// — SSH provides encryption, the local end is plaintext
driver = GraphDatabase.driver("neo4j://localhost:7687",
                              auth=("neo4j", os.environ["NEO4J_PASSWORD"]))
with driver.session(database="neo4j") as s:
    print(s.run("MATCH (s:Sample) RETURN count(s) AS n").single()["n"])
driver.close()
```

Or run on the box against `neo4j://127.0.0.1:7687`, or `neo4j://neo4j:7687` from
inside the docker network.

If you want Browser working from outside without opening a bolt port, your edge
proxy can multiplex bolt onto 443 by routing websocket upgrades at exactly `/`
to Neo4j's bolt port and everything else to Django. Note the hazard: that claims
the bare root URL, so **never add an application websocket route at exactly `/`**
or bolt will shadow it. Subpaths, including this app's
`ws/assistant/progress/{task_id}/`, are unaffected.

## Passwords

- **First start:** `NEO4J_PASSWORD` in the repo-root `.env` (copy `.env.example`)
  seeds the credential when the `neo4j-data` volume is created.
- **The app:** `NEO4J_PASSWORD` and `NEXTSEEK_NEO4J_PASSWORD` in
  `docker/nextseek.env`. Both must match the database.

All of those files are gitignored. Keep it that way.

**Rotating is two steps**, because editing `NEO4J_AUTH` after first start does
nothing — the credential lives in the volume:

```bash
docker exec neo4j cypher-shell -u neo4j -p "$OLD" \
  "ALTER CURRENT USER SET PASSWORD FROM '$OLD' TO '$NEW'"

# update both vars in docker/nextseek.env, then RECREATE.
# `docker compose restart` will NOT re-read env_file:
docker compose up -d --force-recreate nextseek
```

There is a brief window in between where the app cannot reach the graph.
Community edition cannot hold two valid passwords, so it is unavoidable.
