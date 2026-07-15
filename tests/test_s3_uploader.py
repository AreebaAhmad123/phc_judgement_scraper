"""s3_uploader.py tests with a fake boto3 client - no real AWS calls."""
import os
import tempfile

import pytest
from botocore.exceptions import ClientError

from phc_scraper import s3_uploader


def _not_found_error(op):
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, op)


class _FakeS3Client:
    def __init__(self, existing_keys=None):
        self.existing_keys = set(existing_keys or [])
        self.uploaded = []
        self.downloaded = []

    def head_object(self, Bucket, Key):
        if Key not in self.existing_keys:
            raise _not_found_error("HeadObject")
        return {"ContentLength": 123}

    def upload_file(self, local_path, Bucket, Key, ExtraArgs=None):
        self.uploaded.append((local_path, Bucket, Key, ExtraArgs))
        self.existing_keys.add(Key)

    def download_file(self, Bucket, Key, local_path):
        self.downloaded.append((Bucket, Key, local_path))
        with open(local_path, "w") as f:
            f.write("{}")


@pytest.fixture
def tmp_file():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    with open(path, "w") as f:
        f.write("dummy pdf content")
    yield path
    os.remove(path)


def test_upload_skips_when_key_already_exists(monkeypatch, tmp_file):
    fake = _FakeS3Client(existing_keys={"pdfs/foo.pdf"})
    monkeypatch.setattr(s3_uploader, "_client", lambda: fake)
    s3_uploader.upload_file(tmp_file, "pdfs/foo.pdf")
    assert fake.uploaded == []  # idempotent no-op


def test_upload_proceeds_when_key_is_new(monkeypatch, tmp_file):
    fake = _FakeS3Client()
    monkeypatch.setattr(s3_uploader, "_client", lambda: fake)
    s3_uploader.upload_file(tmp_file, "pdfs/new.pdf")
    assert len(fake.uploaded) == 1
    assert fake.uploaded[0][2] == "pdfs/new.pdf"
    assert fake.uploaded[0][3] == {"ContentType": "application/pdf"}


def test_upload_overwrite_true_skips_head_check(monkeypatch, tmp_file):
    fake = _FakeS3Client(existing_keys={"metadata/foo.json"})
    monkeypatch.setattr(s3_uploader, "_client", lambda: fake)
    s3_uploader.upload_file(tmp_file, "metadata/foo.json", overwrite=True)
    assert len(fake.uploaded) == 1  # re-uploaded despite already existing


def test_upload_missing_local_file_raises(monkeypatch):
    fake = _FakeS3Client()
    monkeypatch.setattr(s3_uploader, "_client", lambda: fake)
    with pytest.raises(FileNotFoundError):
        s3_uploader.upload_file("/no/such/file.pdf", "pdfs/x.pdf")


def test_upload_content_type_by_extension(monkeypatch):
    fake = _FakeS3Client()
    monkeypatch.setattr(s3_uploader, "_client", lambda: fake)
    for ext, expected in ((".pdf", "application/pdf"),
                           (".md", "text/markdown; charset=utf-8"),
                           (".json", "application/json")):
        fd, path = tempfile.mkstemp(suffix=ext)
        os.close(fd)
        try:
            fake.existing_keys.clear()
            s3_uploader.upload_file(path, f"artifact/x{ext}")
            assert fake.uploaded[-1][3]["ContentType"] == expected
        finally:
            os.remove(path)


def test_download_state_returns_false_when_absent(monkeypatch, tmp_path):
    fake = _FakeS3Client()  # nothing exists remotely
    monkeypatch.setattr(s3_uploader, "_client", lambda: fake)
    local_path = str(tmp_path / "processed_ids.json")
    found = s3_uploader.download_state_if_exists(local_path)
    assert found is False
    assert not os.path.exists(local_path)


def test_download_state_returns_true_and_writes_file_when_present(monkeypatch, tmp_path):
    fake = _FakeS3Client(existing_keys={"state/processed_ids.json"})
    monkeypatch.setattr(s3_uploader, "_client", lambda: fake)
    local_path = str(tmp_path / "processed_ids.json")
    found = s3_uploader.download_state_if_exists(local_path)
    assert found is True
    assert os.path.exists(local_path)
