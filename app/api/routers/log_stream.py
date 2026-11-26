# ruff: noqa: E501, E701, E702, E401, I001
from __future__ import annotations
import json
from docker.errors import DockerException
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.api.routers.operations import _find_container
from app.api.deps import get_state
from app.api.state import ApiState

router=APIRouter(prefix="/v1/ops/containers",tags=["container-operations"])

@router.get("/{container_id}/logs/stream")
def stream_container_logs(container_id:str,tail:int=100,state:ApiState=Depends(get_state)):
    _,container=_find_container(container_id,state)
    try:
        stream=container.logs(tail=max(1,min(tail,2000)),timestamps=True,stdout=True,stderr=True,stream=True,follow=True)
    except DockerException as exc: raise HTTPException(502,detail=str(exc)) from exc
    def events():
        try:
            for line in stream:
                payload=json.dumps({"line":line.decode("utf-8",errors="replace").rstrip("\r\n")})
                yield f"data: {payload}\n\n"
        except GeneratorExit: return
        except DockerException as exc:
            yield f"event: error\ndata: {json.dumps({'error':str(exc)})}\n\n"
        finally:
            try: stream.close()
            except Exception: pass
    return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
