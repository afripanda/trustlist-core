# secrets

Client wrapper over the cloud-native secrets store (AWS Secrets Manager per
ADR-0010). Resolves secret references supplied via environment variables at
startup; no credentials are ever embedded in code, configuration or container
images.

**Status:** placeholder. Implemented in Stage 0 issue 15.
