from django.db import connection
from rest_framework.status import HTTP_200_OK, HTTP_503_SERVICE_UNAVAILABLE

from core.substrate import SubstrateService


def check_db_connection() -> bool:
    try:
        connection.cursor()
    except Exception:  # noqa
        return False
    return True


def check_blockchain_connection() -> bool:
    try:
        SubstrateService()
    except Exception:  # noqa
        return False
    return True


def collect() -> (str, int, dict):
    checks = {
        "database": check_db_connection,
        "blockchain": check_blockchain_connection,
    }
    details = {name: func() for name, func in checks.items()}
    status, status_code = ("passed", HTTP_200_OK) if all(details.values()) else ("failed", HTTP_503_SERVICE_UNAVAILABLE)
    return status, status_code, details
