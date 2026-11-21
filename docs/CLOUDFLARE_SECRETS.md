# Vault credentials for Cloudflare services

Use the generic [workload vault binding](WORKLOAD_SECRETS.md). The old connector API is retired. Previously stored envelopes and records have not been migrated or deleted. Create new versioned vault entries using the provider's administrative interface; do not print or paste the values into a blueprint, command line, or chat.

- `cloudflare-dns`: use a dedicated scoped API token with DNS Edit on the one intended zone. It is not a global API key.
- `cloudflare-tunnel`: use the connector token of the intended existing remotely managed tunnel, not an API token.

Store the raw value in a dedicated vault reference, rather than the old JSON envelope. Keep DNS and Tunnel bindings separate. Use a new versioned reference when rotating and update the binding revision before asking for approval again.
