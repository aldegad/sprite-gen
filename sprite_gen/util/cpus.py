# SPDX-License-Identifier: Apache-2.0
"""How many CPUs this process may use: the one count the places that start work side by side size themselves by
(`face_pack.build`'s readers, the RIFE binary's runs started together, `rife.runs_at_once`).

Python's own count of them where it keeps one (3.13 on: `os.process_cpu_count`, which `PYTHON_CPU_COUNT` or
`-X cpu_count` sets, so a caller that already runs several of these processes side by side gives each its share);
else the CPUs the process is bound to where the platform says (`os.sched_getaffinity`); else every CPU.
"""
from __future__ import annotations

import os


def usable() -> int:
    """The CPUs this process may use, at least one."""
    count = getattr(os, "process_cpu_count", None)
    if count is not None:
        return max(1, count() or 1)
    if hasattr(os, "sched_getaffinity"):
        return max(1, len(os.sched_getaffinity(0)))
    return max(1, os.cpu_count() or 1)
