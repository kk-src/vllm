# Azure Blob Storage KV Offloading

This example shows how to configure the object-storage secondary tier with
the NIXL `AZURE_BLOB` backend, using either local
[Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite)
or a real Azure Storage account.

The `obj` tier selects its NIXL backend with the `backend` field. `OBJ`
(S3-compatible storage) is the default, so existing configurations are
unchanged; set `backend: "AZURE_BLOB"` to use Azure Blob Storage. See
[Object Storage](../../../../docs/features/kv_offloading_usage.md#object-storage)
for all configuration fields.

The optional manual validation procedure below describes how to check cache
reuse after a server restart. These
instructions and expected results are not a record of completed validation.

## Prerequisites

- vLLM installed with Azure Blob secondary-tier support.
- NIXL built with the `AZURE_BLOB` plugin.
- Docker, used to run Azurite and the Azure CLI.
- CUDA, ROCm or XPU hardware for serving with `OffloadingConnector`.
- `curl` and `jq` for the sample request and output comparison.

## 1. Install dependencies and check for the Azure plugin

```bash
uv pip install nixl==1.5.0
.venv/bin/python -c "from nixl._api import nixl_agent, nixl_agent_config; \
print(nixl_agent('t', nixl_agent_config(backends=[])).get_plugin_list())"
```

`AZURE_BLOB` must appear in the output. If it does not, build NIXL with the
plugin by following the [NIXL Azure Blob documentation](https://github.com/ai-dynamo/nixl/blob/main/src/plugins/azure_blob/README.md).

## 2. Configure storage

Choose **one** option. Run all later steps in the same terminal, because they
use the variables set here.

### Option A: Azurite (local, no Azure account)

Start Azurite in a separate terminal:

```bash
docker run --rm -p 10000:10000 mcr.microsoft.com/azure-storage/azurite \
  azurite-blob --blobHost 0.0.0.0 --skipApiVersionCheck
```

Then set the variables. The connection string is Azurite's published
development value, not a real secret.

```bash
export AZURITE_CONN='DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;'
export STORE_JSON="{\"container_name\": \"kv-cache\", \"connection_string\": \"$AZURITE_CONN\"}"
AZ_CLI=(docker run --rm --network host mcr.microsoft.com/azure-cli az)
AZ_AUTH=(--connection-string "$AZURITE_CONN")
```

### Option B: Azure Storage account

```bash
export STORE_JSON='{"container_name": "kv-cache", "account_url": "https://<account>.blob.core.windows.net"}'
AZ_CLI=(docker run --rm -it -v "$HOME/.azure:/root/.azure" mcr.microsoft.com/azure-cli az)
AZ_AUTH=(--account-name "<account>" --auth-mode login)
"${AZ_CLI[@]}" login --use-device-code
```

vLLM authenticates with the Azure default credential chain, so the machine
running vLLM also needs credentials with the **Storage Blob Data Contributor**
role. Use a managed identity, or export a service principal's
`AZURE_TENANT_ID`, `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET`.

## 3. Prepare the container

```bash
"${AZ_CLI[@]}" storage container create --name kv-cache "${AZ_AUTH[@]}"
"${AZ_CLI[@]}" storage blob list --container-name kv-cache --prefix vllm-gpu-1/ \
  "${AZ_AUTH[@]}" --query "length(@)"
```

The last command must print `0`. If it does not, use a new prefix in the
following steps (for example, `vllm-gpu-2`).

## 4. Run vLLM

Start the server:

```bash
vllm serve Qwen/Qwen2.5-0.5B-Instruct --port 8000 \
  --kv-transfer-config "{
    \"kv_connector\": \"OffloadingConnector\",
    \"kv_role\": \"kv_both\",
    \"kv_connector_extra_config\": {
      \"spec_name\": \"TieringOffloadingSpec\",
      \"cpu_bytes_to_use\": 2000000000,
      \"secondary_tiers\": [{
        \"type\": \"obj\",
        \"backend\": \"AZURE_BLOB\",
        \"store_config\": $STORE_JSON,
        \"prefix\": \"vllm-gpu-1\"
      }]
    }
  }"
```

The startup log should show `Object store tier connectivity probe succeeded`.
This message alone does not confirm that the container exists or that
transfers will succeed.

In another terminal, from the vLLM repository root, send a long prompt with
greedy sampling:

```bash
PROMPT=$(.venv/bin/python -c "print('The quick brown fox jumps over the lazy dog. ' * 300)")
req() { curl -s localhost:8000/v1/completions -H 'Content-Type: application/json' \
  -d "{\"model\":\"Qwen/Qwen2.5-0.5B-Instruct\",\"prompt\":\"$PROMPT\",\"max_tokens\":20,\"temperature\":0}"; }
req > run1.json
```

## 5. Optional manual validation

### Cache reuse after a server restart

To check reuse from Azure, stop the server with Ctrl+C, start it again with
the same command, then send the same request from the request terminal:

```bash
req > run2.json
```

A restart clears the GPU and CPU caches. With Azure as the only secondary
tier, external cache hits after the restart indicate reuse from Azure.
Allow stores to complete before stopping the first server.

Run the storage inspection command in the terminal where `AZ_CLI` and
`AZ_AUTH` were configured.

| Check | Expected result when successful | Purpose |
| --- | --- | --- |
| `"${AZ_CLI[@]}" storage blob list --container-name kv-cache --prefix vllm-gpu-1/ "${AZ_AUTH[@]}" --query "length(@)"` after the first run | Greater than `0` | Check that KV objects were stored in Azure. |
| `External prefix cache hit rate` in the server's periodic stats log after the second run | Greater than 0% | Check that the restarted server reused externally cached KV. |
| `diff <(jq .choices[0].text run1.json) <(jq .choices[0].text run2.json)` | No differences | Compare generated text. Different prefill batch shapes can affect numerical results even with greedy sampling. |
