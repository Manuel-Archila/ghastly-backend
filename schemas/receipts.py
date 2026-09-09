from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ReceiptContentType = Literal[
    "image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf"
]


class ReceiptUploadRequest(BaseModel):
    content_type: ReceiptContentType = "image/jpeg"


class ReceiptUploadOut(BaseModel):
    upload_url: str
    receipt_key: str
    expires_in: int


class ReceiptDownloadOut(BaseModel):
    download_url: str
    expires_in: int
