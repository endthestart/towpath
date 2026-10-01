# Mail authority and the independent archive goal

Status: proposed design, subject to review before implementation.

“Mailbox authority” means permission to change what is in an account at Gmail, Fastmail, or another provider. Examples include applying a label, archiving a message, creating a filter, unsubscribing, disabling a masked address, moving mail to Trash, and permanently deleting it. These actions differ in reversibility and provider permissions.

## Towpath mail management

Towpath's mail read adapter can inspect live mail or a local archive. Its analysis component proposes actions and shows the reason, exact target messages, expected effects, and any uncertainty. The default is proposal only.

An optional **internal action executor** holds a separate, narrowly scoped provider credential. It can act only on a user-approved proposal frozen to one account, a set of message IDs, an action, and the provider state checked at approval time. It rechecks state before acting and records each result. If a message changed or an action failed, that item returns for review. A separate application is not required; the separation is between permissions and responsibilities inside Towpath.

Mail can remain at the provider indefinitely. Towpath's organization, important-mail review, and life-event extraction can all work in that mode. A local archive can be an input, not a mandatory destination.

## Personal Gmail evacuation

Moving an individual's historical Gmail into a local living archive and emptying Gmail is a separate preservation and migration project. It chooses an archive tool, backup destinations, restore tests, coverage checks, and a deletion schedule for that person. It may decide to retain mail at Gmail after evaluating Towpath's management features.

Towpath will define an **archive read adapter** and may expose generic verification reports useful to migrations. The migration does not inherit authority from Towpath's analysis or life-summary components. An archive product's own deletion function is not the default Towpath action executor. Provider deletion requires the migration's independent backup and per-message verification process.

## Example flow

1. Towpath finds 40 recurring newsletters and proposes labels or unsubscribes. The owner reviews and approves selected actions. The executor uses its scoped credential and records provider receipts.
2. Towpath finds 8,000 old promotions and proposes them as possible archive candidates. The owner can use the separate evacuation process to preserve and verify them. Towpath does not treat a cleanup classification as proof that deletion is safe.
3. Towpath sees a travel confirmation and proposes an event claim. The life-summary review can accept, edit, or reject the claim. That decision never grants mailbox write permission.

The unresolved product choice is how much mailbox execution Towpath should eventually support, particularly provider filters and unsubscribe flows. The first release can ship useful read and proposal workflows without a write executor. A public design review should settle the exact action types and permission scopes before those are built.
