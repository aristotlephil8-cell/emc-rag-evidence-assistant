from __future__ import annotations

import multiprocessing
import os
import signal
import threading
from contextlib import suppress
from multiprocessing.connection import Connection
from pathlib import Path
from time import monotonic

from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.schemas.ingestion import ParsedDocument

DEEPDOC_MEMORY_BYTES = 2 * 1024 * 1024 * 1024
DEEPDOC_WORKER_TIMEOUT_SECONDS = 120.0
_DEEPDOC_SEMAPHORE = threading.BoundedSemaphore(value=1)

_WINDOWS_RESOURCE_EXIT_CODES = {
    0xC0000017,  # STATUS_NO_MEMORY
    0xC000009A,  # STATUS_INSUFFICIENT_RESOURCES
}
_POSIX_RESOURCE_SIGNALS = {
    member
    for name in ("SIGKILL", "SIGXCPU", "SIGXFSZ")
    if (member := getattr(signal, name, None)) is not None
}


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - monotonic())


def _apply_memory_limit() -> None:
    if os.name != "posix":
        return
    import resource

    resource.setrlimit(
        resource.RLIMIT_AS,
        (DEEPDOC_MEMORY_BYTES, DEEPDOC_MEMORY_BYTES),
    )


def _worker_entry(
    connection: Connection,
    filename: str,
    content: bytes,
    model_dir: str,
) -> None:
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["NO_PROXY"] = "*"
        _apply_memory_limit()
        from app.ingestion.deepdoc import parse_deepdoc_pdf

        result = parse_deepdoc_pdf(filename, content, Path(model_dir))
        connection.send(("ok", result))
    except IngestionError as exc:
        connection.send(("error", exc.code.value, exc.safe_message))
    except MemoryError:
        connection.send(
            (
                "error",
                IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED.value,
                "DeepDOC worker exceeded the 2 GiB memory limit",
            )
        )
    except BaseException:
        with suppress(BaseException):
            connection.send(
                (
                    "error",
                    IngestionErrorCode.PARSE_FAILED.value,
                    "DeepDOC worker failed",
                )
            )
    finally:
        connection.close()


def _stop_process(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        process.join(timeout=1)
        return
    process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)


def _resource_exit(exitcode: int | None) -> bool:
    if exitcode is None:
        return False
    if exitcode < 0 and -exitcode in _POSIX_RESOURCE_SIGNALS:
        return True
    return exitcode & 0xFFFFFFFF in _WINDOWS_RESOURCE_EXIT_CODES


def run_deepdoc_worker(
    filename: str,
    content: bytes,
    model_dir: Path,
) -> ParsedDocument:
    """Run DeepDOC under one 120 second deadline and a 2 GiB POSIX cap.

    Windows does not expose an ``RLIMIT_AS`` equivalent in the Python standard
    library. The worker boundary and timeout still apply there; deployment
    should additionally enforce the container memory limit.
    """

    deadline = monotonic() + DEEPDOC_WORKER_TIMEOUT_SECONDS
    acquired = False
    receive: Connection | None = None
    send: Connection | None = None
    process: multiprocessing.Process | None = None
    try:
        if not _DEEPDOC_SEMAPHORE.acquire(timeout=_remaining(deadline)):
            raise IngestionError(
                IngestionErrorCode.PARSE_TIMEOUT,
                "DeepDOC worker queue exceeded the 120 second limit",
            )
        acquired = True
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(
            target=_worker_entry,
            args=(send, filename, content, str(model_dir)),
            daemon=True,
            name="cvrag-deepdoc",
        )
        process.start()
        send.close()
        send = None
        remaining = _remaining(deadline)
        if remaining <= 0 or not receive.poll(remaining):
            _stop_process(process)
            raise IngestionError(
                IngestionErrorCode.PARSE_TIMEOUT,
                "DeepDOC parsing exceeded the 120 second limit",
            )
        payload = receive.recv()
        process.join(timeout=_remaining(deadline))
        if payload[0] == "ok":
            return payload[1]
        _, raw_code, safe_message = payload
        raise IngestionError(IngestionErrorCode(raw_code), safe_message)
    except (EOFError, BrokenPipeError, OSError) as exc:
        exitcode = process.exitcode if process is not None else None
        if process is not None:
            _stop_process(process)
        limited = _resource_exit(exitcode)
        raise IngestionError(
            (
                IngestionErrorCode.RESOURCE_LIMIT_EXCEEDED
                if limited
                else IngestionErrorCode.PARSE_FAILED
            ),
            (
                "DeepDOC worker exceeded its resource limit"
                if limited
                else "DeepDOC worker stopped before producing a result"
            ),
        ) from exc
    finally:
        if process is not None and process.is_alive():
            _stop_process(process)
        if receive is not None:
            receive.close()
        if send is not None:
            send.close()
        if acquired:
            _DEEPDOC_SEMAPHORE.release()


__all__ = [
    "DEEPDOC_MEMORY_BYTES",
    "DEEPDOC_WORKER_TIMEOUT_SECONDS",
    "run_deepdoc_worker",
]
