"""One data folder per instance. The containers create everything inside it on first start.

    <data>/state         SQLite stores, the login hash and session key (the web service mounts only this)
    <data>/credentials   passwords and OAuth tokens added on the Connections page (connector only)
    <data>/index         rebuildable search indexes
    <data>/scratch       temporary files for long jobs
    <data>/towpath.toml  optional: settings not yet on a page (for example file discovery)

A person creates the data folder (on TrueNAS, a dataset) and makes the containers' user its owner; nothing
else is made by hand. See docs/setup/install.md.
"""

import os
from dataclasses import dataclass
from pathlib import Path

PRIVATE = ("credentials",)
FOLDERS = ("state", "credentials", "index", "scratch")


class LayoutError(RuntimeError):
    """The data folder can't be used; the message says what to change."""


@dataclass(frozen=True)
class Layout:
    data: Path

    @property
    def state(self) -> Path:
        return self.data / "state"

    @property
    def credentials(self) -> Path:
        return self.data / "credentials"

    @property
    def config(self) -> Path | None:
        """The optional settings file. An instance set up before the data folder existed kept it in config/."""
        for candidate in (self.data / "towpath.toml", self.data / "config" / "towpath.toml"):
            if candidate.is_file():
                return candidate
        return None


def _explain(data: Path) -> str:
    return (f"Towpath can't write to its data folder ({data}). Make the user the containers run as "
            f"(ID {os.getuid()}) the folder's owner, with read and write access; on TrueNAS, use the dataset's "
            "Edit Permissions page. Then restart Towpath.")


def prepare(data_dir: Path) -> Layout:
    """Create the layout if needed and check it can be written. Existing contents are never changed, except
    that a credentials folder from an earlier layout (``tokens/``) is renamed to ``credentials/``."""
    layout = Layout(Path(data_dir))
    if not layout.data.is_dir():
        raise LayoutError(f"Towpath's data folder ({layout.data}) is missing. Create it and restart Towpath.")
    if not os.access(layout.data, os.W_OK | os.X_OK):
        raise LayoutError(_explain(layout.data))
    legacy = layout.data / "tokens"
    if not layout.credentials.exists() and legacy.is_dir():
        legacy.rename(layout.credentials)
    try:
        for name in FOLDERS:
            path = layout.data / name
            path.mkdir(mode=0o700 if name in PRIVATE else 0o750, exist_ok=True)
            if not os.access(path, os.W_OK | os.X_OK):
                raise LayoutError(_explain(path))
    except PermissionError:
        raise LayoutError(_explain(layout.data)) from None
    os.chmod(layout.credentials, 0o700)
    return layout


def state_only(data_dir: Path) -> Layout:
    """For the web service, which sees only ``state/``: check it without creating anything else."""
    layout = Layout(Path(data_dir))
    if not layout.state.is_dir() or not os.access(layout.state, os.W_OK | os.X_OK):
        raise LayoutError(_explain(layout.state))
    return layout
