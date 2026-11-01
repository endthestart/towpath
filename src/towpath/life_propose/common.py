"""The task's limits, its answer schema, and the instruction given to the model."""



TASK = "life.propose"
PROMPT_VERSION = "life.propose/2"
REQUEST_TARGET, REQUEST_KEY = "life:request", "slice"
AUTO_TARGET, AUTO_KEY = "life:auto", "state"
SLICE_SECONDS = 90  # per worker poll, so mail checks and searches keep running
RETRY_SECONDS = 30 * 60  # after the mail account stopped (a sign-in that expired, a server error)
MODEL_RETRY_SECONDS = 10 * 60  # after the model couldn't be reached
BUSY_RETRY_SECONDS = 60  # a gateway serving one request at a time answers 409 while it's busy
BATCH = 20  # candidate messages read from the index at a time
TEXT_LIMIT = 12_000  # characters of a message sent to the model
EXCERPT_LIMIT = 300
SKIP_LABELS = {"CATEGORY_PROMOTIONS", "SPAM", "TRASH"}  # filed as advertising or junk by the provider
NOT_ABOUT_THE_OWNER = ("marketing", "spam")

# Subject words that usually mark a message about something in the owner's life rather than advertising.
WORDS = ("booking", "reservation", "itinerary", "flight", "boarding", "hotel", "check-in", "confirmed",
         "confirmation", "your order", "receipt", "invoice", "shipped", "appointment", "offer letter", "welcome to",
         "admission", "accepted", "graduation", "congratulations", "wedding", "birthday", "lease", "closing",
         "moving", "interview", "tickets", "trip", "travel", "visa", "passport", "registration", "enrollment",
         "certificate", "diploma", "adoption", "birth", "condolence")

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["email", "events"],
    "properties": {"email": {"type": "string", "enum": ["personal", "transaction", "notification", "marketing",
                                                        "spam"]},
                   "events": {"type": "array", "maxItems": 5, "items": {
        "type": "object", "additionalProperties": False,
        "required": ["statement", "significance", "modality", "kind", "expression", "earliest", "latest", "precision",
                     "excerpt"],
        "properties": {
            "statement": {"type": "string", "maxLength": 200},
            "significance": {"type": "string", "enum": ["milestone", "notable", "routine"]},
            "modality": {"type": "string", "enum": ["plan", "occurred"]},
            "kind": {"type": "string", "enum": ["event", "activity", "accomplishment"]},
            "expression": {"type": "string", "maxLength": 80},
            "earliest": {"type": "string", "maxLength": 10},
            "latest": {"type": "string", "maxLength": 10},
            "precision": {"type": "string", "enum": ["day", "month", "year", "range"]},
            "excerpt": {"type": "string", "maxLength": EXCERPT_LIMIT}}}}}}

INSTRUCTION = """You read one email from the owner's own mailbox and decide whether it records something in the
owner's life.

First say what kind of email it is (email):
- "personal": written by a person to the owner.
- "transaction": a record of something the owner arranged or did: a booking, ticket, order, receipt, appointment,
  application, contract or payment.
- "notification": an automated notice: shipping and delivery updates, sign-ups and welcomes, password and security
  alerts, reminders, statements.
- "marketing": advertising, newsletters, offers, sales, surveys, recommendations, points and rewards.
- "spam": unsolicited or suspicious mail: prizes, scams, phishing, pre-approved offers, mail from strangers.
Marketing and spam are not about the owner's life: give no events for them.

Then list the events in the owner's life the email is evidence of. Most emails contain none: then "events" is [].

For each event give:
- statement: one short sentence, such as "Booked a flight to Example City" or "Started a job at Example Lock".
- significance: "milestone" for what changes the course of a life (a new job or leaving one, a move or new home,
  a degree, a marriage, a birth or death, a serious illness, buying a home or car); "notable" for what someone
  would tell a friend about (a trip, a concert, show or game, a wedding or party, a course, a big or meaningful
  purchase, a medical procedure); "routine" for everyday things (ordinary purchases such as a phone case or
  groceries, deliveries, bills, renewals, subscriptions, sign-ups, routine appointments).
- modality: "plan" when the email arranges something for later (a booking, reservation or appointment);
  "occurred" when it confirms something happened (an order placed, a payment made, a degree awarded).
- kind: "event", "activity" or "accomplishment".
- expression, earliest, latest, precision: when the event is or was, as stated in the email; dates as YYYY-MM-DD;
  precision "day", "month", "year" or "range". If the email gives no other date, use the email's date.
- excerpt: an exact quote copied from the email (under 300 characters) that supports the event."""
