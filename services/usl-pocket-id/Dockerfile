# syntax=docker/dockerfile:1.7

ARG POCKET_ID_COMMIT=5521266227205dccb57ba58d820c833a5701346d

FROM alpine/git:2.49.1 AS source
ARG POCKET_ID_COMMIT
WORKDIR /src
RUN git clone --filter=blob:none https://github.com/pocket-id/pocket-id.git . \
    && git checkout --detach "${POCKET_ID_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${POCKET_ID_COMMIT}"
COPY services/usl-pocket-id/pocket-id-v2.14.0-fresh-passkey.patch /tmp/fresh-passkey.patch
RUN git apply --check --unidiff-zero /tmp/fresh-passkey.patch \
    && git apply --unidiff-zero /tmp/fresh-passkey.patch

FROM node:24-alpine AS frontend-builder
RUN corepack enable
WORKDIR /build
COPY --from=source /src/package.json /src/pnpm-workspace.yaml /src/pnpm-lock.yaml ./
COPY --from=source /src/frontend/package.json ./frontend/
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm --filter pocket-id-frontend install --frozen-lockfile
COPY --from=source /src/frontend ./frontend/
RUN --mount=type=cache,target=/build/frontend/node_modules/.vite \
    --mount=type=cache,target=/build/frontend/.svelte-kit \
    BUILD_OUTPUT_PATH=dist pnpm --filter pocket-id-frontend run build

FROM golang:1.26.6-alpine AS backend-builder
ARG BUILD_TAGS=""
WORKDIR /build
COPY --from=source /src/backend/go.mod /src/backend/go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY --from=source /src/backend ./
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist
COPY --from=source /src/.version .version
WORKDIR /build/cmd
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    VERSION=$(cat /build/.version) \
    CGO_ENABLED=0 GOOS=linux go build \
    -tags "${BUILD_TAGS}" \
    -ldflags="-X github.com/pocket-id/pocket-id/backend/internal/common.Version=${VERSION} -buildid=${VERSION}" \
    -trimpath -o /build/pocket-id .

FROM backend-builder AS backend-test
WORKDIR /build
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go test -tags=exclude_frontend,unit ./internal/oidc ./internal/service ./internal/webauthn

FROM alpine:3.24.1
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
