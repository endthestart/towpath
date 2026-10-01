# Public code and private deployments

Towpath is developed in this public repository. It can be used against private data without committing that data to GitHub.

| In this repository | In a user's private deployment |
| --- | --- |
| Application code and database migrations | Mail, messages, photos, documents, exports, backups |
| Generic architecture and operating guides | Account IDs, contact lists, personal timelines, actual prompts/results from private material |
| Example configuration with placeholders | API keys, OAuth tokens, provider sessions, actual endpoint URLs |
| Synthetic tests and fixtures | Real mail or files used for acceptance tests |
| Public benchmark methodology | Machine inventory, hostnames, network addresses, personal cost/throughput logs |

Private deployment configuration should live outside the source checkout or in a local path excluded from version control. Secrets should come from environment variables or a secret store; published examples use nonfunctional placeholders. Ignore rules are a backstop, not a substitute for checking a commit before pushing.

Reports based on someone's real accounts, network, storage, or legacy files stay private. Public issues and pull requests should use synthetic reproduction cases. Telemetry and model calls are separate: a local deployment may use local models, while a user can explicitly choose a remote provider. Towpath should show the chosen destination before data is sent.

Before each public release, inspect tracked paths, diff, and history for personal data and credentials; use a fresh export if a mistake ever entered Git history. Do not copy the owner-specific research and plan that preceded this public repository into it.
