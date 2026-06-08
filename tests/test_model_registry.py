"""DB 기반 모델 레지스트리(promote/rollback/per-tenant/hot-reload) 테스트."""

from __future__ import annotations

import json
from pathlib import Path

from httpx import AsyncClient

_PRED = Path("app/prediction/models/prediction_model.json")
_REC = Path("app/recommendation/models/recommendation_ranker.json")


def _artifact(path: Path, version: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["model_version"] = version
    return data


async def test_register_promote_makes_serving_use_promoted_version(client: AsyncClient):
    v = "ml.logistic-prediction.promoted-A"
    reg = await client.post(
        "/v1/models/prediction/versions", json={"artifact": _artifact(_PRED, v)}
    )
    assert reg.status_code == 200
    assert reg.json()["status"] == "staging"

    # promote 전: 서빙은 패키지 seed(v3)
    before = await client.get("/v1/predictions/model-status")
    assert before.json()["model_version"] == "ml.logistic-prediction.v3"

    promote = await client.post("/v1/models/prediction/promote", json={"version": v})
    assert promote.status_code == 200
    assert promote.json()["active_version"] == v

    # promote 후: 무중단으로 서빙 버전이 교체됨(hot-reload)
    after = await client.get("/v1/predictions/model-status")
    assert after.json()["model_version"] == v


async def test_rollback_by_promoting_previous_version(client: AsyncClient):
    va = _artifact(_PRED, "pred-A")
    vb = _artifact(_PRED, "pred-B")
    await client.post("/v1/models/prediction/versions", json={"artifact": va})
    await client.post("/v1/models/prediction/versions", json={"artifact": vb})
    await client.post("/v1/models/prediction/promote", json={"version": "pred-A"})
    await client.post("/v1/models/prediction/promote", json={"version": "pred-B"})

    status_b = await client.get("/v1/predictions/model-status")
    assert status_b.json()["model_version"] == "pred-B"

    # 롤백 = 이전 버전 재promote (코드 배포 없이)
    rollback = await client.post("/v1/models/prediction/promote", json={"version": "pred-A"})
    assert rollback.json()["active_version"] == "pred-A"
    statuses = {v["version"]: v["status"] for v in rollback.json()["versions"]}
    assert statuses["pred-A"] == "production"
    assert statuses["pred-B"] == "archived"  # 직전 production 은 archived

    status_a = await client.get("/v1/predictions/model-status")
    assert status_a.json()["model_version"] == "pred-A"


async def test_registry_is_per_tenant(client: AsyncClient):
    v = _artifact(_PRED, "tenant-a-only")
    await client.post("/v1/models/prediction/versions", json={"artifact": v})
    await client.post("/v1/models/prediction/promote", json={"version": "tenant-a-only"})

    # tenant-a 는 promote 버전, tenant-b 는 패키지 seed
    a = await client.get("/v1/predictions/model-status")
    b = await client.get("/v1/predictions/model-status", headers={"X-Sunrise-Key": "other-key"})
    assert a.json()["model_version"] == "tenant-a-only"
    assert b.json()["model_version"] == "ml.logistic-prediction.v3"


async def test_register_rejects_invalid_artifact(client: AsyncClient):
    bad = await client.post(
        "/v1/models/prediction/versions", json={"artifact": {"model_type": "wrong"}}
    )
    assert bad.status_code == 422


async def test_unknown_model_returns_404(client: AsyncClient):
    response = await client.get("/v1/models/nonexistent")
    assert response.status_code == 404


async def test_promote_unknown_version_returns_404(client: AsyncClient):
    response = await client.post(
        "/v1/models/prediction/promote", json={"version": "does-not-exist"}
    )
    assert response.status_code == 404


async def test_recommendation_registry_promote(client: AsyncClient):
    v = _artifact(_REC, "rec-promoted")
    reg = await client.post(
        "/v1/models/recommendation/versions", json={"artifact": v}
    )
    assert reg.status_code == 200
    promote = await client.post(
        "/v1/models/recommendation/promote", json={"version": "rec-promoted"}
    )
    assert promote.status_code == 200
    assert promote.json()["active_version"] == "rec-promoted"
