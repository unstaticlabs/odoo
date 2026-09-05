"""Shared client-admission probe injected into the Odoo shell."""

CLIENT_PROBE_SCRIPT = r'''
def synthetic_client_probe(client_id, client_secret, redirect_uri, token_auth_method):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    authorization = requests.get(
        provider.auth_endpoint,
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email groups",
            "state": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        allow_redirects=False,
        timeout=10,
    )
    authorization_detail = (
        authorization.headers.get("Location", "") + "\n" + authorization.text
    ).lower()
    authorization_accepted = (
        authorization.status_code in {200, 302, 303, 307, 308}
        and "error=invalid_client" not in authorization_detail
        and "invalid callback" not in authorization_detail
        and "invalid redirect" not in authorization_detail
    )
    data = {
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": "usl-admission-invalid-code",
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
    }
    auth = None
    if token_auth_method == "client_secret_post":
        data["client_secret"] = client_secret
    else:
        auth = (client_id, client_secret)
    token = requests.post(provider.token_endpoint, data=data, auth=auth, timeout=10)
    try:
        token_error = token.json().get("error")
    except ValueError:
        token_error = None
    secret_accepted = (
        token.status_code in {400, 401}
        and token_error == "invalid_grant"
    )
    return authorization_accepted, secret_accepted
'''
