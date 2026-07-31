"""
DSpace REST API client.

Maintains a persistent requests.Session with:
  - Automatic CSRF cookie management (DSPACE-XSRF-COOKIE / X-XSRF-TOKEN)
  - JWT Bearer token injected into every request after login
  - Automatic re-login on 401 Unauthorized responses (token expiry)

Authentication flow (DSpace 7+):
  1. GET  /api/authn/status  → server sets DSPACE-XSRF-COOKIE
  2. POST /api/authn/login   → sends credentials + X-XSRF-TOKEN header
                              → server returns JWT in Authorization header
  3. All subsequent requests include: Authorization: Bearer <JWT>
"""

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class DSpaceClient:
    """Minimal authenticated client for the DSpace 7+ REST API."""

    def __init__(self, base_url: str, email: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password

        # requests.Session preserves cookies automatically (required for CSRF)
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.jwt: str | None = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _get_csrf_token(self) -> str:
        """
        GET /api/authn/status to trigger the server to set the CSRF cookie,
        then return the token value.
        """
        url = f"{self.base_url}/api/authn/status"
        resp = self.session.get(url)
        resp.raise_for_status()

        csrf = (
            resp.headers.get("DSPACE-XSRF-TOKEN")
            or resp.headers.get("X-XSRF-TOKEN")
            or self.session.cookies.get("DSPACE-XSRF-COOKIE")
        )
        if not csrf:
            raise RuntimeError(
                "Could not obtain CSRF token. "
                "Verify that the DSpace server is running and accessible."
            )
        return csrf

    def login(self) -> None:
        """Authenticate against DSpace and store the JWT for subsequent requests."""
        logger.info("Obtaining CSRF token...")
        csrf = self._get_csrf_token()

        logger.info("Logging in as %s...", self.email)
        url = f"{self.base_url}/api/authn/login"
        resp = self.session.post(
            url,
            data={"user": self.email, "password": self.password},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-XSRF-TOKEN": csrf,
            },
        )

        if resp.status_code == 200:
            auth_header = resp.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                self.jwt = auth_header[len("Bearer "):]
                self.session.headers.update({"Authorization": f"Bearer {self.jwt}"})
                logger.info("Login successful. JWT obtained.")
            else:
                raise RuntimeError(
                    "Login returned 200 but Authorization header is missing."
                )
        elif resp.status_code == 401:
            raise PermissionError(
                f"Login failed (401). Check DSPACE_EMAIL and DSPACE_PASSWORD.\n"
                f"WWW-Authenticate: {resp.headers.get('WWW-Authenticate', 'N/A')}"
            )
        else:
            resp.raise_for_status()

    def _relogin(self) -> None:
        """Drop the current session state and perform a fresh login."""
        logger.warning("JWT expired or invalid. Re-authenticating...")
        self.jwt = None
        self.session.headers.pop("Authorization", None)
        # Re-fetch CSRF cookie (old one may be invalid too)
        self.session.cookies.clear()
        self.login()

    # ------------------------------------------------------------------
    # CSRF helper for write operations
    # ------------------------------------------------------------------

    def _csrf(self) -> str:
        """Return the current CSRF token from the session cookie."""
        token = self.session.cookies.get("DSPACE-XSRF-COOKIE", "")
        if not token:
            # Cookie was cleared; refresh it
            token = self._get_csrf_token()
        return token

    # ------------------------------------------------------------------
    # HTTP helpers with auto-refresh on 401
    # ------------------------------------------------------------------

    def get(self, path: str, params: dict | None = None, stream: bool = False) -> Any:
        """Perform a GET request. Re-authenticates once on 401."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, params=params, stream=stream)
        if resp.status_code == 401:
            self._relogin()
            resp = self.session.get(url, params=params, stream=stream)
        resp.raise_for_status()
        if stream:
            return resp  # Return raw response for binary/streaming content
        # 204 No Content — return empty dict (used by search/item endpoints)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def get_content(self, path: str) -> bytes:
        """GET raw bytes (used to download bitstream content like CSV files)."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url)
        if resp.status_code == 401:
            self._relogin()
            resp = self.session.get(url)
        resp.raise_for_status()
        return resp.content

    def post(self, path: str, json: dict | None = None, data: dict | None = None,
             files: dict | None = None, extra_headers: dict | None = None) -> Any:
        """Perform a POST request with CSRF token. Re-authenticates once on 401."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"X-XSRF-TOKEN": self._csrf()}
        if extra_headers:
            headers.update(extra_headers)

        resp = self.session.post(url, json=json, data=data, files=files, headers=headers)
        if resp.status_code == 401:
            self._relogin()
            headers["X-XSRF-TOKEN"] = self._csrf()
            resp = self.session.post(url, json=json, data=data, files=files, headers=headers)
        resp.raise_for_status()

        # Some endpoints return 201/204 with no body
        if resp.content and resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {"status_code": resp.status_code}

    def put(self, path: str, json: dict | None = None,
            extra_headers: dict | None = None) -> Any:
        """Perform a PUT request with CSRF token. Re-authenticates once on 401."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "X-XSRF-TOKEN": self._csrf(),
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        resp = self.session.put(url, json=json, headers=headers)
        if resp.status_code == 401:
            self._relogin()
            headers["X-XSRF-TOKEN"] = self._csrf()
            resp = self.session.put(url, json=json, headers=headers)
        resp.raise_for_status()

        if resp.content and resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {"status_code": resp.status_code}

    def patch(self, path: str, json: list | None = None,
              extra_headers: dict | None = None) -> Any:
        """Perform a PATCH request (JSON-Patch) with CSRF token. Re-authenticates once on 401."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "X-XSRF-TOKEN": self._csrf(),
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)

        resp = self.session.patch(url, json=json, headers=headers)
        if resp.status_code == 401:
            self._relogin()
            headers["X-XSRF-TOKEN"] = self._csrf()
            resp = self.session.patch(url, json=json, headers=headers)
        resp.raise_for_status()

        if resp.content and resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {"status_code": resp.status_code}

    def delete(self, path: str, params: dict | None = None) -> None:
        """Perform a DELETE request with CSRF token. Re-authenticates once on 401."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"X-XSRF-TOKEN": self._csrf()}

        resp = self.session.delete(url, params=params, headers=headers)
        if resp.status_code == 401:
            self._relogin()
            headers["X-XSRF-TOKEN"] = self._csrf()
            resp = self.session.delete(url, params=params, headers=headers)
        resp.raise_for_status()

    def post_uri_list(self, path: str, uri: str) -> Any:
        """
        Perform a POST with Content-Type: text/uri-list body.
        Used by DSpace to:
          - Submit a workspace item to workflow (POST /api/workflow/workflowitems)
          - Claim a pool task (POST /api/workflow/claimedtasks)
        Returns the parsed JSON body, or an empty dict for 204 No Content responses.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "X-XSRF-TOKEN": self._csrf(),
            "Content-Type": "text/uri-list",
        }

        resp = self.session.post(url, data=uri, headers=headers)
        if resp.status_code == 401:
            self._relogin()
            headers["X-XSRF-TOKEN"] = self._csrf()
            resp = self.session.post(url, data=uri, headers=headers)
        resp.raise_for_status()

        if resp.content and resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {}
