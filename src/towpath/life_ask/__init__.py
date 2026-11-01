"""Ask: questions about the owner's life, answered from their own records, every sentence cited.

The owner asks on the Ask page (decisions store). The worker gathers evidence the owner allowed: life-story events
(claims, with the owner's corrections) and messages from accounts the life story may use, found by the words of the
question, and gives them to the model chosen for ``ask.answer`` as numbered sources. The model answers in short
sentences, each naming the sources it rests on. A sentence without a source is shown as unsupported, and an answer the
sources don't give is said to be unknown. Without a model, the page shows the sources found.
"""

from towpath.life_ask.answering import answer_pending, _sources_text
from towpath.life_ask.asking import ask, questions
from towpath.life_ask.gathering import evidence

__all__ = [
    "_sources_text", "answer_pending", "ask", "evidence", "questions",
]
