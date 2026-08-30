import io
import json
import zipfile

MAP = {
    "version": 3,
    "file": "app.js",
    "sources": ["src/payments.js"],
    "names": ["charge"],
    "mappings": "AAAAA,SAAS",
    "sourcesContent": ["export function charge(order) {\n  throw new Error('x')\n}\n"],
    "debug_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
}
DEBUG_ID = MAP["debug_id"]


def build(document=None, manifest=None, name="app.js.map"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if manifest is not None:
            archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(name, json.dumps(document or MAP))
    return buffer.getvalue()
