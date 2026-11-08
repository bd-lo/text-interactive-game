"""健康检查路由: 运维探活, 不触碰业务状态与外部依赖。"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["ops"])


class HealthResponse(BaseModel):
    """健康检查响应体。"""

    status: str = "ok"


@router.get("/healthz", response_model=HealthResponse, summary="健康检查")
def healthz() -> HealthResponse:
    """返回服务存活状态; 纯内存计算, 不访问数据库与 LLM。"""
    return HealthResponse()
