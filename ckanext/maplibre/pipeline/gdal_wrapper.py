# encoding: utf-8
"""Safe subprocess wrappers around gdal_translate / ogr2ogr / tippecanoe.

All binaries are looked up via ``shutil.which`` (or an explicit config path).
Commands are built from server-controlled args only — never interpolating raw
user input. ``subprocess.run`` is called with ``shell=False`` and ``check=False``
so the caller can inspect stderr.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60 * 60 * 6  # 6h ceiling for any single conversion.


class BinaryNotFound(RuntimeError):
    pass


class ConversionFailed(RuntimeError):
    def __init__(self, binary: str, returncode: int, stderr: str):
        super().__init__(
            f'{binary} exited with {returncode}:\n{stderr[-2000:]}'
        )
        self.binary = binary
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class CommandResult:
    binary: str
    returncode: int
    stdout: str
    stderr: str


def _resolve_binary(name: str, configured_path: str = '') -> str:
    candidate = configured_path or name
    resolved = shutil.which(candidate)
    if not resolved:
        raise BinaryNotFound(f'Required binary not found on PATH: {name}')
    return resolved


def run_command(binary: str, args: Sequence[str], *,
                configured_path: str = '',
                timeout: int = DEFAULT_TIMEOUT_SECONDS,
                cwd: Optional[str] = None) -> CommandResult:
    """Execute ``binary`` with ``args`` and capture stdout/stderr.

    Raises ``BinaryNotFound`` if the binary isn't installed.
    Raises ``ConversionFailed`` on non-zero exit.
    """
    path = _resolve_binary(binary, configured_path=configured_path)
    cmd: List[str] = [path] + [str(a) for a in args]
    log.info('maplibre pipeline running: %s', ' '.join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=None,
        )
    except subprocess.TimeoutExpired as exc:
        raise ConversionFailed(binary, -1,
                               f'Timeout after {timeout}s: {exc}') from exc
    if proc.returncode != 0:
        raise ConversionFailed(binary, proc.returncode, proc.stderr or '')
    return CommandResult(
        binary=binary,
        returncode=proc.returncode,
        stdout=proc.stdout or '',
        stderr=proc.stderr or '',
    )


def tippecanoe_version(configured_path: str = '') -> Optional[str]:
    try:
        result = run_command('tippecanoe', ['--version'],
                             configured_path=configured_path,
                             timeout=30)
    except (BinaryNotFound, ConversionFailed):
        return None
    text = (result.stderr or result.stdout or '').strip().splitlines()
    return text[0] if text else None


def gdal_version(configured_path: str = '') -> Optional[str]:
    try:
        result = run_command('gdal_translate', ['--version'],
                             configured_path=configured_path,
                             timeout=30)
    except (BinaryNotFound, ConversionFailed):
        return None
    return (result.stdout or '').strip()
