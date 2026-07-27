from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence, cast


class ProcessTreeError(OSError):
    """A process tree could not be created or terminated safely."""


@dataclass(slots=True)
class ProcessTree:
    process: subprocess.Popen[bytes]
    windows_job_handle: int | None = None

    def terminate(self) -> None:
        if os.name == "nt":
            if self.windows_job_handle is None:
                raise ProcessTreeError("missing Windows job object")
            _terminate_windows_job(self.windows_job_handle)
            return
        try:
            kill_process_group = cast(Callable[[int, int], None], getattr(os, "killpg"))
            kill_process_group(self.process.pid, int(getattr(signal, "SIGKILL")))
        except ProcessLookupError:
            return
        except OSError as error:
            try:
                self.process.kill()
            finally:
                raise ProcessTreeError("failed to terminate POSIX process group") from error

    def close(self) -> None:
        if self.windows_job_handle is not None:
            _close_windows_handle(self.windows_job_handle)
            self.windows_job_handle = None
        elif os.name != "nt":
            try:
                self.terminate()
            except ProcessTreeError:
                pass


def spawn_process_tree(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> ProcessTree:
    if os.name == "nt":
        return _spawn_windows_process_tree(argv, cwd=cwd, env=env)
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
    except OSError as error:
        raise ProcessTreeError("failed to start POSIX process group") from error
    return ProcessTree(process)


def _spawn_windows_process_tree(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> ProcessTree:
    job_handle = _create_windows_job()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | _CREATE_SUSPENDED,
        )
        _assign_process_to_windows_job(job_handle, process.pid)
        _resume_windows_process(process.pid)
        return ProcessTree(process, job_handle)
    except (OSError, ProcessTreeError) as error:
        if process is not None:
            try:
                _terminate_windows_job(job_handle)
            except (OSError, ProcessTreeError):
                pass
            try:
                process.kill()
            except OSError:
                pass
            process.communicate()
        _close_windows_handle(job_handle)
        raise ProcessTreeError("failed to start Windows process tree") from error


_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002


def _create_windows_job() -> int:
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class BASIC_LIMIT_INFORMATION(ctypes.Structure):
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

    class EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise ProcessTreeError("CreateJobObjectW failed")
    value = int(handle)
    information = EXTENDED_LIMIT_INFORMATION()
    information.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    configured = kernel32.SetInformationJobObject(
        handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    )
    if not configured:
        _close_windows_handle(value)
        raise ProcessTreeError("SetInformationJobObject failed")
    return value


def _assign_process_to_windows_job(job_handle: int, process_id: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    process_handle = kernel32.OpenProcess(
        _PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
        False,
        process_id,
    )
    if not process_handle:
        raise ProcessTreeError("OpenProcess failed")
    try:
        if not kernel32.AssignProcessToJobObject(job_handle, process_handle):
            raise ProcessTreeError("AssignProcessToJobObject failed")
    finally:
        kernel32.CloseHandle(process_handle)


def _resume_windows_process(process_id: int) -> None:
    import ctypes
    from ctypes import wintypes

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    invalid_handle = ctypes.c_void_p(-1).value
    if not snapshot or int(snapshot) == invalid_handle:
        raise ProcessTreeError("CreateToolhelp32Snapshot failed")
    resumed = False
    entry = THREADENTRY32()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        has_entry = bool(kernel32.Thread32First(snapshot, ctypes.byref(entry)))
        while has_entry:
            if entry.th32OwnerProcessID == process_id:
                thread = kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if thread:
                    try:
                        if kernel32.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed = True
                    finally:
                        kernel32.CloseHandle(thread)
            has_entry = bool(kernel32.Thread32Next(snapshot, ctypes.byref(entry)))
    finally:
        kernel32.CloseHandle(snapshot)
    if not resumed:
        raise ProcessTreeError("no suspended process thread could be resumed")


def _terminate_windows_job(job_handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    if not kernel32.TerminateJobObject(job_handle, 1):
        raise ProcessTreeError("TerminateJobObject failed")


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)
