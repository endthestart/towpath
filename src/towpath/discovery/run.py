"""Run an external tool: argument list only (no shell), time limit, output cap, and kill on either."""

import os
import selectors
import subprocess
import time
from dataclasses import dataclass


class ToolError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


@dataclass
class Result:
    returncode: int
    stdout: bytes
    stderr_tail: str
    seconds: float


def run(argv: list[str], *, input_bytes: bytes = b"", timeout: float, max_output: int,
        env: dict | None = None, cwd: str | None = None) -> Result:
    if isinstance(argv, str) or not argv or not all(isinstance(a, str) for a in argv):
        raise ToolError("bad-command", "command must be a non-empty list of strings")
    started = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, cwd=cwd, start_new_session=True)
    except FileNotFoundError:
        raise ToolError("not-installed", f"{os.path.basename(argv[0])} was not found") from None
    except PermissionError:
        raise ToolError("not-executable", f"{os.path.basename(argv[0])} is not executable") from None
    out, err = bytearray(), bytearray()
    sel = selectors.DefaultSelector()
    try:
        try:
            proc.stdin.write(input_bytes)
        except BrokenPipeError:
            pass
        proc.stdin.close()
        sel.register(proc.stdout, selectors.EVENT_READ, out)
        sel.register(proc.stderr, selectors.EVENT_READ, err)
        open_streams = 2
        while open_streams:
            remaining = started + timeout - time.monotonic()
            if remaining <= 0:
                raise ToolError("timeout", f"{os.path.basename(argv[0])} exceeded {timeout:g} s")
            for key, _ in sel.select(timeout=min(remaining, 1.0)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    sel.unregister(key.fileobj)
                    open_streams -= 1
                    continue
                key.data.extend(chunk)
                if key.data is err and len(err) > 65536:
                    del err[:-65536]
                if len(out) > max_output:
                    raise ToolError("output-limit", f"output exceeded {max_output} bytes")
        try:
            proc.wait(timeout=max(0.1, started + timeout - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise ToolError("timeout", f"{os.path.basename(argv[0])} exceeded {timeout:g} s") from None
    except (ToolError, KeyboardInterrupt):
        _kill(proc)
        raise
    finally:
        sel.close()
        for stream in (proc.stdout, proc.stderr):
            stream.close()
    return Result(proc.returncode, bytes(out), err.decode("utf-8", "replace")[-2000:], time.monotonic() - started)


def _kill(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, 9)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait()
