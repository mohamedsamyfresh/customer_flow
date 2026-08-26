import base64
import hashlib
import os
import zipfile


def get_record_line(zip_path, data):
    digest = hashlib.sha256(data).digest()
    hash_str = f"sha256={base64.urlsafe_b64encode(digest).decode().rstrip('=')}"
    return f"{zip_path},{hash_str},{len(data)}"

def build():
    src_dir = "/mnt/c/Users/mohamed.mohamed/freshfamily-auth/src/freshfamily_auth"
    out_dir = "/mnt/c/Users/mohamed.mohamed/customer-flow-backend/packages"
    os.makedirs(out_dir, exist_ok=True)
    whl_path = os.path.join(out_dir, "freshfamily_auth-0.1.0-py3-none-any.whl")

    metadata = """Metadata-Version: 2.1
Name: freshfamily-auth
Version: 0.1.0
Summary: Shared JWT/JWKS authentication library for Fresh Family microservices
License: MIT
Requires-Python: >=3.10
Requires-Dist: pyjwt[crypto]>=2.8.0
Requires-Dist: cryptography>=42.0.0
Requires-Dist: httpx>=0.27.0
Provides-Extra: fastapi
Requires-Dist: fastapi>=0.110.0; extra == 'fastapi'
Provides-Extra: test
Requires-Dist: pytest>=8.0.0; extra == 'test'
Requires-Dist: pytest-asyncio>=0.23.0; extra == 'test'
Requires-Dist: httpx>=0.27.0; extra == 'test'
"""

    wheel = """Wheel-Version: 1.0
Generator: custom_builder
Root-Is-Purelib: true
Tag: py3-none-any
"""

    record_lines = []

    with zipfile.ZipFile(whl_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src_dir):
            for file in files:
                if file.endswith(".pyc") or "__pycache__" in root:
                    continue
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, "/mnt/c/Users/mohamed.mohamed/freshfamily-auth/src")
                with open(full_path, "rb") as f:
                    content = f.read()
                zf.writestr(rel_path, content)
                record_lines.append(get_record_line(rel_path, content))

        # Add dist-info files
        meta_bytes = metadata.encode("utf-8")
        zf.writestr("freshfamily_auth-0.1.0.dist-info/METADATA", meta_bytes)
        record_lines.append(get_record_line("freshfamily_auth-0.1.0.dist-info/METADATA", meta_bytes))

        wheel_bytes = wheel.encode("utf-8")
        zf.writestr("freshfamily_auth-0.1.0.dist-info/WHEEL", wheel_bytes)
        record_lines.append(get_record_line("freshfamily_auth-0.1.0.dist-info/WHEEL", wheel_bytes))

        # Add RECORD without hash
        record_lines.append("freshfamily_auth-0.1.0.dist-info/RECORD,,")
        record_content = "\n".join(record_lines) + "\n"
        zf.writestr("freshfamily_auth-0.1.0.dist-info/RECORD", record_content.encode("utf-8"))

    print(f"Successfully built {whl_path}")

if __name__ == "__main__":
    build()
