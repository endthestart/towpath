from contextlib import closing

import pytest

from towpath import request_worker, stores
from towpath.unified import requests
from towpath.unified.contracts import Filters
from tests.unified_env import Uni


def test_singleton_refuses_second_worker_and_releases_after_exception(tmp_path):
    with pytest.raises(ValueError), request_worker.singleton(tmp_path):
        with pytest.raises(RuntimeError, match="another request worker"):
            with request_worker.singleton(tmp_path):
                pytest.fail("second worker entered")
        raise ValueError("stop")
    with request_worker.singleton(tmp_path):
        pass


def test_idle_worker_does_not_connect_and_receipts_survive_restart(tmp_path, monkeypatch):
    uni = Uni(tmp_path, monkeypatch)
    try:
        with closing(stores.open_store(uni.config.store_dir, "queue", "web")):
            pass
        calls = []
        # The idle path must not build any connector/provider.
        from towpath.unified import sources
        saved = sources.build_adapters
        monkeypatch.setattr(sources, "build_adapters", lambda *a, **kw: calls.append(True) or saved(*a, **kw))
        assert request_worker.run_once(uni.config)["searches"] == 0
        assert calls == []
        requests.request_search(uni.config.store_dir, Filters.parse("canal"))
        assert request_worker.run_once(uni.config)["searches"] == 1
        assert calls == [True]
        assert request_worker.run_once(uni.config)["searches"] == 0
        assert calls == [True]
        with closing(stores.open_store(uni.config.store_dir, "source", "web")) as db:
            assert db.execute("SELECT COUNT(*) FROM cache").fetchone()[0] == 0
            row = db.execute("SELECT i.source_id, i.native_id, p.part_id FROM items i JOIN parts p USING(item_id) "
                             "WHERE i.source_id='imap-fixture' AND p.mime_type='text/plain' LIMIT 1").fetchone()
        requests.request_part(uni.config.store_dir, row["source_id"], row["native_id"] + "#part=" + row["part_id"])
        assert request_worker.run_once(uni.config)["fetched"] == 1
        assert request_worker.run_once(uni.config)["fetched"] == 0
    finally:
        uni.close()


def test_clean_stop_without_presence_result_is_reported_without_sensitive_reason(monkeypatch):
    monkeypatch.setattr(requests, "run_searches", lambda *a, **k: {"searches_run": 0})
    monkeypatch.setattr(request_worker.connect, "fetch_requests", lambda *a: {
        "fetched": 0, "failed": 0, "termination": "auth-stop", "reason": "private server detail"})
    assert request_worker.run_once(None) == {
        "searches": 0, "fetched": 0, "failed": 0, "presence_checked": 0, "termination": "auth-stop"}
