from __future__ import annotations

import gzip
import os
import shutil
import ssl
import tempfile
import urllib.request
from pathlib import Path

import certifi

DATA_DIR = Path.home() / ".cache" / "zh-reader-preprocessor"
CEDICT_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"
HSK_URL = "https://raw.githubusercontent.com/drkameleon/complete-hsk-vocabulary/main/complete.json"


def install_data(data_dir: Path = DATA_DIR) -> tuple[Path, Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    cedict_target = data_dir / "cedict_ts.u8"
    hsk_target = data_dir / "complete-hsk-vocabulary.json"
    _download_decompressed(CEDICT_URL, cedict_target)
    _download(HSK_URL, hsk_target)
    return cedict_target, hsk_target


def _https_context() -> ssl.SSLContext:
    paths = ssl.get_default_verify_paths()
    if (paths.cafile and Path(paths.cafile).is_file()) or (
        paths.capath and Path(paths.capath).is_dir()
    ):
        return ssl.create_default_context()
    ca_file = os.environ.get("SSL_CERT_FILE")
    if ca_file and Path(ca_file).is_file():
        return ssl.create_default_context(cafile=ca_file)
    return ssl.create_default_context(cafile=certifi.where())


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "zh-reader-preprocessor/0.1"})
        with urllib.request.urlopen(
            request, timeout=60, context=_https_context()
        ) as response, temporary_path.open("wb") as output:
            if not response.geturl().startswith("https://"):
                raise OSError(f"Refusing an insecure redirect while downloading {url}.")
            shutil.copyfileobj(response, output)
        if temporary_path.stat().st_size == 0:
            raise OSError(f"Downloaded an empty data file from {url}.")
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)


def _download_decompressed(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as compressed:
        compressed_path = Path(compressed.name)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as unpacked:
        unpacked_path = Path(unpacked.name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "zh-reader-preprocessor/0.1"})
        with urllib.request.urlopen(
            request, timeout=60, context=_https_context()
        ) as response, compressed_path.open("wb") as output:
            if not response.geturl().startswith("https://"):
                raise OSError(f"Refusing an insecure redirect while downloading {url}.")
            shutil.copyfileobj(response, output)
        with gzip.open(compressed_path, "rb") as source, unpacked_path.open("wb") as output:
            shutil.copyfileobj(source, output)
        if unpacked_path.stat().st_size == 0:
            raise OSError(f"Downloaded an empty data file from {url}.")
        unpacked_path.replace(target)
    finally:
        compressed_path.unlink(missing_ok=True)
        unpacked_path.unlink(missing_ok=True)
