"""sist2 capability slot: probe and version only.

sist2 4.2.3 keeps ZIP > mbox > attachment lineage (verified on synthetic
files), but has no documented command-line way to read a bounded excerpt or
recover a nested original, and its raw index schema is documented as unstable.
Until such an interface is verified, search and the rest report "not
implemented" rather than reading sist2's index files or guessing endpoints.
"""

from towpath.discovery.providers.base import BaseProvider, Unavailable
from towpath.discovery.run import ToolError, run

REASON = ("sist2 adapter is a capability slot: {op} is not implemented until a stable interface is verified "
          "(docs/evaluations/file-discovery-providers.md)")


class Provider(BaseProvider):
    adapter = "sist2"
    capabilities = {"probe": "verified", "search": "not-implemented", "describe": "not-implemented",
                    "excerpt": "not-implemented", "recover": "not-implemented", "enumerate": "not-implemented",
                    "root-scoped-query": "not-implemented", "lineage": "verified (evaluation only)"}

    def probe(self, timeout: float) -> dict:
        try:
            out = run(list(self.config.options["command"]) + ["--version"], timeout=timeout, max_output=10_000)
        except ToolError as exc:
            raise Unavailable(f"sist2 {exc.code}: {exc.detail}") from None
        lines = out.stdout.decode(errors="replace").strip().splitlines()
        return {"tool": "sist2", "version": lines[-1] if lines else "unknown", "slot_only": True}

    def search(self, query, roots, max_rows, timeout):
        raise Unavailable(REASON.format(op="search"))

    def enumerate(self, root, max_rows, timeout):
        raise Unavailable(REASON.format(op="enumerate"))

    def describe(self, native_id, timeout):
        raise Unavailable(REASON.format(op="describe"))

    def excerpt(self, native_id, start, max_bytes, timeout):
        raise Unavailable(REASON.format(op="excerpt"))

    def recover(self, native_id, dest, max_bytes, timeout):
        raise Unavailable(REASON.format(op="recover"))
