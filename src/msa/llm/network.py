import ipaddress
import os
from urllib.parse import urlsplit


def on_prem_url(value):
    parts = urlsplit(str(value))
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        raise ValueError("An on-prem HTTP(S) endpoint is required")
    host = parts.hostname.lower()
    # Имена разрешаются только списком конфигурации; DNS здесь не проверяется.
    # Добавляя имя, оператор отвечает за то, куда оно разрешается в своей сети.
    allowed_hosts = {"localhost", "vllm"}
    allowed_hosts.update(
        item.strip().lower()
        for item in os.getenv("MSA_ON_PREM_HOSTS", "").split(",")
        if item.strip()
    )
    if host in allowed_hosts:
        return str(value)
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(f"Endpoint host is not on-prem: {host}") from exc
    if not (address.is_private or address.is_loopback):
        raise ValueError(f"Endpoint host is not on-prem: {host}")
    return str(value)
