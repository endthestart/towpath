"""Optional file discovery: find documents in folders, archives, and old mail backups.

Towpath does not crawl, parse, or index files itself. An existing search tool
(a *provider*, such as Recoll) owns the catalog and text index; this package
asks it bounded questions and keeps only references, coverage, and versions in
``files.db``. Human grants live in the decisions store.

Dependency direction (enforced by tests): this package may import
``towpath.canonical``, ``towpath.credentials``, ``towpath.stores`` and
``towpath.config``. Nothing in mail sync, scans, adapters, quota, or models
imports it, and it imports none of them. ``towpath.config`` imports only
``towpath.discovery.config``, and only when a ``[files]`` table is present.
"""
