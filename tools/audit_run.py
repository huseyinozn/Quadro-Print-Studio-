"""Run a quadro_studio CLI command while recording every file it opens.

Usage: python3 tools/audit_run.py AUDIT.json -- <cli args...>
Used by the acceptance run to prove that generation reads only the slice and
the printer profile (never the golden oracle).
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

opened = []


def hook(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        p = os.path.abspath(os.fsdecode(args[0]))
        mode = args[1] if len(args) > 1 else None
        opened.append((p, str(mode)))


def main():
    audit_path = sys.argv[1]
    assert sys.argv[2] == "--"
    from quadro_studio import cli   # import before hooking: only runtime file access is recorded
    sys.addaudithook(hook)
    t0 = time.time()
    rc = cli.main(sys.argv[3:])
    data = [p for p, m in opened if not p.endswith((".py", ".pyc")) and "__pycache__" not in p and "/lib/python" not in p]
    with open(audit_path, "w") as f:
        json.dump(dict(argv=sys.argv[3:], exit_code=rc, elapsed_s=round(time.time() - t0, 2),
                       files_opened=sorted(set(data))), f, indent=1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
