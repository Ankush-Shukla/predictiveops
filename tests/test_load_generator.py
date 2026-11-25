import pytest
from pydantic import ValidationError
from app.services.load_generator import LoadJob

def test_accepts_private_docker_ipv4():
    assert LoadJob(service="api",target_ip="172.20.0.5",port=8000).target_ip=="172.20.0.5"

@pytest.mark.parametrize("address",["8.8.8.8","127.0.0.1","169.254.1.2","::1"])
def test_rejects_non_private_or_non_ipv4_targets(address):
    with pytest.raises(ValidationError): LoadJob(service="api",target_ip=address,port=8000)

@pytest.mark.parametrize("path",["https://example.com","//example.com","health"])
def test_rejects_absolute_urls_and_invalid_paths(path):
    with pytest.raises(ValidationError): LoadJob(service="api",target_ip="172.20.0.5",port=8000,path=path)

def test_enforces_resource_bounds():
    with pytest.raises(ValidationError): LoadJob(service="api",target_ip="172.20.0.5",port=8000,concurrency=101)
    with pytest.raises(ValidationError): LoadJob(service="api",target_ip="172.20.0.5",port=8000,duration_seconds=301)
