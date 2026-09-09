"""Cliente de almacenamiento de objetos para recibos (y, más adelante,
archivos de `/export`) — PLAN-backend.md §14: Cloudflare R2, compatible con
la API de S3 (mismo `boto3`, solo cambia `endpoint_url`).

El backend nunca ve los bytes del archivo: solo firma URLs de subida/bajada
de corta duración. `generate_presigned_url` es una operación LOCAL (firma
HMAC con las credenciales configuradas) — no hace red hacia R2, así que no
hace falta mockear nada para probarla.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import boto3
from botocore.client import Config as BotoConfig

from config import get_settings
from core.errors import AppError

if TYPE_CHECKING:
    # boto3-stubs es un dependency-group de dev (solo para mypy); en
    # producción no está instalado, así que el import real nunca corre.
    from mypy_boto3_s3 import S3Client

# CLAUDE.md §10: "Recibos en S3 privado, solo URLs prefirmadas con
# expiración de 5 min."
PRESIGNED_URL_EXPIRES_SECONDS = 300


class ObjectStorageNotConfiguredError(AppError):
    status_code = 503
    code = "OBJECT_STORAGE_NOT_CONFIGURED"


def _bucket_receipts() -> str:
    settings = get_settings()
    if not (
        settings.aws_access_key_id
        and settings.aws_secret_access_key
        and settings.s3_bucket_receipts
    ):
        raise ObjectStorageNotConfiguredError(
            "El almacenamiento de recibos no está configurado en este entorno."
        )
    return settings.s3_bucket_receipts


def _client() -> S3Client:
    settings = get_settings()
    return boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region or "auto",
        endpoint_url=settings.s3_endpoint_url,
        config=BotoConfig(signature_version="s3v4"),
    )


def generate_upload_url(key: str, *, content_type: str) -> str:
    bucket = _bucket_receipts()
    url = _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=PRESIGNED_URL_EXPIRES_SECONDS,
    )
    return url


def generate_download_url(key: str) -> str:
    bucket = _bucket_receipts()
    url = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=PRESIGNED_URL_EXPIRES_SECONDS,
    )
    return url
