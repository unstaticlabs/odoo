{{- if ne .Subject.CommonName .Token.usl_subject }}{{ fail "The CSR subject is not authorized by the USL Sign token." }}{{ end -}}
{{- if not (regexMatch "^USL Sign Personal: .+$" .Token.usl_subject) }}{{ fail "The personal certificate subject is invalid." }}{{ end -}}
{{- if not (regexMatch "^[0-9]+$" .Token.usl_signer) }}{{ fail "The signer binding is invalid." }}{{ end -}}
{{- if not (regexMatch "^[0-9]+$" .Token.usl_enrollment) }}{{ fail "The enrolment binding is invalid." }}{{ end -}}
{{- if not (regexMatch "^[0-9]+$" .Token.usl_request) }}{{ fail "The request binding is invalid." }}{{ end -}}
{{- if not (regexMatch "^[0-9]+$" .Token.usl_role) }}{{ fail "The role binding is invalid." }}{{ end -}}
{{- if not (regexMatch "^[0-9a-f]{64}$" .Token.usl_document_sha256) }}{{ fail "The document digest is invalid." }}{{ end -}}
{{- if not (regexMatch "^[0-9a-f]{64}$" .Token.usl_policy_sha256) }}{{ fail "The policy digest is invalid." }}{{ end -}}
{{- if not (regexMatch "^[0-9a-f]{64}$" .Token.usl_public_key_sha256) }}{{ fail "The browser public-key digest is invalid." }}{{ end -}}
{
  "subject": {
    "commonName": {{ toJson .Token.usl_subject }}
  },
  "sans": [
    {"type": "uri", "value": {{ toJson (printf "urn:usl:signer:%s" .Token.usl_signer) }}},
    {"type": "uri", "value": {{ toJson (printf "urn:usl:enrollment:%s" .Token.usl_enrollment) }}},
    {"type": "uri", "value": {{ toJson (printf "urn:usl:request:%s" .Token.usl_request) }}},
    {"type": "uri", "value": {{ toJson (printf "urn:usl:role:%s" .Token.usl_role) }}},
    {"type": "uri", "value": {{ toJson (printf "urn:sha256:%s" .Token.usl_document_sha256) }}},
    {"type": "uri", "value": {{ toJson (printf "urn:usl:policy-sha256:%s" .Token.usl_policy_sha256) }}},
    {"type": "uri", "value": {{ toJson (printf "urn:usl:public-key-sha256:%s" .Token.usl_public_key_sha256) }}}
  ],
  "keyUsage": ["digitalSignature"],
  "extKeyUsage": ["emailProtection"]
}
