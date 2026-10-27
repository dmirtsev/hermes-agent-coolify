"""Build-time only: pinned public tokenizer assets, verified before use."""
import hashlib
import os
from pathlib import Path
import urllib.request

target = Path(os.environ.get("HERMES_TOKENIZER_ASSETS", "/opt/hermes/tokenizers"))
target.mkdir(parents=True, exist_ok=True)
base = "https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/resolve/60d8d70770c6776ff598c94bb586a859a38244f1/"
for remote, local, checksum in [
    ("tokenizer.json", "deepseek-tokenizer.json", "8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf"),
    ("encoding/encoding_dsv4.py", "encoding_dsv4.py", "bdbd57c132a1b3725042323d02b98b9d1df28e5f388f134399555d041f5055e0"),
]:
    data = urllib.request.urlopen(base + remote, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise SystemExit("Tokenizer checksum mismatch: " + local)
    (target / local).write_bytes(data)
import tiktoken
tiktoken.get_encoding("o200k_base")
