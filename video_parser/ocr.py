from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - requirements install python-dotenv.
    load_dotenv = None


SUPPORTED_ACTIONS = {"GeneralAccurateOCR", "GeneralBasicOCR"}
TRUTHY = {"1", "true", "yes", "on"}


class TencentOCRError(RuntimeError):
    """Base error for the optional Tencent Cloud OCR integration."""


class OCRNotConfiguredError(TencentOCRError):
    """Raised when OCR was requested without usable credentials/configuration."""


class OCRSDKUnavailableError(TencentOCRError):
    """Raised when the Tencent Cloud SDK is not installed."""


@dataclass(frozen=True)
class TencentOCRConfig:
    enabled: bool = False
    secret_id: str = ""
    secret_key: str = ""
    region: str = "ap-guangzhou"
    endpoint: str = "ocr.tencentcloudapi.com"
    action: str = "GeneralAccurateOCR"
    version: str = "2018-11-19"
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "TencentOCRConfig":
        if load_dotenv is not None:
            load_dotenv()
        return cls(
            enabled=os.getenv("TENCENT_OCR_ENABLED", "false").strip().lower() in TRUTHY,
            secret_id=os.getenv("TENCENTCLOUD_SECRET_ID", "").strip(),
            secret_key=os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip(),
            region=os.getenv("TENCENTCLOUD_REGION", "ap-guangzhou").strip() or "ap-guangzhou",
            endpoint=os.getenv("TENCENTCLOUD_ENDPOINT", "ocr.tencentcloudapi.com").strip() or "ocr.tencentcloudapi.com",
            action=os.getenv("TENCENT_OCR_ACTION", "GeneralAccurateOCR").strip() or "GeneralAccurateOCR",
            version=os.getenv("TENCENT_OCR_VERSION", "2018-11-19").strip() or "2018-11-19",
            timeout_seconds=max(5, int(os.getenv("TENCENT_OCR_TIMEOUT_SECONDS", "30"))),
        )

    @property
    def configured(self) -> bool:
        return bool(self.secret_id and self.secret_key)

    @property
    def available(self) -> bool:
        return self.enabled and self.configured and importlib.util.find_spec("tencentcloud") is not None


@dataclass(frozen=True)
class OCRResponse:
    text: str
    detections: list[dict[str, Any]]
    request_id: str | None = None


def tencent_ocr_status(config: TencentOCRConfig | None = None) -> dict[str, Any]:
    config = config or TencentOCRConfig.from_env()
    sdk_installed = importlib.util.find_spec("tencentcloud") is not None
    if not config.enabled:
        status = "disabled"
        message = "TENCENT_OCR_ENABLED is not true."
    elif not config.configured:
        status = "unconfigured"
        message = "Tencent Cloud OCR credentials are missing."
    elif not sdk_installed:
        status = "sdk_missing"
        message = "tencentcloud-sdk-python is not installed."
    else:
        status = "available"
        message = "Tencent Cloud OCR is ready."
    return {
        "status": status,
        "message": message,
        "action": config.action,
        "region": config.region,
        "sdk_installed": sdk_installed,
        "configured": config.configured,
        "enabled": config.enabled,
    }


class TencentOCRClient:
    def __init__(self, config: TencentOCRConfig | None = None, sdk_client: Any | None = None):
        self.config = config or TencentOCRConfig.from_env()
        if self.config.action not in SUPPORTED_ACTIONS:
            raise TencentOCRError(
                f"Unsupported Tencent OCR action: {self.config.action}. "
                f"Use one of: {', '.join(sorted(SUPPORTED_ACTIONS))}."
            )
        if sdk_client is not None:
            self._client = sdk_client
            return
        if not self.config.enabled or not self.config.configured:
            raise OCRNotConfiguredError(
                "Tencent Cloud OCR is not configured. Set TENCENT_OCR_ENABLED=true "
                "and provide TENCENTCLOUD_SECRET_ID/TENCENTCLOUD_SECRET_KEY."
            )
        try:
            from tencentcloud.common import credential
            from tencentcloud.common.profile.client_profile import ClientProfile
            from tencentcloud.common.profile.http_profile import HttpProfile
            from tencentcloud.ocr.v20181119 import ocr_client
        except ImportError as exc:  # pragma: no cover - depends on the local environment.
            raise OCRSDKUnavailableError("Install tencentcloud-sdk-python to use Tencent Cloud OCR.") from exc

        credentials = credential.Credential(self.config.secret_id, self.config.secret_key)
        http_profile = HttpProfile()
        http_profile.endpoint = self.config.endpoint
        http_profile.reqTimeout = self.config.timeout_seconds
        client_profile = ClientProfile()
        client_profile.httpProfile = http_profile
        self._client = ocr_client.OcrClient(credentials, self.config.region, client_profile)

    def recognize_file(self, image_path: str | Path) -> OCRResponse:
        path = Path(image_path).expanduser().resolve()
        if not path.is_file():
            raise TencentOCRError(f"OCR image does not exist: {path}")
        try:
            image_base64 = base64.b64encode(path.read_bytes()).decode("ascii")
            request_cls = self._request_class()
            request = request_cls()
            request.from_json_string(json.dumps({"ImageBase64": image_base64}, ensure_ascii=False))
            method = getattr(self._client, self.config.action)
            response = method(request)
            payload = json.loads(response.to_json_string())
            return _response_from_payload(payload)
        except TencentOCRError:
            raise
        except Exception as exc:  # noqa: BLE001 - SDK errors vary by action/version.
            message = str(exc)
            if "FailedOperation.ImageNoText" in message:
                request_match = re.search(r"requestId:([A-Za-z0-9-]+)", message)
                return OCRResponse(text="", detections=[], request_id=request_match.group(1) if request_match else None)
            raise TencentOCRError(f"Tencent Cloud OCR request failed: {exc}") from exc

    def _request_class(self):
        try:
            from tencentcloud.ocr.v20181119 import models
        except ImportError as exc:  # pragma: no cover - depends on the local environment.
            raise OCRSDKUnavailableError("Install tencentcloud-sdk-python to use Tencent Cloud OCR.") from exc
        request_cls = getattr(models, f"{self.config.action}Request", None)
        if request_cls is None:
            raise TencentOCRError(f"Tencent OCR SDK does not expose {self.config.action}Request.")
        return request_cls


def _response_from_payload(payload: dict[str, Any]) -> OCRResponse:
    detections: list[dict[str, Any]] = []
    for item in payload.get("TextDetections") or []:
        text = str(item.get("DetectedText") or "").strip()
        if not text:
            continue
        confidence = item.get("Confidence")
        try:
            confidence = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            confidence = None
        polygon = item.get("Polygon") or item.get("ItemPolygon") or []
        detections.append({"text": text, "confidence": confidence, "polygon": polygon})
    return OCRResponse(
        text="\n".join(item["text"] for item in detections),
        detections=detections,
        request_id=payload.get("RequestId"),
    )
