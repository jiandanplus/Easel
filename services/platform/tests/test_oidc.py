from urllib.parse import parse_qs, urlsplit

import pytest
from platform_service.errors import PlatformError
from test_identity import identity as identity_fixture, challenge, login

identity = identity_fixture


@pytest.mark.parametrize(
    "claims",
    [
        {"nonce": "wrong"},
        {"aud": "wrong"},
        {"iss": "https://wrong.example"},
        {"exp": 1},
        {"sub": ""},
    ],
)
async def test_invalid_id_token(identity, claims):
    with pytest.raises(PlatformError):
        await login(identity, **claims)


async def test_algorithm_confusion_rejected(identity):
    identity[1].algorithm = "HS256"
    with pytest.raises(PlatformError):
        await login(identity)


@pytest.mark.parametrize("invalid", ["cookie", "state", "product"])
async def test_correlation_rejected(identity, invalid):
    service, provider = identity
    tx = await service.begin_login(
        "easel",
        "desktop",
        "http://127.0.0.1:37689/platform-auth/callback",
        challenge("A" * 48),
        "local-state",
    )
    auth = await service.authorize(tx["transaction_id"])
    params = parse_qs(urlsplit(auth["url"]).query)
    provider.params = {**params, "product": "easel"}
    with pytest.raises(PlatformError):
        await service.callback(
            "second" if invalid == "product" else "easel",
            "code",
            "wrong" if invalid == "state" else params["state"][0],
            "wrong" if invalid == "cookie" else auth["cookie"],
        )


async def test_callback_is_once_and_oidc_pkce_separate(identity):
    service, provider = identity
    tx = await service.begin_login(
        "easel",
        "desktop",
        "http://127.0.0.1:37689/platform-auth/callback",
        challenge("A" * 48),
        "local-state",
    )
    auth = await service.authorize(tx["transaction_id"])
    provider.params = {**parse_qs(urlsplit(auth["url"]).query), "product": "easel"}
    assert provider.params["code_challenge"] != [challenge("A" * 48)]
    await service.callback("easel", "code", provider.params["state"][0], auth["cookie"])
    with pytest.raises(PlatformError):
        await service.callback(
            "easel", "code", provider.params["state"][0], auth["cookie"]
        )


async def test_unverified_email_is_explicit(identity):
    result = await login(identity, email_verified="true")
    assert result["account"]["email_verified"] is False


async def test_wrong_signature_rejected(identity):
    from cryptography.hazmat.primitives.asymmetric import rsa
    import jwt
    import httpx

    service, provider = identity
    original = provider.handler
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def handler(request):
        response = original(request)
        if request.url.path.endswith("token"):
            claims = jwt.decode(
                response.json()["id_token"], options={"verify_signature": False}
            )
            return httpx.Response(
                200,
                json={
                    "id_token": jwt.encode(
                        claims, wrong, algorithm="RS256", headers={"kid": "fixture"}
                    )
                },
            )
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service.oidc.client = client
        with pytest.raises(PlatformError):
            await login(identity)


@pytest.mark.parametrize(
    "failure",
    ["redirect", "oversized", "invalid_json", "timeout", "issuer", "remote_endpoint"],
)
async def test_discovery_transport_boundaries(identity, failure):
    import httpx

    service, provider = identity

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture")
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://evil.example"})
        if failure == "oversized":
            return httpx.Response(200, content=b" " * 262145)
        if failure == "invalid_json":
            return httpx.Response(200, content=b"broken")
        result = provider.handler(request).json()
        if failure == "issuer":
            result["issuer"] = "https://evil.example"
        if failure == "remote_endpoint":
            result["token_endpoint"] = "https://evil.example/token"
        return httpx.Response(200, json=result)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service.oidc.client = client
        with pytest.raises(PlatformError):
            await service.oidc.discovery()


async def test_real_http_signed_provider_complete_exchange(identity):
    import asyncio
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import httpx

    service, provider = identity

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = httpx.Request(
                self.command,
                provider.issuer.removesuffix("/oidc") + self.path,
                content=body,
            )
            response = provider.handler(request)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider.issuer = f"http://127.0.0.1:{server.server_port}/oidc"
    service.settings.issuer = provider.issuer
    service.settings.allow_loopback_http = True
    service.oidc.client = None
    try:
        result = await login(identity)
        scope = await service.authenticate(result["access_token"])
        assert (await service.me(scope))["account"]["email"] == "same@example.test"
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "kty",
        "crv",
        "alg",
        "use",
        "key_ops",
        "kid",
        "duplicate",
        "signature",
        "nonce",
        "issuer",
        "audience",
        "unallowed_algorithm",
    ],
)
async def test_es384_logto_signature_boundaries(identity, failure):
    import json
    import httpx
    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec

    service, provider = identity
    original = provider.handler
    private_key = ec.generate_private_key(ec.SECP384R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key()))
    jwk.update(kid="logto-es384", alg="ES384", use="sig", key_ops=["verify"])
    if failure in ("kty", "crv", "alg", "use", "key_ops", "kid"):
        jwk[failure] = {
            "kty": "RSA",
            "crv": "P-256",
            "alg": "RS256",
            "use": "enc",
            "key_ops": ["encrypt"],
            "kid": "wrong",
        }[failure]

    def handler(request):
        if request.url.path.endswith("jwks"):
            return httpx.Response(
                200, json={"keys": [jwk, jwk] if failure == "duplicate" else [jwk]}
            )
        response = original(request)
        if request.url.path.endswith("token"):
            claims = jwt.decode(
                response.json()["id_token"], options={"verify_signature": False}
            )
            if failure in ("nonce", "issuer", "audience"):
                claims[
                    {"nonce": "nonce", "issuer": "iss", "audience": "aud"}[failure]
                ] = "wrong"
            signing_key = (
                ec.generate_private_key(ec.SECP384R1())
                if failure == "signature"
                else private_key
            )
            algorithm = "ES256" if failure == "unallowed_algorithm" else "ES384"
            if failure == "unallowed_algorithm":
                signing_key = ec.generate_private_key(ec.SECP256R1())
            return httpx.Response(
                200,
                json={
                    "id_token": jwt.encode(
                        claims,
                        signing_key,
                        algorithm=algorithm,
                        headers={"kid": "logto-es384"},
                    )
                },
            )
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service.oidc.client = client
        if failure is None:
            result = await login(identity)
            assert result["account"]["email"] == "same@example.test"
        else:
            with pytest.raises(PlatformError, match="identity_token_invalid"):
                await login(identity)
