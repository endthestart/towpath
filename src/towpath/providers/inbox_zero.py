"""Read-only adapter for Inbox Zero's public API (provider contract, docs/interfaces.md section 6).

It uses a key scoped to STATS_READ and RULES_READ and makes only GET requests.
Features without a public API are offered as links to Inbox Zero's own screens.
Endpoints and screen paths were read from Inbox Zero's source at commit c4edc85
(2026-10-01) and are not yet exercised against a running instance.
"""

from urllib.parse import urlencode

from towpath import credentials
from towpath.http import HttpStatusError, request_json

PERIODS = {"day", "week", "month", "year"}
SCREENS = {  # path templates under the instance URL; {account} is the Inbox Zero email account ID
    "categories": "/{account}/smart-categories",
    "important_unanswered": "/{account}/reply-zero",
    "unsubscribe": "/{account}/bulk-unsubscribe",
    "rules": "/{account}/automation",
    "statistics": "/{account}/stats",
}


class InboxZeroProvider:
    kind = "mail-management"

    def __init__(self, provider, base_dir=None):
        self.provider = provider
        self.api = provider.base_url + "/api/v1"
        self._key = credentials.resolve(provider.api_key, base_dir)

    def _get(self, path: str, params: dict | None = None):
        url = self.api + path + (("?" + urlencode(params)) if params else "")
        data, _ = request_json("GET", url, {"API-Key": self._key})
        return data

    def describe(self) -> dict:
        return {"schema": "towpath.provider/0", "provider_id": self.provider.id, "kind": self.kind,
                "adapter": "inbox-zero/0", "base_url": self.provider.base_url,
                "license_class": "source available with use restrictions"}

    def probe(self) -> dict:
        """Which read capabilities the key allows. Refusals are expected for anything else."""
        checks = {"overview": ("/stats/by-period", {"period": "week"}), "rules": ("/rules", None)}
        result = {}
        for name, (path, params) in checks.items():
            try:
                self._get(path, params)
                result[name] = "ok"
            except HttpStatusError as exc:
                result[name] = f"unavailable (HTTP {exc.status})"
        return result

    def overview(self, period: str = "week", from_ms: int | None = None, to_ms: int | None = None) -> dict:
        if period not in PERIODS:
            raise ValueError(f"period must be one of {sorted(PERIODS)}")
        window = {k: v for k, v in (("fromDate", from_ms), ("toDate", to_ms)) if v}
        params = {"period": period, **window}
        return {"by_period": self._get("/stats/by-period", params),
                "response_time": self._get("/stats/response-time", window or None)}

    def rules(self) -> list:
        data = self._get("/rules")
        return data.get("rules", data) if isinstance(data, dict) else data

    def links(self) -> dict:
        account = self.provider.links.get("account_id")
        if not account:
            return {"note": "set links.account_id (the Inbox Zero email account ID from its URL) to enable links"}
        paths = {**SCREENS, **{k: v for k, v in self.provider.links.items() if k in SCREENS}}
        return {name: self.provider.base_url + path.format(account=account) for name, path in paths.items()}
