# The rate-limit handler is not part of the standard Caddy distribution.
# Keep both Caddy and the module immutable so a rebuild does not silently pick
# up a different ingress binary. The module commit was published 2026-06-12.
ARG CADDY_VERSION=2.11.4
ARG CADDY_RATELIMIT_COMMIT=5625512f24f6f59d6f64fb3aafe5eecff0b286db

FROM caddy:${CADDY_VERSION}-builder AS builder
ARG CADDY_VERSION
ARG CADDY_RATELIMIT_COMMIT
RUN xcaddy build v${CADDY_VERSION} \
    --with github.com/mholt/caddy-ratelimit@${CADDY_RATELIMIT_COMMIT}

FROM caddy:${CADDY_VERSION}-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
