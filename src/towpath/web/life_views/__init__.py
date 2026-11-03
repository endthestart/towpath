"""The Life page: the life story's settings now, and the claims proposed for review as they arrive.

The owner chooses which mail accounts the life story may use and whether the text of that mail may be sent to the
AI model connected on the Connections page. Both are decisions in the decisions store; nothing is proposed until
both are given.
"""

from towpath.web.life_views.asking import ask, pairs
from towpath.web.life_views.backup import export, restore
from towpath.web.life_views.claims import evidence, forget, keep_evidence, remember, review, share
from towpath.web.life_views.page import account, auto, model, overview, pause, profile, run_once, start, status
from towpath.web.life_views.story_pages import memories, memory, memory_decide, story, timeline, today, year

__all__ = [
    "account", "ask", "auto", "evidence", "export", "forget", "keep_evidence", "memories", "memory", "memory_decide",
    "model", "overview", "pairs", "pause", "profile", "remember", "restore", "review", "run_once", "share", "start",
    "status", "story", "timeline", "today", "year",
]
