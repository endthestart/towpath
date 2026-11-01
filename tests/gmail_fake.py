"""A fake of Google's discovery client over a fixture file, returning Google-shaped responses."""

import base64
import json

import pytest

pytest.importorskip("googleapiclient")
import httplib2  # noqa: E402
from googleapiclient.errors import HttpError  # noqa: E402

from towpath import fieldmask  # noqa: E402


class Call:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeService:
    """Mimics service.users().messages()/history() chains over a fixture file."""

    def __init__(self, path, ignore_mask=False):
        self.data = json.loads(path.read_text())
        self.ignore_mask = ignore_mask
        self.calls = []

    def _404(self):
        raise HttpError(httplib2.Response({"status": 404}), b'{"error": {"code": 404}}')

    def users(self):
        return self

    def messages(self):
        return self

    def attachments(self):
        return _Attachments(self)

    def history(self):
        return _History(self)

    def getProfile(self, userId):  # noqa: N802 - Google's method name
        self.calls.append(("getProfile", {"userId": userId}))
        return Call(lambda: {"emailAddress": self.data["emailAddress"], "historyId": self.data["historyId"]})

    def list(self, **kw):
        self.calls.append(("messages.list", kw))
        ids = sorted(self.data["messages"], reverse=True)
        if kw.get("q"):  # plain words against the synthetic raw message, standing in for Gmail's search
            ids = [i for i in ids if all(w.lower() in base64.urlsafe_b64decode(
                self.data["messages"][i]["raw"] + "==").decode("utf-8", "replace").lower() for w in kw["q"].split())]
        start = int(kw.get("pageToken") or 0)
        size = kw["maxResults"]

        def run():
            out = {"messages": [{"id": i, "threadId": self.data["messages"][i]["threadId"]}
                                for i in ids[start:start + size]]}
            if start + size < len(ids):
                out["nextPageToken"] = str(start + size)
            if kw.get("q"):
                out["resultSizeEstimate"] = len(ids)
            return out
        return Call(run)

    def get(self, **kw):
        self.calls.append(("messages.get", kw))

        def run():
            msg = self.data["messages"].get(kw["id"])
            if msg is None:
                self._404()
            msg = {k: v for k, v in msg.items() if k != "raw"}
            if kw.get("fields") and not self.ignore_mask:
                return fieldmask.apply(msg, fieldmask.parse(kw["fields"]))
            return msg
        return Call(run)


class _Attachments:
    def __init__(self, svc):
        self.svc = svc

    def get(self, **kw):
        self.svc.calls.append(("attachments.get", kw))
        data = self.svc.data["attachments"][kw["id"]]
        return Call(lambda: {"size": len(data), "data": data})


class _History:
    def __init__(self, svc):
        self.svc = svc

    def list(self, **kw):
        self.svc.calls.append(("history.list", kw))
        start = int(kw["startHistoryId"])

        def run():
            if start < int(self.svc.data["historyFloor"]):
                self.svc._404()
            recs = [h for h in self.svc.data["history"] if int(h["id"]) > start]
            page = int(kw.get("pageToken") or 0)
            out = {"history": recs[page:page + 2], "historyId": self.svc.data["historyId"]}
            if page + 2 < len(recs):
                out["nextPageToken"] = str(page + 2)
            return out
        return Call(run)
