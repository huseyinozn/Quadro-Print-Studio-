"""Test-only: run the web app with a fault injected AFTER generation and BEFORE
verification (one model-move acceleration command removed). Used to prove that a
file failing the independent verification can never be downloaded.

python3 tools/serve_with_fault.py PORT WORKDIR
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quadro_studio.web.server import make_server


def drop_one_acceleration(path):
    with open(path) as f:
        lines = f.read().split("\n")
    i = next(k for k, l in enumerate(lines) if l.startswith("; FARM_PART layer=3 "))
    j = next(k for k in range(i, len(lines)) if lines[k].startswith("SET_VELOCITY_LIMIT"))   # block-start model acceleration
    del lines[j]
    with open(path, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    srv, app = make_server("127.0.0.1", int(sys.argv[1]), sys.argv[2], post_generate_hook=drop_one_acceleration)
    print("fault-injecting test server on", srv.server_address, flush=True)
    srv.serve_forever()
