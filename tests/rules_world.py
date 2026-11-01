"""A mailbox and a model answering by rule, for the tests of mail rules and sender categories."""

import json
import re

from tests.test_mail_threads import World, answer as thread_answer

OWNER = "correct horse battery staple"


def answer(prompt: str) -> str:
    if "<sender>" in prompt:
        if "shop.example" in prompt:
            return json.dumps({"category": "marketing", "reason": "Sale announcements."})
        return json.dumps({"category": "cold_email", "reason": "A pitch."})
    if '<sender n="' in prompt:  # several senders at once: answer for each block
        blocks = re.findall(r'<sender n="(\d+)">(.*?)</sender>', prompt, re.S)
        return json.dumps({"senders": [
            {"n": int(n), "category": "marketing" if "shop.example" in b else "cold_email",
             "reason": "Sale announcements." if "shop.example" in b else "A pitch."} for n, b in blocks]})
    if "check one email against the owner's rules" in prompt:
        ids = re.findall(r"^- (rule_[0-9a-f]+):", prompt, re.M)
        chosen = ids[0] if "Big sale" in prompt else "none"
        return json.dumps({"rule": chosen, "reason": "It is about a sale."})
    return thread_answer(prompt)


def make_world(tmp_path, openai_stub):
    w = World(tmp_path, openai_stub)
    w.model_stub.respond = answer
    yield w
    w.server.__exit__(None, None, None)


def run_all(w):
    from towpath.mail import rules as rules_mod, senders as senders_mod

    senders_mod.categorize_pending(w.worker())
    return rules_mod.evaluate_pending(w.worker())
