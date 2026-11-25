# ruff: noqa: E501, E701, E702, E401, I001
from __future__ import annotations
import ipaddress
from datetime import UTC, datetime
import httpx
from docker.errors import DockerException, NotFound
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import docker
from app.api.deps import get_state
from app.api.state import ApiState

router = APIRouter(prefix="/v1/ops/load-tests", tags=["application-load-testing"])
PROJECT = "predictiveops"

class LoadTestRequest(BaseModel):
    container_id: str = Field(min_length=12, max_length=128)
    port: int = Field(ge=1, le=65535)
    path: str = Field(default="/health", min_length=1, max_length=512)
    concurrency: int = Field(default=5, ge=1, le=100)
    request_rate: int = Field(default=10, ge=1, le=100)
    duration_seconds: int = Field(default=30, ge=5, le=300)
    warning_latency_ms: float = Field(default=500, ge=1, le=60000)
    critical_latency_ms: float = Field(default=1500, ge=1, le=120000)
    warning_error_rate: float = Field(default=0.02, ge=0, le=1)
    critical_error_rate: float = Field(default=0.10, ge=0, le=1)

def _docker(): return docker.from_env(timeout=8)
def _project_container(container): return container.labels.get("com.docker.compose.project") == PROJECT

def _target_address(container, generator):
    target_networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    generator_networks = generator.attrs.get("NetworkSettings", {}).get("Networks", {})
    for network_name in sorted(set(target_networks) & set(generator_networks)):
        address = target_networks[network_name].get("IPAddress")
        if not address: continue
        ip = ipaddress.ip_address(address)
        if ip.version == 4 and ip.is_private and not ip.is_loopback and not ip.is_link_local:
            return address
    raise HTTPException(422, detail="Target does not share a private Docker network with the load generator")

def _load_generator(client):
    matches = client.containers.list(filters={"label":[f"com.docker.compose.project={PROJECT}","com.docker.compose.service=load-generator"]})
    if not matches: raise HTTPException(503,detail="Isolated load-generator service is not running")
    generator=matches[0]; generator.reload()
    if generator.status!="running": raise HTTPException(503,detail="Isolated load-generator service is not healthy")
    return generator

@router.get("/targets")
def load_test_targets():
    try:
        client=_docker(); items=[]
        for container in client.containers.list(all=True,filters={"label":f"com.docker.compose.project={PROJECT}"}):
            service=container.labels.get("com.docker.compose.service")
            if container.labels.get("predictiveops.role")=="stress" or service=="load-generator": continue
            container.reload()
            if container.status!="running": continue
            exposed=container.attrs.get("Config",{}).get("ExposedPorts",{}) or {}
            ports=sorted({int(key.split("/")[0]) for key in exposed if key.split("/")[0].isdigit()})
            if service=="api" and 8000 not in ports: ports.append(8000)
            if ports: items.append({"id":container.id,"name":container.name,"service":service or container.name,"ports":sorted(ports)})
        return {"targets":sorted(items,key=lambda item:item["service"])}
    except DockerException as exc: raise HTTPException(503,detail=f"Docker inventory unavailable: {exc}") from exc

@router.post("",status_code=202)
async def start_load_test(request:LoadTestRequest,state:ApiState=Depends(get_state)):
    try:
        client=_docker()
        try: target=client.containers.get(request.container_id)
        except NotFound: raise HTTPException(404,detail="Managed container not found") from None
        if not _project_container(target): raise HTTPException(403,detail="Only managed PredictiveOps project containers can be load tested")
        target.reload()
        if target.status!="running": raise HTTPException(409,detail="Target container must be running")
        if target.labels.get("predictiveops.role")=="stress": raise HTTPException(403,detail="Temporary stress workers cannot be load-test targets")
        exposed=target.attrs.get("Config",{}).get("ExposedPorts",{}) or {}
        ports={int(key.split("/")[0]) for key in exposed if key.split("/")[0].isdigit()}
        service=target.labels.get("com.docker.compose.service",target.name)
        if service=="api": ports.add(8000)
        if request.port not in ports: raise HTTPException(422,detail="Port must be declared by the selected container image")
        generator=_load_generator(client); address=_target_address(target,generator)
    except HTTPException: raise
    except DockerException as exc: raise HTTPException(503,detail=f"Docker API error: {exc}") from exc
    payload=request.model_dump(exclude={"container_id"}); payload.update({"service":service,"target_ip":address})
    try:
        async with httpx.AsyncClient(base_url="http://load-generator:8001",timeout=5.0,trust_env=False) as session:
            response=await session.post("/jobs",json=payload)
            if response.status_code>=400: raise HTTPException(response.status_code,detail=response.json().get("detail","Load generator rejected request"))
            result=response.json(); result["container_name"]=target.name
            state.store.add_audit(ts=datetime.now(UTC),series_id=f"load-test:{result['job_id']}",action="application_load_test_started",target=target.name,outcome="started",dry_run=True,probability=None,model_version=None,reason="Bounded HTTP load test started against a managed container",details={"service":service,"container_id":target.id,"port":request.port,"path":request.path,"concurrency":request.concurrency,"request_rate":request.request_rate,"duration_seconds":request.duration_seconds,"warning_latency_ms":request.warning_latency_ms,"critical_latency_ms":request.critical_latency_ms,"warning_error_rate":request.warning_error_rate,"critical_error_rate":request.critical_error_rate})
            return result
    except HTTPException: raise
    except httpx.HTTPError as exc: raise HTTPException(503,detail=f"Load generator unavailable: {exc}") from exc

@router.get("/{job_id}")
async def load_test_status(job_id:str):
    try:
        async with httpx.AsyncClient(base_url="http://load-generator:8001",timeout=5.0,trust_env=False) as session:
            response=await session.get(f"/jobs/{job_id}")
            if response.status_code==404: raise HTTPException(404,detail="Load test not found")
            response.raise_for_status(); return response.json()
    except HTTPException: raise
    except httpx.HTTPError as exc: raise HTTPException(503,detail=f"Load generator unavailable: {exc}") from exc

@router.post("/{job_id}/cancel")
async def cancel_load_test(job_id:str):
    try:
        async with httpx.AsyncClient(base_url="http://load-generator:8001",timeout=5.0,trust_env=False) as session:
            response=await session.post(f"/jobs/{job_id}/cancel")
            if response.status_code>=400: raise HTTPException(response.status_code,detail=response.json().get("detail"))
            return response.json()
    except HTTPException: raise
    except httpx.HTTPError as exc: raise HTTPException(503,detail=f"Load generator unavailable: {exc}") from exc
