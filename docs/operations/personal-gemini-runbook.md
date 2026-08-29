# Personal Gemini operations

## Release boundary

Personal Gemini is an optional Paperless-only feature. Local upload, OCR,
Tantivy search, Ollama BGE-M3 indexing, hybrid retrieval, the Odoo bridge, and
both MCP endpoints operate without Gemini. Odoo has no document-chat UI and
never receives a Gemini API key.

The only allowed external endpoint is
`https://generativelanguage.googleapis.com/v1beta/openai/`. The production
allowlist contains the stable model IDs `gemini-3.7-flash` and
`gemini-3.6-flash`; the former is the default. Never replace either with a
`latest`, preview, or experimental alias. Before each release, recheck the IDs
against Google's [model catalogue](https://ai.google.dev/gemini-api/docs/models)
and [deprecation schedule](https://ai.google.dev/gemini-api/docs/deprecations).

## User journey and privacy

An eligible user opens **My profile → Personal Gemini**, enters their own API
key, selects an approved model, and enables metadata suggestions, document
chat, or both. No administrator approval is required. Eligibility is resolved
from the current active Paperless account, the internal Documents base group,
and its governed Pocket social-account mapping. Portal, anonymous, inactive,
unmapped, and service identities are excluded.

The screen discloses that relevant document text, filename, metadata, and the
user's prompt leave USL and are processed by Google only when an enabled
personal feature is invoked. Suggestions never apply metadata automatically.
Chat has no tools and cannot modify documents. Treat every document as
untrusted input; document text cannot authorize an action or alter access.

**Test connection** retrieves only the provider's model list. It sends no
document or prompt. **Disable both** revokes generation while retaining the
encrypted credential. **Delete API key** disables both features and erases all
credential ciphertext, nonce, wrapped-key, key-identity, and test-time fields.

## Master-key secret

Generate and store the key ring in the deployment secret manager, never in
Git, an env file, a command line, Odoo, MCP, or a Paperless export. The mounted
file has this schema:

```json
{
  "format": "usl-paperless-personal-ai-keys-v1",
  "active_key_id": "deployment-key-id",
  "active_key_version": 1,
  "keys": [
    {"id": "deployment-key-id", "version": 1, "key": "BASE64_OF_32_RANDOM_BYTES"}
  ]
}
```

The deployment points `USL_PERSONAL_AI_MASTER_KEYS_PATH` at the read-only
Docker secret `/run/secrets/usl_personal_ai_master_keys`. The variable contains
only the path. Do not use a name ending in `_FILE`: Paperless's container entry
point interprets that suffix and copies file contents into process environment.
The release check explicitly rejects the legacy inline variables.

For migration QA, `migration/manage` validates the explicit ignored
`.documents-personal-ai-keys.json` and records its path with the runtime. The
file remains mode `0600`; key material is never copied into runtime JSON or
printed. Production must instead set
`USL_PERSONAL_AI_MASTER_KEYS_HOST_PATH` to an existing file populated by the
approved secret manager. Production reconstruction fails before resetting the
target if that explicit key ring is absent or malformed; it never generates a
production master key.

Run after every deploy and restore:

```bash
python manage.py check_personal_ai_release
```

Success prints only the active non-secret key ID and version. It fails if the
key ring is malformed, a native global LLM setting is populated, or key
material was copied into an environment variable.

## Rotation

1. Back up the Paperless database and the current key ring as two independently
   protected artifacts. A database backup without its historical key versions
   cannot recover personal credentials.
2. Add a new 32-byte random key with an incremented version; keep every old key
   referenced by a profile. Change `active_key_version` to the new version.
3. Deploy the secret and recreate Paperless. Run the release check.
4. Credential resolution lazily unwraps the existing data key and rewraps it
   under the active master key. It never re-encrypts the user's API key.
5. Count profiles whose `(master_key_id, master_key_version)` differs from the
   active pair. Exercise or explicitly rotate the remaining profiles through a
   controlled management procedure before removing an old key.
6. Remove an old key only after that count is zero and the post-rotation backup
   has been independently restored and checked.

Never rotate by rewriting ciphertext directly. A missing old master key is not
recoverable from the database; disable/delete the affected credentials and ask
their owners to enter new keys.

## Incident response and revocation

- Suspected personal API-key exposure: the user revokes it at Google, selects
  **Delete API key**, creates a replacement, and retests. Administrators cannot
  retrieve or impersonate the old credential.
- Suspected master-key exposure: disable the Paperless webserver, preserve
  forensic database and secret snapshots, revoke affected Google credentials,
  rotate the master key, and require users to replace their keys. Do not log or
  export encrypted fields during triage.
- Eligibility or document-access loss: Odoo/Pocket synchronization revokes
  Paperless grants. Each background or streaming request rechecks the current
  active mapped identity, feature toggle, and document permissions; loss stops
  further provider output.
- Gemini outage: personal generation returns a bounded unavailable response.
  Upload, OCR, indexing, lexical/semantic search, archive operations, and MCP
  remain available and must be tested separately.

## Backup, restore, and validation

The Paperless database backup includes encrypted user profiles; the separately
protected key ring is required to open them. Do not include either user API
keys or the master-key ring in ordinary Paperless export archives. After an
independent restore, mount the key ring, run migrations and the release check,
then prove the following before enabling traffic:

- both toggles remain off for a user who never opted in;
- a restored configured user sees only `api_key_configured=true`, never key
  material;
- one user's ciphertext cannot be opened as another user;
- disable/delete take effect immediately;
- the connection test touches only `/models`;
- provider exceptions contain no credential in responses, logs, or chains;
- an unmapped/inactive/service identity receives HTTP 403;
- search, indexing and MCP pass with Gemini unreachable.

The unit gate is:

```bash
python manage.py test paperless_personal_ai.tests -v 2
```

The frontend gate is the exact-source, pinned-Node Jest spec
`personal-ai-settings.component.spec.ts`, followed by the all-locale production
build. French is maintained in the hash-guarded upstream XLIFF catalogue.
