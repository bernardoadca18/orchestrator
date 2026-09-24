import io
from zipfile import ZipFile

from app.services.package_zip import stream_package_zip


def test_zip_preserves_bytes_is_reproducible_and_streams(tmp_path):
    payloads = {"narration.wav": bytes(range(256)) * 10_000, "captions.srt": b"caption\n"}
    files = []
    for name, data in payloads.items():
        path = tmp_path / name
        path.write_bytes(data)
        files.append((name, path))
    chunks = list(stream_package_zip(files))
    assert max(map(len, chunks)) <= 256 * 1024
    encoded = b"".join(chunks)
    assert encoded == b"".join(stream_package_zip(files))
    with ZipFile(io.BytesIO(encoded)) as archive:
        assert archive.testzip() is None
        assert {name: archive.read(name) for name in archive.namelist()} == payloads
