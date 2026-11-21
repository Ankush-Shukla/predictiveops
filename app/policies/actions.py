"""Allowlisted remediation actions.

There is deliberately NO generic command execution here. Each action maps to one fixed
container-API call on one explicitly configured, label-verified container.
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from app.core.exceptions import PredictiveOpsError
from app.policies.models import NAME_PATTERN

ALLOWED_ACTIONS: frozenset[str] = frozenset({"restart_container", "rollback_canary"})
MANAGED_LABEL = "predictiveops.managed"  # container must carry predictiveops.managed=true
CANARY_ROLE_LABEL = "predictiveops.role"  # rollback_canary additionally needs role=canary


class ActionNotAllowedError(PredictiveOpsError):
    """Action or target rejected by the safety checks."""


class ActionExecutor(Protocol):
    def execute(self, action: str, container: str) -> str: ...


class NoopExecutor:
    """Executes nothing. Used when remediation is disabled/dry-run, and in tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def execute(self, action: str, container: str) -> str:
        self.calls.append((action, container))
        return "noop"


class DockerExecutor:
    """Runs allowlisted actions through the Docker SDK (restart / stop of labelled containers).

    NOTE: exercised in tests only against a fake client; not verified against a real daemon.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> DockerExecutor:
        import docker  # imported lazily so the API runs without a Docker daemon

        return cls(docker.from_env())

    def execute(self, action: str, container: str) -> str:
        if action not in ALLOWED_ACTIONS:
            raise ActionNotAllowedError(f"action {action!r} is not allowlisted")
        if not re.fullmatch(NAME_PATTERN, container):
            raise ActionNotAllowedError("invalid container name")
        c = self._client.containers.get(container)
        labels = c.labels or {}
        if labels.get(MANAGED_LABEL) != "true":
            raise ActionNotAllowedError(f"container {container!r} is not labelled {MANAGED_LABEL}=true")
        if action == "restart_container":
            c.restart(timeout=10)
        else:  # rollback_canary
            if labels.get(CANARY_ROLE_LABEL) != "canary":
                raise ActionNotAllowedError(f"container {container!r} is not a canary")
            c.stop(timeout=10)
        return f"{action} completed on {container}"


class LazyDockerExecutor:
    """Connects to Docker on first use so startup never depends on the daemon."""

    def __init__(self) -> None:
        self._inner: DockerExecutor | None = None

    def execute(self, action: str, container: str) -> str:
        if self._inner is None:
            self._inner = DockerExecutor.from_env()
        return self._inner.execute(action, container)
