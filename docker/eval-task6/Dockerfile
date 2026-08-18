# Network-free composite used only for Plan 018 V4-9 Task 6 acceptance.
#
# The deployed application image owns Django 5.2.15.  The independently built
# eval image owns the pinned JAX/NumPyro stack.  Django, asgiref and sqlparse
# are pure Python and support Python 3.12, so copy those exact deployed bytes
# into the eval image rather than downloading or changing either base image.
ARG APP_IMAGE=nextseek-nextseek:latest
ARG EVAL_IMAGE=nextseek-eval:v4-4
FROM ${APP_IMAGE} AS app

FROM ${EVAL_IMAGE}
COPY --from=app /app/.venv/lib/python3.14/site-packages/django /usr/local/lib/python3.12/site-packages/django
COPY --from=app /app/.venv/lib/python3.14/site-packages/asgiref /usr/local/lib/python3.12/site-packages/asgiref
COPY --from=app /app/.venv/lib/python3.14/site-packages/sqlparse /usr/local/lib/python3.12/site-packages/sqlparse
