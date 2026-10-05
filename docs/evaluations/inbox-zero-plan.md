# Inbox Zero integration evaluation plan

Status: **planned, not run.** This plan is public; results from a real mailbox stay private. Only a generic summary (what worked, what did not, with no account data) is published afterwards.

## Purpose

Confirm that Inbox Zero fits as Towpath's first [mail-management provider](../mail-management.md) before it is used with a primary account, and answer every point marked "to verify" or "to exercise" in the [provider contract](../interfaces.md#6-mail-management-provider).

## Setup

- A **dedicated test Google account** with a small amount of mail, not the owner's primary account.
- **Two separate Google Cloud projects**: one for Inbox Zero (its write scopes), one for Towpath (`gmail.readonly`). Both published to production unverified, used only by the owner.
- Inbox Zero self-hosted from its documented Compose setup, with its external API enabled.
- A local OpenAI-compatible model endpoint the owner controls; no other model credentials present in Inbox Zero's environment.

## Checks

| # | Area | Check | Pass when |
| --- | --- | --- | --- |
| 1 | Google separation | Towpath's token info lists only `gmail.readonly`, before and after Inbox Zero is connected | No broader scope ever appears on Towpath's token |
| 2 | API key | A key with only `STATS_READ` and `RULES_READ` reads statistics and rules, and is refused for rule changes and unsubscribe | Refusals confirmed |
| 3 | Statistics | Overview numbers match what the test mailbox contains | Plausible and stable across calls |
| 4 | Rules | Rules created in Inbox Zero's UI appear through the API with conditions and actions | Complete enough for a read-only list |
| 5 | Model roles | Every role (default, economy, chat, draft, lightweight) and every fallback list is set to the local endpoint; requests observed at the endpoint and in network logs | No request reaches any other host, including on errors and fallbacks |
| 6 | Automatic actions | Rules limited to label, archive, and draft; no send, reply, forward, or delete actions configured | No message is sent, forwarded, or deleted during the test |
| 7 | Drafts | Drafts appear in Gmail and are never sent without the person | Confirmed |
| 8 | Unsubscribe | Behavior of its unsubscriber on synthetic newsletters the owner controls | Methods and side effects understood and documented |
| 9 | Webhook | With and without the private-address setting, from a rule to a Towpath test endpoint | Decide whether the setting is acceptable; events treated as hints |
| 10 | Data | What Inbox Zero stores in its database about messages | Documented for backup and deletion guidance |
| 11 | Operations | Resource use, startup, upgrade, and backup of its Postgres and Redis | Acceptable on a typical home server |
| 12 | License labeling | Compose profile and docs state its terms accurately | Matches its LICENSE file |

## Outcome

One of: adopt as the `mail` profile; adopt with conditions (recorded in [decisions](../decisions.md)); or reject and evaluate an alternative provider or Towpath-built features.
