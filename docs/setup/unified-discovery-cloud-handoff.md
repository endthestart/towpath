# Cloud handoff: unified discovery foundation

Work only from GitHub-visible code and synthetic fixtures. The local operator owns all account, NAS and deployment validation. This document is an implementation handoff prepared for the owner to dispatch; writing it does not send a message or start another task.

## Starting point and scope

Branch from the latest `codex/local-email-ui` planning revision, recording its exact base commit before work. Use a separate development branch and PR. Read [vision](../vision.md), [ADR 0001](../adrs/0001-reference-first-digital-life.md), [the weekly plan](../plans/2026-10-05-local-iteration-week.md) and [the foundation specification](../specs/unified-discovery-foundation.md), plus existing interfaces, discovery/context policy and store roles.

The first implementation package is a common source/search/evidence surface, a read-only IMAP adapter, an integrated local search UI, durable collection definitions and an agent-readable context interface. Reuse the existing Gmail connector, file providers, request/queue boundaries and grants. Provider search or an existing local backend may serve content search; never call metadata coverage a full-content index. Do not implement a replacement crawler, MIME/archive parser suite, full-text engine or vector database.

## Deliver in reviewable increments

1. **Contracts and fixtures:** source capabilities/coverage, result envelope, filters, per-source paging/errors, evidence/version/citation metadata. Keep legacy mail/file shapes and existing file packets compatible.
2. **Provider adapters:** verify an existing IMAP client/library's current license, API, maintenance and read-only behavior before choosing it. Test UIDVALIDITY/UID identity, read-only selection, PEEK, interruptions, folder changes and no write/Seen side effects. Extend Gmail query/content paths without scope or pacing changes. Reuse file-provider and manifest evidence interfaces.
3. **UI and collections:** one source-aware keyword search view, honest status, selected-content requests, stable reference inspection and saved queries/explicit reference sets. The UI receives no provider credentials and executes no provider subprocess.
4. **Agent/evidence surface:** JSON CLI or local API for scoped search/describe/context; collection evaluation; typed-date and claim schema validation/round trips. Preserve exclusions, source/consumer grants, stale-reference behavior, untrusted-text labeling and excerpt budgets.

Supply each increment with tests, changed docs, a commit and exact local acceptance steps. Add synthetic regressions for failures before fixing them. Run Ruff, fixture-domain scan, relative-link checks and the full test suite before committing. Report optional native skips accurately.

Use invented Gmail/IMAP messages, NEF filename/metadata records, unreadable formats, nested archive locators, project manifests and qualification receipts. No actual owner paths, catalogs, hostnames, messages, photos or recovered code may enter fixtures or prompts.

## Boundaries

No main merge, publication, deployment, live login, physical transfer, deletion, file organization, repository repair, mail mutation or automatic model processing. No private infrastructure access. Model enrichment, MCP transport and RAG are later/stretch layers; the shared retrieval contract is the core.

The separate release lane is currently blocked by a GHCR source-blob upload failure. Main's second live publishing attempt [37361271203](https://github.com/endthestart/towpath/actions/runs/37361271203) failed at PATCH with HTTP 416 after container checks and source collection. Another agent can investigate that as a separate bounded task. Do not disable source-availability, visibility or retention gates, and do not build images on deployment hosts as a workaround.

Return the PR and commits, which operations exist, which remain synthetic, provider/license evidence, passing checks, and a private-configuration-free local handoff. The owner/local operator will supply credentials and source paths and validate the actual service behavior.
