"""Однократное создание доступа к базе; существующий .env сохраняется."""

import os
import secrets
from pathlib import Path


def init_env(root: Path) -> bool:
    path = root / ".env"
    if path.exists():
        return False
    password = secrets.token_hex(24)
    # O_EXCL защищает существующие реквизиты и при двух одновременных запусках,
    # которые оба успели пройти проверку exists().
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8"
    ) as stream:
        stream.write(f"POSTGRES_PASSWORD={password}\n")
        stream.write(f"MSA_DATABASE_URL=postgresql://msa:{password}@127.0.0.1:5433/msa_product\n")
    return True
