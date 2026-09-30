"""Aplica migraciones de pyway en el arranque del contenedor.

Railway (y la mayoría de PaaS) da un solo DATABASE_URL; pyway pide
host/puerto/usuario/clave por separado, así que lo parseamos acá en vez de
mantener un segundo .pyway.conf por entorno.
"""

from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import urlparse


def main() -> int:
    raw = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    url = urlparse(raw)
    result = subprocess.run(
        [
            "pyway",
            "migrate",
            "--database-host",
            url.hostname or "",
            "--database-port",
            str(url.port or 5432),
            "--database-name",
            url.path.lstrip("/"),
            "--database-username",
            url.username or "",
            "--database-password",
            url.password or "",
        ],
        check=False,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
