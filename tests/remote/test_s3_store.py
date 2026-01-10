import io
import unittest

from testguard.remote.object_store.s3_store import S3ObjectStore


class FakePaginator:
    def __init__(self, *, client) -> None:
        self._client = client

    def paginate(self, *, Bucket: str, Prefix: str):
        keys = []
        for (bucket, key), _data in self._client._objects.items():
            if bucket != Bucket:
                continue
            if Prefix and not key.startswith(Prefix):
                continue
            keys.append(key)

        yield {"Contents": [{"Key": k} for k in sorted(keys)]}


class FakeS3Client:
    def __init__(self) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}
        self.put_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.paginator_calls: list[str] = []

    def put_object(self, **kwargs):
        self.put_calls.append(dict(kwargs))
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        body = kwargs["Body"]
        assert isinstance(body, (bytes, bytearray))
        self._objects[(bucket, key)] = bytes(body)
        return {}

    def get_object(self, *, Bucket: str, Key: str):
        self.get_calls.append({"Bucket": Bucket, "Key": Key})
        data = self._objects[(Bucket, Key)]
        return {"Body": io.BytesIO(data)}

    def get_paginator(self, name: str):
        self.paginator_calls.append(name)
        assert name == "list_objects_v2"
        return FakePaginator(client=self)


class TestS3ObjectStore(unittest.TestCase):
    def test_put_and_get_with_prefix(self) -> None:
        fake = FakeS3Client()
        store = S3ObjectStore(bucket="b", prefix="testguard/runs", client=fake)

        store.put_bytes(key="run1/run_report.json", data=b"abc", content_type="application/json")
        self.assertEqual(len(fake.put_calls), 1)
        self.assertEqual(fake.put_calls[0]["Bucket"], "b")
        self.assertEqual(fake.put_calls[0]["Key"], "testguard/runs/run1/run_report.json")
        self.assertEqual(fake.put_calls[0]["ContentType"], "application/json")

        data = store.get_bytes(key="run1/run_report.json")
        self.assertEqual(data, b"abc")
        self.assertEqual(fake.get_calls[0]["Key"], "testguard/runs/run1/run_report.json")

    def test_list_keys_returns_store_relative_keys(self) -> None:
        fake = FakeS3Client()
        store = S3ObjectStore(bucket="b", prefix="testguard", client=fake)

        store.put_bytes(key="runs/r1/a.txt", data=b"1")
        store.put_bytes(key="runs/r2/b.txt", data=b"2")
        store.put_bytes(key="other/x.txt", data=b"3")

        keys = list(store.list_keys(prefix="runs/"))
        self.assertEqual(keys, ["runs/r1/a.txt", "runs/r2/b.txt"])

    def test_list_keys_works_with_empty_prefix(self) -> None:
        fake = FakeS3Client()
        store = S3ObjectStore(bucket="b", prefix="", client=fake)

        store.put_bytes(key="a.txt", data=b"1")
        store.put_bytes(key="dir/b.txt", data=b"2")

        keys = list(store.list_keys(prefix="dir/"))
        self.assertEqual(keys, ["dir/b.txt"])


if __name__ == "__main__":
    unittest.main()
