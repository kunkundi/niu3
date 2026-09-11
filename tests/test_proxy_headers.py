import os
import runpy
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.dashboard.api import create_app
from tests.helpers import Fixture, at


class ProxyHeadersTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        with patch.dict(os.environ, {"NIUNO3_ADMIN_PASSWORD": "proxy-test-password-2026"}):
            self.app = create_app(self.f.db, self.f.calendar, clock=at)

    def tearDown(self):
        self.f.close()

    def client(self, trusted, peer="172.19.0.1"):
        with patch.dict(os.environ, {"NIUNO3_TRUSTED_PROXIES": trusted}), patch("uvicorn.run") as run:
            runpy.run_module("app.entrypoints.dashboard", run_name="__main__")
        options = run.call_args.kwargs
        app = self.app
        if options["proxy_headers"]:
            app = ProxyHeadersMiddleware(app, trusted_hosts=options["forwarded_allow_ips"])
        return TestClient(app, base_url="http://niualpha.com", client=(peer, 12345))

    def login(self, client, **headers):
        return client.post(
            "/api/v1/auth/login",
            json={"password": "proxy-test-password-2026"},
            headers={
                "X-NiuNo3-Request": "1",
                "Origin": "https://niualpha.com",
                "X-Forwarded-Proto": "https",
                "X-Forwarded-For": "203.0.113.25",
                **headers,
            },
        )

    def test_trusted_tunnel_allows_https_origin_and_sets_secure_cookie(self):
        with self.client("172.19.0.1") as client:
            response = self.login(client)
            self.assertEqual(response.status_code, 200)
            cookie = response.headers["set-cookie"]
            for flag in ("Secure", "HttpOnly", "SameSite=strict"):
                self.assertIn(flag, cookie)
            self.assertEqual(self.login(client, Origin="https://unrelated.example").status_code, 403)

    def test_untrusted_peer_and_disabled_proxy_cannot_spoof_https(self):
        for trusted, peer in (("172.19.0.1", "198.51.100.10"), ("", "172.19.0.1")):
            with self.subTest(trusted=trusted, peer=peer), self.client(trusted, peer) as client:
                self.assertEqual(self.login(client).status_code, 403)
                local = self.login(client, Origin="http://niualpha.com")
                self.assertEqual(local.status_code, 200)
                self.assertNotIn("Secure", local.headers["set-cookie"])

    def test_login_rate_limit_uses_forwarded_client_not_tunnel_peer(self):
        with self.f.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO login_attempts VALUES(?,?)",
                [("203.0.113.25", at().isoformat())] * 10,
            )
        with self.client("172.19.0.1") as client:
            self.assertEqual(self.login(client).status_code, 429)
            self.assertEqual(self.login(client, **{"X-Forwarded-For": "203.0.113.26"}).status_code, 200)
