"""Health e readiness.

Distinção que importa para o Compose e para o orquestrador:
  - /healthz  o processo está vivo?            (nunca toca dependência)
  - /readyz   consegue atender de verdade?     (checa Postgres e Redis)

Usar só um dos dois causa dois bugs clássicos: healthcheck que derruba o
container quando o banco pisca, ou tráfego roteado para um serviço que
ainda não conecta.
"""

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.db import get_engine
from app.core.redis import get_redis

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "core-api"}


@router.get("/readyz")
async def readyz(response: Response) -> dict[str, object]:
    checks: dict[str, str] = {}

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 — queremos reportar, não propagar
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"

    ready = all(v == "ok" for v in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"ready": ready, "checks": checks}
