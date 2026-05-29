# Lean Container-Claude-Code (CC) runner image for the NExtSEEK dmac_assistant
# integration. Runs headless Claude Code over stream-json with Bedrock auth.
#
# This is a deliberately minimal image: the NS (chat_nextseek) route runs
# in-process in Django, so the CC container only needs the `claude` CLI + an
# auth path (Bedrock) + a writable scratch dir. The full dmac_assistant POC
# image (node + uv + torch + sentence-transformers + baked nextseek plugin) is
# the richer production target; this lean image proves the CC wiring end to end.
#
# Auth + model are injected per-container at run time (CLAUDE_CODE_USE_BEDROCK=1,
# AWS_BEARER_TOKEN_BEDROCK, AWS_REGION, --model <bedrock-id>), never baked.
FROM --platform=linux/amd64 node:20-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
      curl \
      jq \
    && rm -rf /var/lib/apt/lists/*

# Pin to the same Claude Code version dmac_assistant's POC image uses.
RUN npm install -g @anthropic-ai/claude-code@2.1.92

# Non-root sandbox user; /data/scratch is the rw working area (bind-mounted).
RUN useradd -m -u 1001 -s /bin/bash user \
    && mkdir -p /data/scratch \
    && chown -R user:user /data

USER user
WORKDIR /home/user

# Default command is overridden per turn by the CC engine (adds --model,
# --resume). Kept here so the image is runnable standalone for smoke tests.
CMD ["claude", "--print", "--input-format", "stream-json", \
     "--output-format", "stream-json", "--verbose", \
     "--dangerously-skip-permissions"]
