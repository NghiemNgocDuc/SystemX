"""The only file delete path and the process-close guard.

Rules:
- A file may be deleted only if its real path is strictly inside an allowed root
  (temp folders, registered app caches) AND not inside any protected folder.
- Links, junctions, and other reparse points are never followed or deleted.
- Files in use are skipped and counted, never forced. Nothing here asks for admin.
- System processes are never closed. Explorer only through its own restart tool.
"""

from __future__ import annotations

import os
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..models import TouchedFile


class GuardRefusal(Exception):
    """The guard said no. The message is safe to show and record."""


def _norm(p: str | os.PathLike) -> str:
    return os.path.normcase(os.path.normpath(os.path.realpath(p)))


def _inside(child: str, root: str) -> bool:
    """True if child is strictly inside root (both already normalized)."""
    root = root.rstrip("\\/")
    return child.startswith(root + os.sep)


def _is_reparse(path: str | os.PathLike) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)) or stat.S_ISLNK(st.st_mode)


@dataclass
class DeleteReport:
    files: list[TouchedFile] = field(default_factory=list)
    bytes_freed: int = 0
    skipped_in_use: int = 0
    skipped_recent: int = 0
    skipped_links: int = 0
    refused: int = 0

    @property
    def count(self) -> int:
        return len(self.files)


class Guard:
    def __init__(self, allowed_roots: list[Path], protected: list[Path], protected_exact: list[Path] = ()):
        self.allowed = [_norm(p) for p in allowed_roots if p]
        self.protected = [_norm(p) for p in protected if p]
        # Folders that may hold allowed things below them, but must never be deleted themselves.
        self.protected_exact = {_norm(p) for p in protected_exact if p}

    # ---- files ------------------------------------------------------------

    def check_delete(self, path: str | os.PathLike) -> str:
        """Return the normalized real path if deleting it is allowed, else raise GuardRefusal."""
        if _is_reparse(path):
            raise GuardRefusal(f"Refused: {path} is a link or junction.")
        real = _norm(path)
        if real in self.protected_exact or real in self.allowed:
            raise GuardRefusal(f"Refused: {path} is a protected folder.")
        if len(Path(real).parts) <= 1:
            raise GuardRefusal(f"Refused: {path} is a drive root.")
        for prot in self.protected:
            if real == prot or _inside(real, prot):
                raise GuardRefusal(f"Refused: {path} is inside a protected folder.")
        if not any(_inside(real, root) for root in self.allowed):
            raise GuardRefusal(f"Refused: {path} is outside the folders I may clean.")
        return real

    def delete_contents(self, root: str | os.PathLike, min_age_seconds: float = 3600) -> DeleteReport:
        """Delete files under an allowed root, leaving the root itself. Skips anything
        modified in the last hour (likely in use by an installer), links, and locked files."""
        report = DeleteReport()
        root_real = _norm(root)
        if root_real not in self.allowed and not any(_inside(root_real, a) for a in self.allowed):
            raise GuardRefusal(f"Refused: {root} is not a folder I may clean.")
        if _is_reparse(root):
            raise GuardRefusal(f"Refused: {root} is a link or junction.")
        now = time.time()
        dirs_seen: list[str] = []
        stack = [str(root)]
        while stack:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                if _is_reparse(entry.path):
                    report.skipped_links += 1
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                        dirs_seen.append(entry.path)
                        continue
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    report.skipped_in_use += 1
                    continue
                if now - st.st_mtime < min_age_seconds:
                    report.skipped_recent += 1
                    continue
                try:
                    self.check_delete(entry.path)
                except GuardRefusal:
                    report.refused += 1
                    continue
                try:
                    os.remove(entry.path)
                except OSError:
                    report.skipped_in_use += 1
                    continue
                report.files.append(TouchedFile(entry.path, "deleted", st.st_size, st.st_mtime))
                report.bytes_freed += st.st_size
        for d in sorted(dirs_seen, key=len, reverse=True):
            try:
                os.rmdir(d)  # only succeeds when empty
            except OSError:
                pass
        return report

    # ---- processes --------------------------------------------------------

    def check_close(self, pid: int, *, allow_explorer: bool = False):
        import psutil

        if pid in (0, 4) or pid == os.getpid():
            raise GuardRefusal("Refused: that is a system process.")
        try:
            proc = psutil.Process(pid)
            name = proc.name().lower()
            user = proc.username()
        except psutil.NoSuchProcess:
            raise GuardRefusal("That program already closed.")
        except psutil.AccessDenied:
            raise GuardRefusal("Couldn't: that program runs with higher rights than mine.")
        if name == "explorer.exe" and not allow_explorer:
            raise GuardRefusal("Refused: Windows Explorer is only restarted through its own tool.")
        if name in SYSTEM_PROCESSES:
            raise GuardRefusal(f"Refused: {name} is part of Windows.")
        me = os.environ.get("USERNAME", "").lower()
        if me and user and not user.lower().endswith("\\" + me) and user.lower() != me:
            raise GuardRefusal("Refused: that program belongs to another account.")
        return proc


SYSTEM_PROCESSES = frozenset({
    "system", "registry", "memory compression", "secure system", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsaiso.exe", "svchost.exe",
    "dwm.exe", "fontdrvhost.exe", "sihost.exe", "ctfmon.exe", "logonui.exe", "spoolsv.exe",
    "audiodg.exe", "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe", "conhost.exe",
    "taskhostw.exe", "runtimebroker.exe", "searchindexer.exe", "wudfhost.exe", "userinit.exe",
})


# ---- default roots for this PC ----------------------------------------------

_KNOWN_FOLDERS = {
    "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
}


def _known_folder(name: str) -> Path | None:
    """Real location of a known folder. OneDrive backup often moves Documents and Desktop."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    guid = GUID()
    ctypes.oledll.ole32.CLSIDFromString(_KNOWN_FOLDERS[name], ctypes.byref(guid))
    out = ctypes.c_wchar_p()
    try:
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out))
        return Path(out.value) if out.value else None
    except OSError:
        return None
    finally:
        if out:
            ctypes.windll.ole32.CoTaskMemFree(out)


def default_guard(extra_cache_roots: list[Path] = ()) -> Guard:
    env = os.environ
    home = Path(env.get("USERPROFILE", str(Path.home())))
    allowed = [Path(env.get("TEMP", str(home / "AppData" / "Local" / "Temp")))]
    allowed += list(extra_cache_roots)

    protected: list[Path] = []
    for key in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData",
                "OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if env.get(key):
            protected.append(Path(env[key]))
    for name in _KNOWN_FOLDERS:
        protected.append(_known_folder(name) or home / name)
    protected += [home / ".ssh", home / "AppData" / "Roaming" / "Microsoft" / "Outlook",
                  home / "AppData" / "Local" / "Microsoft" / "Outlook"]

    exact = [home, home / "AppData", home / "AppData" / "Local", home / "AppData" / "Roaming"]
    return Guard(allowed, protected, exact)
