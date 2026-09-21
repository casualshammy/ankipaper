"""HTTP endpoint that exposes Prometheus metrics."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.config import Settings, get_settings

router = APIRouter()
_security = HTTPBasic(auto_error=False)


def _require_metrics_auth(
    credentials: HTTPBasicCredentials | None = Depends(_security),
    settings: Settings = Depends(get_settings),
) -> None:
    """Validates HTTP Basic credentials against settings.

    Raises:
        HTTPException 503: if metrics credentials are not configured.
        HTTPException 401: if credentials are missing or invalid.
    """

    if not settings.metrics_username or not settings.metrics_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="metrics not configured",
        )
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="ankipaper-metrics"'},
        )
    user_ok = secrets.compare_digest(
        credentials.username.encode(), settings.metrics_username.encode()
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode(), settings.metrics_password.encode()
    )
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": 'Basic realm="ankipaper-metrics"'},
        )


@router.get(
    "/metrics",
    dependencies=[Depends(_require_metrics_auth)],
    include_in_schema=False,
)
def metrics() -> Response:
    """Returns metrics in the Prometheus text exposition format."""

    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
