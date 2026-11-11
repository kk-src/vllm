# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
"""Connection configuration for the object store secondary tier."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_RESERVED_BACKEND_PARAM_KEYS = frozenset(
    {
        "bucket",
        "endpoint_override",
        "access_key",
        "secret_key",
        "session_token",
        "region",
        "scheme",
        "ca_bundle",
        "num_threads",
    }
)


def _to_nixl_param(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower()
    return str(value)


@dataclass(slots=True)
class ObjStoreConfig:
    """Connection parameters for an object store backend.

    When `access_key` and `secret_key` are left empty the NIXL OBJ
    plugin falls back to the AWS SDK default credential provider chain
    (IAM roles, environment variables, credential files, etc.), which
    enables workload-identity based auth on Kubernetes.
    """

    bucket: str
    endpoint_override: str
    access_key: str = field(default="", repr=False)
    secret_key: str = field(default="", repr=False)
    session_token: str = field(default="", repr=False)
    region: str = ""
    scheme: str = "http"
    ca_bundle: str = ""
    backend_params: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def to_nixl_params(self) -> dict[str, str]:
        """Build the NIXL backend params dict.

        Credential and optional fields are only included when non-empty
        so that the AWS SDK default credential chain can activate.
        """
        params: dict[str, str] = {
            "bucket": self.bucket,
            "endpoint_override": self.endpoint_override,
            "scheme": self.scheme,
        }
        # Omit empty optional fields so the NIXL OBJ plugin's underlying
        # AWS SDK can fall back to its default credential provider chain
        # (IAM roles, env vars, credential files, etc.).
        # https://github.com/ai-dynamo/nixl/blob/main/src/plugins/obj/README.md
        for key in ("access_key", "secret_key", "session_token", "region", "ca_bundle"):
            value = getattr(self, key)
            if value:
                params[key] = value

        duplicate_keys = _RESERVED_BACKEND_PARAM_KEYS.intersection(self.backend_params)
        if duplicate_keys:
            raise ValueError(
                "backend_params cannot override reserved object store "
                f"parameters: {sorted(duplicate_keys)}"
            )

        params.update(
            {key: _to_nixl_param(value) for key, value in self.backend_params.items()}
        )
        return params


@dataclass
class AzureBlobStoreConfig:
    """Connection parameters for the NIXL Azure Blob backend.

    When ``connection_string`` is empty, the NIXL AZURE_BLOB plugin uses
    ``account_url`` with the Azure default credential chain.
    """

    container_name: str
    account_url: str = ""
    connection_string: str = field(default="", repr=False)
    ca_bundle: str = ""

    def __post_init__(self) -> None:
        if not self.container_name:
            raise ValueError("Azure Blob store_config requires container_name")
        if not self.account_url and not self.connection_string:
            raise ValueError(
                "Azure Blob store_config requires account_url or connection_string"
            )

    def to_nixl_params(self) -> dict[str, str]:
        """Build the NIXL backend params dict, omitting empty optional fields."""
        params: dict[str, str] = {"container_name": self.container_name}
        for key in ("account_url", "connection_string", "ca_bundle"):
            value = getattr(self, key)
            if value:
                params[key] = value
        return params
