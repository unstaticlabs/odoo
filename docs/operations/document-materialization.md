# Secure agent document materialization

## Boundary and lifecycle

`usl.document.download.grant` is the only supported sessionless document-file
boundary. An authenticated Odoo/MCP user explicitly asks
`usl.document.mcp_create_download_grant` for one document, one immutable
`usl.document.version`, and either its `original` or `archive` representation.
Odoo checks the normal document, linked-record, company, version, and binary
rules, probes that exact Paperless object, and returns a 256-bit opaque token.

Only the SHA-256 token hash is stored. The returned URL is a temporary bearer
secret. Its default lifetime is 300 seconds and the ordinary limit is 30–900
seconds. It may be replayed for GET, HEAD, retries, and byte ranges until it
expires or is revoked. Search, metadata, OCR, and version-list operations never
create a grant.

Every redemption restores the active issuing user and the bound company
context, then re-evaluates current authorization to the exact document/version.
Disabled users, removed companies, changed record rules, changed binary
identity, revoked grants, and expired grants fail as an indistinguishable 404.
Paperless remains private; Odoo forwards only validated Range/If-Range headers
and streams 64 KiB chunks without exposing its service token or internal URL.

Grant issuance, first successful materialization, revocation, and the first
denial of each category are immutable audit events. Repeated range requests
increment bounded grant counters instead of creating one audit row per byte
range. The daily cleanup removes grants and their audit rows 365 days after
expiry by default. Set
`usl_documents.download_grant_audit_retention_days` to 30–3650 only when the
retention policy requires it. Expiry and revocation are immediate and do not
depend on cleanup.

## Required Odoo configuration

Set the public origin once and freeze it:

```text
web.base.url = https://odoo.example.com
web.base.url.freeze = True
```

Issuance fails closed if the origin is not HTTPS, contains credentials, a path,
query or fragment, or is not frozen. The incoming `Host` header is never used.
The Paperless binary streaming timeout is configured with
`usl_documents.paperless_stream_timeout` and defaults to 60 seconds (bounded
internally to 1–600 seconds).

The Odoo port and `/usl_documents/materialize` route must not be publicly
reachable. The public proxy owns `/agent-documents/<token>` and is responsible
for keeping the token out of access logs. Application, trace, analytics, and
exception middleware must not record request headers for this route.

## Nginx reference contract

Place the exact-match materialization locations before the ordinary Odoo
location. The generic Odoo location must also strip the internal header.

```nginx
# Never expose the fixed internal controller.
location = /usl_documents/materialize {
    return 404;
}

location ~ ^/agent-documents/(?<usl_grant>[A-Za-z0-9_-]{43})$ {
    if ($is_args) { return 404; }
    limit_except GET HEAD { deny all; }

    access_log off;
    proxy_cache off;
    proxy_buffering off;
    add_header Cache-Control "private, no-store, max-age=0" always;

    # This assignment replaces any client-supplied value with the captured token.
    proxy_set_header X-USL-Document-Grant $usl_grant;
    proxy_set_header Range $http_range;
    proxy_set_header If-Range $http_if_range;
    rewrite ^ /usl_documents/materialize break;
    proxy_pass http://odoo:8069;
}

location ^~ /agent-documents/ {
    access_log off;
    return 404;
}

location / {
    proxy_set_header X-USL-Document-Grant "";
    proxy_pass http://odoo:8069;
}
```

Disable request-target capture in WAF, tracing, and error logs for the external
capability location as well; `access_log off` covers only Nginx access logging.

## Caddy reference contract

This reference discards query strings and skips access logging for every
capability-shaped request. The fallback proxy strips spoofed internal headers.

```caddyfile
odoo.example.com {
    @grant {
        path_regexp grant ^/agent-documents/([A-Za-z0-9_-]{43})$
        method GET HEAD
    }
    log_skip @grant

    route {
        respond /usl_documents/materialize 404

        handle @grant {
            uri strip_query
            rewrite * /usl_documents/materialize
            reverse_proxy odoo:8069 {
                header_up -X-USL-Document-Grant
                header_up X-USL-Document-Grant {re.grant.1}
                header_up Range {http.request.header.Range}
                header_up If-Range {http.request.header.If-Range}
                flush_interval -1
            }
        }

        handle /agent-documents/* {
            respond 404
        }

        reverse_proxy odoo:8069 {
            header_up -X-USL-Document-Grant
        }
    }
}
```

Confirm the deployed Caddy version supports `log_skip`; otherwise configure an
equivalent route-level access-log exclusion before enabling materialization.

## Conformance and smoke test

Before MCP rollout, verify all of the following through the public origin:

1. Odoo and Paperless listen only on the private network.
2. Only 43-character base64url tokens match the external route.
3. GET and HEAD work; other methods, malformed paths, and the fixed internal
   path fail without metadata.
4. Query strings are rejected or discarded, and client-supplied
   `X-USL-Document-Grant` is stripped everywhere else.
5. Proxy, Odoo, Paperless, WAF, trace, and analytics logs contain neither the
   raw token nor the external request target.
6. `curl -I` returns Content-Length, Content-Type, Content-Disposition, ETag
   when available, Accept-Ranges, and the no-store/no-referrer hardening.
7. `curl -H 'Range: bytes=0-1023'` returns 206 and the exact 1,024 bytes;
   an unsatisfiable range returns 416.
8. Repeated HEAD/range calls work within the TTL. Revocation, user deactivation,
   and company/record access removal make the same unexpired URL return 404.
9. Existing authenticated browser preview/download behavior still works.

Do not put the Paperless service token in the proxy and do not rewrite directly
to Paperless. Rollback removes the public ingress route and rolls back the MCP
image first; additive Odoo grant tables may remain. Existing grants then become
unavailable. Do not attempt a schema downgrade.

## Future MCP resource

This release intentionally does not add an `odoo-document://` MCP resource.
Binary resource contents base64-expand large PDFs, while resource links require
a separate authorization contract. A future proposal may expose the same bound
version through a client-negotiated streaming resource, but it must reuse this
authorization service and must not weaken explicit materialization.
