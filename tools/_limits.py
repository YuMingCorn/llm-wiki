"""Resource limits for every tool: at most 4 threads and 8 GB of memory.

Import this module before numpy, onnxruntime or other native libraries.

- Threads: the usual thread-count variables of native math libraries are set
  to 4 unless the caller already set them lower. Code that creates its own
  pools (onnxruntime sessions, fastembed) passes ``THREADS`` explicitly.
- Memory: on Windows, the process is put into a Job Object whose total
  committed memory (this process and its children) is capped at 8 GB. Going
  over makes the allocation fail with an error instead of exhausting the
  machine. On other systems ``RLIMIT_AS`` is used where available.

The environment variables ``WIKI_MAX_THREADS`` and ``WIKI_MAX_MEMORY_GB``
can lower the limits but never raise them above 4 threads and 8 GB.
"""

from __future__ import annotations

import os
import sys

MAX_THREADS, MAX_MEMORY_GB = 4, 8  # resource ceiling for every tool run


def _env_number(name: str, ceiling: float) -> float:
    """Reads an override that may only lower the ceiling."""
    try:
        value = float(os.environ.get(name, ceiling))
    except ValueError:
        return ceiling
    return min(value, ceiling) if value > 0 else ceiling


THREADS = int(_env_number("WIKI_MAX_THREADS", MAX_THREADS))
MEMORY_BYTES = int(_env_number("WIKI_MAX_MEMORY_GB", MAX_MEMORY_GB) * 2**30)
_THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
                "RAYON_NUM_THREADS")
_applied = False


def _limit_threads() -> None:
    for name in _THREAD_VARS:
        current = os.environ.get(name, "")
        if not current.isdigit() or not 0 < int(current) <= THREADS:
            os.environ[name] = str(THREADS)
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _limit_memory_windows() -> bool:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_limit_job_memory = 0x200
    extended_limit_information = 9
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = job_object_limit_job_memory
    info.JobMemoryLimit = MEMORY_BYTES
    if not kernel32.SetInformationJobObject(
            wintypes.HANDLE(job), extended_limit_information,
            ctypes.byref(info), ctypes.sizeof(info)):
        return False
    # The job handle is kept open on purpose: closing it would not lift the
    # limit, and the process ends when the tool ends.
    return bool(kernel32.AssignProcessToJobObject(
        wintypes.HANDLE(job), wintypes.HANDLE(kernel32.GetCurrentProcess())))


def _limit_memory_posix() -> bool:
    try:
        import resource  # noqa: PLC0415
    except ImportError:
        return False
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    limit = MEMORY_BYTES if hard == resource.RLIM_INFINITY else min(
        MEMORY_BYTES, hard)
    resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    return True


def apply() -> bool:
    """Applies both limits once; returns whether memory is capped."""
    global _applied
    if _applied:
        return True
    _limit_threads()
    try:
        capped = (_limit_memory_windows() if sys.platform == "win32"
                  else _limit_memory_posix())
    except (OSError, ValueError, AttributeError):
        capped = False
    _applied = capped
    return capped


apply()
