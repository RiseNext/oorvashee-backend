"""Thin async Cloudinary integration.

Two surfaces:
  - Pure-crypto signing + verification (no network).
  - Async `destroy()` for removing assets (only HTTP call we make).

We do NOT use the official `cloudinary` Python SDK:
  - It's sync-only and pulls in image-manipulation deps we don't need.
  - Signing is a trivial HMAC; destroy is one POST.
  - Stays lean: no surprise behaviour from a deep dep tree.

Reference:
  https://cloudinary.com/documentation/upload_images#generating_authentication_signatures
  https://cloudinary.com/documentation/admin_api#delete_resources
"""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.core.exceptions import IntegrationError, InvalidMediaSignatureError
from app.core.logging import get_logger

log = get_logger(__name__)

CLOUDINARY_API_BASE = "https://api.cloudinary.com/v1_1"
DESTROY_TIMEOUT_SECONDS = 8.0


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------


def _canonicalize(params: dict[str, Any]) -> str:
    """Cloudinary signing canonicalisation.

    Sort keys alphabetically, join `key=value` pairs with `&`. Skip `file`,
    `api_key`, `resource_type` and `signature` per Cloudinary docs.
    """
    skip = {"file", "api_key", "resource_type", "signature"}
    pairs = sorted(
        (k, _stringify(v))
        for k, v in params.items()
        if k not in skip and v is not None and v != ""
    )
    return "&".join(f"{k}={v}" for k, v in pairs)


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        # Cloudinary expects pipe-delimited lists for `eager`, `tags`, etc.
        return "|".join(str(v) for v in value)
    return str(value)


def sign_params(params: dict[str, Any], api_secret: str) -> str:
    """SHA-1 of canonical-params + secret. Cloudinary default algorithm.

    SHA-1 is correct here despite being weak elsewhere — Cloudinary's
    server-side verification expects exactly this. Their docs also support
    SHA-256 via `signature_algorithm=sha256`; we stick to SHA-1 because
    the official Cloudinary widget defaults to it.
    """
    canonical = _canonicalize(params)
    return hashlib.sha1((canonical + api_secret).encode("utf-8")).hexdigest()  # noqa: S324


def verify_upload_response(response: dict[str, Any], api_secret: str) -> bool:
    """Cloudinary signs every upload-success response.

    `signature = sha1(public_id=...&version=... + api_secret)`.
    """
    public_id = response.get("public_id")
    version = response.get("version")
    given_sig = response.get("signature")
    if not public_id or version is None or not given_sig:
        return False
    to_verify = f"public_id={public_id}&version={version}"
    expected = hashlib.sha1((to_verify + api_secret).encode("utf-8")).hexdigest()  # noqa: S324
    return hmac.compare_digest(expected, str(given_sig))


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SignedUploadEnvelope:
    """Everything the frontend needs to POST directly to Cloudinary."""

    cloud_name: str
    api_key: str
    timestamp: int
    signature: str
    upload_url: str
    folder: str
    eager: list[str]
    allowed_formats: list[str]
    max_file_size: int
    resource_type: str
    public_id: str | None  # optional pre-assigned ID (e.g. for deterministic asset names)
    tags: list[str]
    context: dict[str, str]


class CloudinaryClient:
    def __init__(self, settings: Settings) -> None:
        self._cloud_name = settings.cloudinary_cloud_name
        self._api_key = settings.cloudinary_api_key
        self._api_secret = settings.cloudinary_api_secret

    # ---------- Properties ----------

    @property
    def cloud_name(self) -> str:
        return self._cloud_name

    @property
    def api_key(self) -> str:
        return self._api_key

    def upload_url(self, *, resource_type: str = "image") -> str:
        return f"{CLOUDINARY_API_BASE}/{self._cloud_name}/{resource_type}/upload"

    # ---------- Signing ----------

    def build_signed_envelope(
        self,
        *,
        folder: str,
        eager: list[str],
        allowed_formats: list[str],
        max_file_size: int,
        resource_type: str = "image",
        public_id: str | None = None,
        tags: list[str] | None = None,
        context: dict[str, str] | None = None,
        timestamp: int | None = None,
    ) -> SignedUploadEnvelope:
        """Produce a signed payload the frontend hands to Cloudinary.

        All constraints (`max_file_size`, `allowed_formats`, `folder`, `eager`)
        are part of the signature — Cloudinary refuses an upload that changes
        any of them.
        """
        ts = timestamp or int(time.time())
        tags = tags or []
        context = context or {}

        # Build the param dict in the EXACT shape the frontend will submit.
        # Cloudinary's signature comparison is byte-exact against the
        # canonicalised form of these values.
        params: dict[str, Any] = {
            "timestamp": ts,
            "folder": folder,
            "eager": eager,
            "allowed_formats": allowed_formats,
            "max_file_size": max_file_size,
            "tags": tags,
        }
        if public_id:
            params["public_id"] = public_id
        if context:
            # Cloudinary `context` is `key1=val1|key2=val2`
            params["context"] = "|".join(f"{k}={v}" for k, v in context.items())

        signature = sign_params(params, self._api_secret)

        return SignedUploadEnvelope(
            cloud_name=self._cloud_name,
            api_key=self._api_key,
            timestamp=ts,
            signature=signature,
            upload_url=self.upload_url(resource_type=resource_type),
            folder=folder,
            eager=eager,
            allowed_formats=allowed_formats,
            max_file_size=max_file_size,
            resource_type=resource_type,
            public_id=public_id,
            tags=tags,
            context=context,
        )

    # ---------- Verification ----------

    def verify_response(self, response: dict[str, Any]) -> None:
        """Verify the Cloudinary upload response. Raises on tamper."""
        if not verify_upload_response(response, self._api_secret):
            log.warning(
                "cloudinary_response_signature_mismatch",
                public_id=response.get("public_id"),
            )
            raise InvalidMediaSignatureError(
                "Cloudinary upload signature did not verify"
            )

    # ---------- Destroy ----------

    async def destroy(self, public_id: str, *, resource_type: str = "image") -> None:
        """Delete an asset. Best-effort — logs but never blocks the caller.

        Cloudinary returns `{"result": "ok"}` for a real delete and
        `{"result": "not found"}` if the asset is already gone — both are
        treated as success.
        """
        timestamp = int(time.time())
        params = {"public_id": public_id, "timestamp": timestamp}
        signature = sign_params(params, self._api_secret)

        form = {
            "public_id": public_id,
            "timestamp": str(timestamp),
            "api_key": self._api_key,
            "signature": signature,
        }
        url = f"{CLOUDINARY_API_BASE}/{self._cloud_name}/{resource_type}/destroy"

        try:
            async with httpx.AsyncClient(timeout=DESTROY_TIMEOUT_SECONDS) as client:
                response = await client.post(url, data=form)
        except httpx.RequestError as exc:
            log.exception(
                "cloudinary_destroy_network_failure", public_id=public_id
            )
            raise IntegrationError(
                "Cloudinary destroy request failed",
                code="cloudinary_unreachable",
            ) from exc

        if response.status_code >= 400:
            log.warning(
                "cloudinary_destroy_http_error",
                public_id=public_id,
                status=response.status_code,
                body=response.text[:500],
            )
            raise IntegrationError(
                "Cloudinary destroy returned an error",
                code="cloudinary_destroy_failed",
                extra={"upstream_status": response.status_code},
            )

        result = response.json().get("result")
        if result not in ("ok", "not found"):
            log.warning(
                "cloudinary_destroy_unexpected_result",
                public_id=public_id,
                result=result,
            )
        else:
            log.info(
                "cloudinary_destroyed",
                public_id=public_id,
                result=result,
            )

    # ---------- Delivery URLs (read-only, no signing) ----------

    def build_delivery_url(
        self,
        public_id: str,
        *,
        transformations: str | None = None,
        version: int | None = None,
        resource_type: str = "image",
        delivery_type: str = "upload",
    ) -> str:
        """Build a `res.cloudinary.com/...` delivery URL.

        Cloudinary serves these from a global CDN; the URL itself is the
        transformation request. We do NOT need to sign delivery URLs
        unless the asset was uploaded as `type=authenticated`.
        """
        base = f"https://res.cloudinary.com/{self._cloud_name}/{resource_type}/{delivery_type}"
        parts = [base]
        if transformations:
            parts.append(transformations)
        if version is not None:
            parts.append(f"v{version}")
        parts.append(public_id)
        return "/".join(parts)
