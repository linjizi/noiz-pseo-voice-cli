"""Thin Payload CMS REST client (stdlib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class CmsError(RuntimeError):
    pass


class CmsClient:
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        email: Optional[str] = None,
        password: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.email = email
        self.password = password
        self.timeout = timeout
        self._jwt: Optional[str] = None

    def _auth_headers(self) -> dict[str, str]:
        if self.api_key:
            # Payload API-key convention: Authorization: users API-Key <key>
            return {"Authorization": f"users API-Key {self.api_key}"}
        if self.email and self.password:
            if not self._jwt:
                self._jwt = self._login()
            return {"Authorization": f"JWT {self._jwt}"}
        return {}

    def _login(self) -> str:
        url = f"{self.base_url}/api/users/login"
        body = json.dumps({"email": self.email, "password": self.password}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            raise CmsError(f"CMS login HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise CmsError(f"CMS login unreachable: {exc.reason}") from exc
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise CmsError("CMS login succeeded but returned no token")
        return str(token)

    def _request(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}/api{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        req = urllib.request.Request(url, method="GET")
        for key, value in self._auth_headers().items():
            req.add_header(key, value)
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                if not body:
                    return {}
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            raise CmsError(f"CMS HTTP {exc.code} on {url}") from exc
        except urllib.error.URLError as exc:
            raise CmsError(f"CMS unreachable: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise CmsError(f"CMS returned non-JSON from {url}") from exc

    def _write(self, path: str, method: str, fields: dict[str, Any]) -> Any:
        url = f"{self.base_url}/api{path}"
        body = json.dumps(fields).encode()
        req = urllib.request.Request(url, data=body, method=method)
        for key, value in self._auth_headers().items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise CmsError(f"CMS {method} HTTP {exc.code} on {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise CmsError(f"CMS {method} unreachable: {exc.reason}") from exc

    def create_record(self, fields: dict[str, Any]) -> dict[str, Any]:
        data = self._write("/voice-detail-pages", "POST", fields)
        return data.get("doc", data)

    def patch_record(self, record_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        data = self._write(f"/voice-detail-pages/{record_id}", "PATCH", fields)
        return data.get("doc", data)

    def ping(self) -> bool:
        self._request("/voice-detail-pages", {"limit": 1, "depth": 0})
        return True

    def list_records(
        self,
        filters: Optional[dict[str, Any]] = None,
        limit: int = 100,
        depth: int = 0,
        page: int = 1,
    ) -> tuple[list[dict[str, Any]], int]:
        params: dict[str, Any] = {"limit": limit, "depth": depth, "page": page}
        if filters:
            params["where"] = json.dumps(filters)
        data = self._request("/voice-detail-pages", params)
        docs = data.get("docs", [])
        return docs, int(data.get("totalDocs", len(docs)))

    def get_record(self, key: str, depth: int = 2) -> dict[str, Any]:
        key = key.strip()
        if key.isdigit():
            data = self._request(f"/voice-detail-pages/{key}", {"depth": depth, "draft": "false"})
            if isinstance(data, dict) and "doc" in data:
                return data["doc"]
            return data
        for field in ("voiceId", "canonicalSlug", "slug"):
            docs, _ = self.list_records(
                # Flat filter: list_records() puts `filters` under params["where"]
                # itself, so wrapping in {"where": ...} would double-nest and
                # Payload answers HTTP 400 (voices get / check, v0.3.0 bug).
                {field: {"equals": key}}, limit=1, depth=depth
            )
            if docs:
                return docs[0]
        raise CmsError(f"no voice-detail record found for {key!r}")

    def verify_credentials(self) -> None:
        """Raise CmsError unless the configured credentials authenticate.

        v0.4.0 (cathan 2026-08-08): the CMS has no role tiers, so the CLI does
        not either — login success is the gate for every command (scope is
        still limited to the voice-detail-pages chain).
        """
        if self.email and self.password:
            self._login()
            return
        if self.api_key:
            # Payload API-key convention: Authorization: users API-Key <key>.
            # /api/users/me is the standard auth probe.
            self._request("/users/me", {})
            return
        raise CmsError(
            "no CMS credentials configured "
            "(NOIZ_CMS_EMAIL/NOIZ_CMS_PASSWORD or NOIZ_CMS_API_KEY)"
        )
