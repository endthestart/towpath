"""Exporting the owner's decisions and restoring them."""

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from pathlib import Path


@require_GET
def export(request):
    """Everything Towpath learned and the owner decided, as one zip to keep."""
    from django.http import HttpResponse

    from towpath import export as export_mod

    name, data = export_mod.build(settings.TOWPATH_STORE_DIR)
    response = HttpResponse(data, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


PENDING_RESTORE = "pending-restore.zip"


@require_http_methods(["GET", "POST"])
def restore(request):
    """Bring the owner's decisions back from an export: upload it, see what would be added, then add exactly the file
    whose plan was shown (its digest travels with the form)."""
    import hashlib
    import logging
    import os
    import tempfile

    from towpath import restore as restore_mod

    store = Path(settings.TOWPATH_STORE_DIR)
    pending = store / PENDING_RESTORE
    context = {"nav": "life"}

    def shown(data: bytes) -> None:
        context["plan"] = restore_mod.plan(store, data)
        context["digest"] = hashlib.sha256(data).hexdigest()

    try:
        action = request.POST.get("action") if request.method == "POST" else None
        if action == "restore":
            data = pending.read_bytes() if pending.exists() else b""
            if not data or hashlib.sha256(data).hexdigest() != request.POST.get("digest"):
                context["error"] = "That export changed since its plan was shown; choose it again."
            else:
                context["done"] = restore_mod.apply(store, data, author="owner")
            pending.unlink(missing_ok=True)
        elif action == "cancel":
            pending.unlink(missing_ok=True)
        elif request.method == "POST":
            upload = request.FILES.get("package")
            if upload is None:
                context["error"] = "Choose an export file (a towpath-export zip)."
            elif upload.size > restore_mod.MAX_TOTAL:
                context["error"] = "That file is too large to be a Towpath export."
            else:
                data = b"".join(upload.chunks())
                shown(data)
                fd, tmp = tempfile.mkstemp(dir=store, prefix=".restore-")
                with os.fdopen(fd, "wb") as f:  # mkstemp makes it readable by this service only
                    f.write(data)
                os.replace(tmp, pending)
                context["name"] = upload.name
        if "plan" not in context and "done" not in context and "error" not in context and pending.exists():
            shown(pending.read_bytes())
    except restore_mod.RestoreError as exc:
        context.pop("plan", None)
        context["error"] = str(exc)
        if request.method == "POST":
            pending.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - a package that breaks a restore is dropped, and the page still answers
        logging.getLogger(__name__).exception("restore failed")
        context.pop("plan", None)
        context["error"] = "That export couldn't be restored."
        pending.unlink(missing_ok=True)
    return render(request, "life_restore.html", context)
