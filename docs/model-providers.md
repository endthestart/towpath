# Model provider contract

Status: proposed implementation contract. Provider support will be claimed only after synthetic compatibility tests against each named endpoint.

The first model-backed feature will use an explicitly configured `base_url`, credential reference, and model ID with the [OpenAI Chat Completions API](https://developers.openai.com/api/reference/cli/resources/chat/subresources/completions). This is a practical common API shape used by many local servers and gateways; each implementation may support a different subset. A profile also declares whether its endpoint is local or remote and which data classes may be sent there.

| Feature | Initial API expectation | Fallback when unavailable |
| --- | --- | --- |
| Structured text extraction | `POST /v1/chat/completions`, with JSON output validated by Towpath | Deterministic rules and manual review; no automatic provider switch |
| Text embeddings | `POST /v1/embeddings` where supported | Lexical search; no semantic search claim |
| Model discovery | `GET /v1/models` where supported | Explicit model ID in configuration |
| Responses API | Optional adapter after a separate compatibility test | Chat Completions path |
| Reranking and transcription | Separate optional adapters with documented contracts | Feature disabled |

OpenAI's [API reference](https://developers.openai.com/api/reference/overview) describes the official `v1` endpoints; its [embeddings guide](https://developers.openai.com/api/docs/guides/embeddings) covers vector generation. “OpenAI-compatible” in this project means the tested subset above, not that every endpoint supports every OpenAI feature. Validate responses, usage fields, limits, errors, streaming behavior, and structured-output behavior per endpoint.

Poundlock can be configured as an endpoint for deployments that use it. Its route revision and served-model metadata should be recorded when provided, but core records cannot require those fields. Directly configured OpenAI, llama.cpp, Ollama's compatible API, and other gateways must be possible without Poundlock. None is auto-selected.

The public example below is intentionally inert; it contains no usable credential or private address:

```toml
[inference.extract]
base_url = "http://localhost:8080/v1"
model = "example-local-model"
api_key_env = "TOWPATH_EXTRACT_API_KEY"
destination = "local"
```

The installer should require a deliberate choice before sending personal content to a remote endpoint. Changing a profile cannot silently reroute queued items; work records the intended endpoint class, model, prompt version, and input fingerprint. Errors pause or fail work rather than falling through to another service.
