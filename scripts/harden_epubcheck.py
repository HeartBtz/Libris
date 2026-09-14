"""Apply pinned security updates to the upstream EPUBCheck 5.3.0 distribution.

Keep launcher-compatible filenames; the JAR metadata records the updated versions.
No runtime downloads are performed. Maven artifacts are verified at image build time.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

UPDATES = (
    ("jackson-core-2.18.2.jar", "com/fasterxml/jackson/core/jackson-core/2.18.8/jackson-core-2.18.8.jar",
     "ab865e39f01403598748b090a3bf528616e701913a71afc37a227daa0fabb4ae"),
    ("jackson-databind-2.18.2.jar", "com/fasterxml/jackson/core/jackson-databind/2.18.8/jackson-databind-2.18.8.jar",
     "06ea5950905263fac3e1730de6a21a023151626af6bb3cb099f88644a0fa04f0"),
    ("httpcore5-5.1.3.jar", "org/apache/httpcomponents/core5/httpcore5/5.4.3/httpcore5-5.4.3.jar",
     "18bfbbabb478dfb67f31aeaf428c387f3c3df654582e1309f708ee1f3086830a"),
    ("httpcore5-h2-5.1.3.jar", "org/apache/httpcomponents/core5/httpcore5-h2/5.4.3/httpcore5-h2-5.4.3.jar",
     "c7db7026b8e2dea39132b04a6069f6671e2858309b20a146ec5c7dd6ed73a0b6"),
)


def main():
    root = Path(sys.argv[1]) / "lib"
    downloads = []
    for filename, artifact, expected in UPDATES:
        target = root / filename
        if not target.is_file():
            raise RuntimeError(f"Unexpected EPUBCheck layout: {filename}")
        with urllib.request.urlopen("https://repo.maven.apache.org/maven2/" + artifact, timeout=60) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != expected:
            raise RuntimeError(f"Checksum mismatch: {artifact}")
        downloads.append((target, content))
    for target, content in downloads:
        target.write_bytes(content)
    print("Verified and updated four EPUBCheck dependency artifacts.")


if __name__ == "__main__":
    main()
