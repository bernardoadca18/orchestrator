"""Bounded-memory, reproducible ZIP transport for already validated artifacts."""

from collections import deque
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo


def stream_package_zip(files: list[tuple[str, Path]]):
    chunks: deque[bytes] = deque()

    class Sink:
        position = 0

        def write(self, data):
            self.position += len(data)
            if data:
                chunks.append(data)
            return len(data)

        def tell(self):
            return self.position

        def flush(self):
            pass

    with ZipFile(Sink(), "w", compression=ZIP_STORED) as archive:
        for name, path in files:
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.file_size = path.stat().st_size
            with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                while data := source.read(256 * 1024):
                    target.write(data)
                    while chunks:
                        yield chunks.popleft()
            while chunks:
                yield chunks.popleft()
    while chunks:
        yield chunks.popleft()
