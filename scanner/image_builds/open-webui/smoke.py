"""Offline dependency compatibility; no models, accounts or persistent state."""
import io
import importlib.metadata
import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
import fsspec
from datasets import Dataset
from mcp import ClientSession
import pyarrow as pa
from pypdf import PdfReader, PdfWriter
import sentence_transformers
import transformers
from unstructured.partition.text import partition_text

test_key = "compatibility-check-only-never-a-production-key"
token = jwt.encode({"sub": "compatibility-check"}, test_key, algorithm="HS256")
assert jwt.decode(token, test_key, algorithms=["HS256"])["sub"] == "compatibility-check"
assert rsa.generate_private_key(public_exponent=65537, key_size=2048).key_size == 2048
assert fsspec.filesystem("memory").protocol == "memory"
assert pa.table({"value": [1]}).num_rows == 1
assert Dataset.from_dict({"value": [1]})[0]["value"] == 1
writer = PdfWriter()
writer.add_blank_page(width=72, height=72)
stream = io.BytesIO()
writer.write(stream)
stream.seek(0)
assert len(PdfReader(stream).pages) == 1
# Import its parser API without downloading NLTK/model resources in a build.
assert callable(partition_text)
for name in ("PyJWT", "cryptography", "mcp", "pyarrow", "pypdf", "sentence-transformers", "transformers", "unstructured"):
    print(name, importlib.metadata.version(name))
