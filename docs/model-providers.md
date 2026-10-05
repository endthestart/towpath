# Model provider contract

Status: **built and tested against a loopback stub (slice 1b); not yet run against a real server.** Code: `src/towpath/models/`. Provider support will be claimed only after compatibility tests against each named endpoint. The client never uses the OpenAI SDK's environment defaults: base URL and key come only from the profile, and organization, project, and admin values read from `OPENAI_*` variables are cleared before any request.

Towpath targets configurable [OpenAI-compatible](https://developers.openai.com/api/reference/overview) HTTP endpoints. Every endpoint is explicit: base URL, credential reference, model ID, and destination class. Poundlock is one optional endpoint. No endpoint is built in, auto-detected, or used as a fallback, and personal content is never sent to a remote endpoint without a recorded, per-endpoint grant.

"OpenAI-compatible" here means the specific routes Towpath has probed on that endpoint, not every OpenAI feature. Compatible servers vary in their support for structured output, embeddings, model listing, streaming, usage fields, and error formats.

## Endpoint profile

```toml
[endpoints.local_chat]
base_url = "http://127.0.0.1:8080/v1"
credential = "env:TOWPATH_LOCAL_CHAT_KEY"   # or "file:<path>", "keyring:<name>", "none"
model = "example-local-model"
kind = "chat"                               # "chat" or "embeddings"
destination = "this-machine"                # "this-machine", "bundled", "self-hosted", or "third-party"
allow_data = ["synthetic", "metadata"]
timeout_seconds = 120

[endpoints.local_embed]
base_url = "http://127.0.0.1:8081/v1"
credential = "none"
model = "example-embedding-model"
kind = "embeddings"
destination = "this-machine"
allow_data = ["synthetic", "metadata", "content"]

[tasks]
"mail.classify" = "local_chat"
"search.embed" = "local_embed"
# "life.extract" has no binding, so that feature is disabled.
```

Profiles are created after login. API keys are entered on a setup screen served by `towpath-worker`, the service that uses them, following the same pattern as [connection setup](components.md#services); the web UI shows the profile but not the key. The example is inert: placeholder model names, loopback addresses, and an environment-variable reference with no value. Real profiles live in the private deployment ([publication rules](publication.md)).

### Destination classes

| Destination | Meaning | Check Towpath can make |
| --- | --- | --- |
| `this-machine` | Server on the same host | Base URL host must resolve only to loopback; otherwise the profile is rejected |
| `bundled` | The model server in Towpath's own Compose `models` profile | Base URL host must be that Compose service's name; otherwise the profile is rejected |
| `self-hosted` | A server the person controls elsewhere, such as a home server or a gateway like Poundlock | Cannot be verified; treated as remote for grants |
| `third-party` | A hosted API operated by someone else | Treated as remote; the review screen names the operator domain before the first grant |

### Data classes

| Class | Examples |
| --- | --- |
| `synthetic` | Probe prompts and test fixtures |
| `metadata` | Sender domain, list headers, dates, subject line (still personal data; subject lines often reveal content) |
| `content` | Message bodies, document text, recollection text |
| `attachments` | Attachment bytes or extracted text |
| `derived-personal` | Accepted claims, people and place records, timelines |

`allow_data` in the file is a ceiling. For any destination other than `this-machine` or `bundled`, each class above `synthetic` also needs a grant recorded in the model ledger by an explicit command or review action, tied to the endpoint fingerprint (base URL, model, kind, credential reference). Changing any fingerprint field voids the grants for that endpoint. Queued work records the endpoint fingerprint it was approved for and fails rather than running against a changed profile.

## Capability probing

`probe` runs only synthetic prompts and records a dated capability report in the model ledger.

| Capability | Probe | Used for |
| --- | --- | --- |
| `chat` | `POST {base_url}/chat/completions` with a fixed synthetic message | Any chat task |
| `json_schema` | Same route with `response_format` type `json_schema` and a small schema; output must validate | Preferred structured output |
| `json_object` | `response_format` type `json_object`; output must parse | Second structured-output method |
| `models_list` | `GET {base_url}/models`; check the configured model appears | Configuration check only; never used to pick a model |
| `embeddings` | `POST {base_url}/embeddings` with two synthetic strings; record vector dimension | Semantic search |
| `usage` | Presence of token usage fields in responses | Cost and size reporting; optional |
| `served_model` | Response `model` field or gateway metadata (Poundlock's route metadata, when provided) | Ledger detail; never required |

Tool calling, streaming, and the Responses API are not used by the first features and are not probed. They can be added as separate capabilities with their own tests.

## Fallbacks

Fallbacks change method on the same endpoint or turn a feature off. They never switch endpoints.

| Feature | Order of methods | When nothing works |
| --- | --- | --- |
| Structured extraction | `json_schema` → `json_object` → plain prompt asking for JSON; each output validated by Towpath against the schema; one repair attempt on invalid JSON | Deterministic rules only; item goes to manual review with the reason |
| Semantic search | Embeddings from the bound embeddings endpoint, index keyed by endpoint fingerprint and dimension | Lexical full-text search; the UI says semantic search is unavailable |
| Model listing | `GET /models` | Configured model ID is trusted as written |
| Usage reporting | Response usage fields | Towpath's own input-size estimate, marked as an estimate |

An endpoint that returns an error, times out, or produces invalid output repeatedly pauses its queue and reports the reason. Nothing is retried elsewhere.

## Gateway behavior

The inference gateway runs in `towpath-worker` and is the only Towpath code that makes outbound model calls ([interfaces](interfaces.md#5-inference-gateway-inside-worker)). For each call it:

1. Resolves the task binding; no binding means `Disabled`.
2. Refuses any input whose [model use](life-stream.md#audience-and-model-use) is `excluded`, or `local-only` when the endpoint is not `this-machine` or `bundled`; then checks the data class against the profile ceiling and, for non-local destinations, the ledger grant.
3. Selects the structured-output method from the latest capability report.
4. Sends the minimal input the task needs (for example, metadata only for list detection).
5. Validates the output and records endpoint fingerprint, model, served model if reported, prompt version, input fingerprint, outcome, and usage if available.

Endpoints receive no tools and no credentials for other services. Output is treated as a proposal.

**Integrated tools have their own model settings.** A tool such as the mail-management provider calls its own configured model directly, outside this gateway, so Towpath's grants and item-level model use do not govern it. Pointing such a tool at a local endpoint controls where its requests go, not which items it sends. Towpath's setup configures every model role and fallback the tool has, shows which endpoint each uses, and makes no privacy claim for the tool until that configuration has been verified ([mail management](mail-management.md#caveats)).

## Endpoint examples

All of these are configured the same way and none is preferred by Towpath: a local llama.cpp or other compatible server, Ollama's OpenAI-compatible API, Poundlock or another self-hosted gateway, or OpenAI's API ([chat completions reference](https://developers.openai.com/api/reference/cli/resources/chat/subresources/completions), [embeddings guide](https://developers.openai.com/api/docs/guides/embeddings)) configured with a `third-party` destination.
