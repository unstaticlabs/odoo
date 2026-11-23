# syntax=docker/dockerfile:1.7

# Pocket ID v2.19.2 with the USL fresh-passkey patch. The build stages mirror
# upstream's docker/Dockerfile for this release; only the source checkout, the
# patch and the test stage are ours.
# Renovate reads the version and the commit of the tag together
# (renovate.json). A new release changes both in one pull request, and the
# patch then fails `git apply --check` until someone rebases it.
# renovate: datasource=github-tags depName=pocket-id/pocket-id
ARG POCKET_ID_VERSION=v2.19.2
ARG POCKET_ID_COMMIT=2b2750ed61e0016f4c15630322d15ca22143cdb6

FROM alpine/git:2.49.1 AS source
ARG POCKET_ID_COMMIT
WORKDIR /src
RUN git clone --filter=blob:none https://github.com/pocket-id/pocket-id.git . \
    && git checkout --detach "${POCKET_ID_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${POCKET_ID_COMMIT}"
COPY services/usl-pocket-id/pocket-id-v2.19.2-fresh-passkey.patch /tmp/fresh-passkey.patch
RUN git apply --check /tmp/fresh-passkey.patch \
    && git apply /tmp/fresh-passkey.patch \
    && rm -rf .git

FROM --platform=$BUILDPLATFORM ghcr.io/voidzero-dev/vite-plus:1.4.0 AS frontend-builder
ENV CI=true
WORKDIR /app
COPY --from=source --chown=vp:vp /src/package.json /src/pnpm-workspace.yaml /src/pnpm-lock.yaml /src/.node-version ./
COPY --from=source --chown=vp:vp /src/frontend/package.json ./frontend/
COPY --from=source --chown=vp:vp /src/tests/package.json ./tests/
COPY --from=source --chown=vp:vp /src/email-templates/package.json ./email-templates/
RUN --mount=type=cache,id=pocket-id-pnpm,target=/home/vp/.local/share/pnpm/store,sharing=locked,uid=1000,gid=1000 \
    vp -C frontend install --frozen-lockfile --prefer-offline
COPY --from=source --chown=vp:vp /src/frontend ./frontend/
RUN --mount=type=cache,target=/app/frontend/node_modules/.vite,uid=1000,gid=1000 \
    --mount=type=cache,target=/app/frontend/.svelte-kit,uid=1000,gid=1000 \
    BUILD_OUTPUT_PATH=dist vp -C frontend run build

FROM golang:1.27.3-alpine AS backend-builder
ARG BUILD_TAGS=""
WORKDIR /build
COPY --from=source /src/backend/go.mod /src/backend/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY --from=source /src/backend ./
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist
COPY --from=source /src/.version .version
WORKDIR /build/cmd
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    VERSION="$(cat /build/.version)-usl1" \
    && CGO_ENABLED=0 GOOS=linux go build \
    -tags "${BUILD_TAGS}" \
    -ldflags="-X github.com/pocket-id/pocket-id/backend/internal/common.Version=${VERSION} -buildid=${VERSION}" \
    -trimpath -o /build/pocket-id .

FROM backend-builder AS backend-test
WORKDIR /build
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go test -tags=exclude_frontend,unit ./internal/common ./internal/controller ./internal/oidc ./internal/service ./internal/webauthn

FROM alpine:3.24.3
WORKDIR /app
RUN apk add --no-cache curl su-exec
COPY --from=backend-builder /build/pocket-id /app/pocket-id
COPY --from=source /src/scripts/docker /app/docker
RUN chmod +x /app/pocket-id && find /app/docker -name "*.sh" -exec chmod +x {} \;
EXPOSE 1411
ENV APP_ENV=production
HEALTHCHECK --interval=90s --timeout=5s --start-period=10s --retries=3 CMD ["/app/pocket-id", "healthcheck"]
ENTRYPOINT ["sh", "/app/docker/entrypoint.sh"]
CMD ["/app/pocket-id"]
