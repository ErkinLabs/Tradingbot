"""Tests for dashboard auth middleware and health endpoint."""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard.server import app


class TestDashboardAuth(unittest.TestCase):

    def test_health_no_auth_required(self):
        client = TestClient(app)
        r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    @patch("dashboard.auth.config.DASHBOARD_API_KEY", "secret-key")
    def test_api_rejects_missing_key(self):
        client = TestClient(app)
        r = client.get("/api/stats")
        self.assertEqual(r.status_code, 401)

    @patch("dashboard.auth.config.DASHBOARD_API_KEY", "secret-key")
    def test_api_accepts_valid_key(self):
        client = TestClient(app)
        r = client.get("/api/stats", headers={"X-API-Key": "secret-key"})
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
