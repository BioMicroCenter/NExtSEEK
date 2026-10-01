# API

NExtSEEK has a REST API for scripts and other programs. Almost everything the web app does with samples and metadata is also available there, under the `/nextseek_api/` path of the site.

## Live, interactive documentation

The API describes itself from the code, so these pages always match what the server does. They are the full list of endpoints, parameters and response shapes.

| Page | Path | Use it to |
|---|---|---|
| Swagger UI | `/nextseek_api/swagger/` | Browse and try requests in the browser |
| ReDoc | `/nextseek_api/redoc/` | Read the reference |
| OpenAPI schema | `/nextseek_api/schema/` | Generate a client |

!!! note
    All three need a signed-in session. In a browser that is signed out you get an authentication error, not a login page. Sign in to NExtSEEK first, then open the link. Requests you try in Swagger UI run as the account you are signed in with.

## What the API covers

* Samples and sample types
* Assays, assay registration, projects, investigations and studies
* People and users
* Data files and SOPs
* Sample search: advanced search and graph search
* Sample retrieval (download)
* Templates and batch upload
* The entity tree

## Authenticating from a script

A script sends credentials in the `Authorization` header. Use HTTP Basic with your NExtSEEK login. Many endpoints also accept a `Token` header, with a token an administrator issues. Inside a browser, the session cookie of a signed-in user works.

```bash
curl -u "<your-login>:<your-password>" "https://<your-site>/nextseek_api/projects/"
```

Replace `<your-site>` with the address of your NExtSEEK site. You see the same projects and samples through the API as in the web app: access follows your project membership.

## Legacy API

The older `/api/` routes are retired. Use the `/nextseek_api/` endpoints.

