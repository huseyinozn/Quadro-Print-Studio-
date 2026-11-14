"""Assemble review_bundle.zip from the source tree and an acceptance-run folder.

python3 tools/build_bundle.py --acceptance OUT_DIR --zip review_bundle.zip [--preview-png PNG]

Private inputs (slice, printer profile, golden oracle and profiles derived
from them) are never added.
"""
import argparse
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = ["README.md", "REPORT.md", "CHECKS.md", ".gitignore"]
SRC_DIRS = ["quadro_studio", "tests", "tools"]
RESULTS = ["validation.json", "acceptance_run.json", "plan.json", "inspect.json", "generate.json",
           "generation_audit.json", "unit_tests.log", "preview.svg"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--acceptance", required=True)
    ap.add_argument("--zip", required=True)
    ap.add_argument("--preview-png", default=None)
    a = ap.parse_args()
    acc = os.path.abspath(a.acceptance)
    with zipfile.ZipFile(a.zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in SRC:
            z.write(os.path.join(ROOT, f), "review_bundle/" + f)
        for d in SRC_DIRS:
            for root, dirs, files in os.walk(os.path.join(ROOT, d)):
                dirs[:] = [x for x in dirs if x != "__pycache__"]
                for f in sorted(files):
                    if f.endswith((".py", ".md", ".txt")):
                        full = os.path.join(root, f)
                        z.write(full, "review_bundle/" + os.path.relpath(full, ROOT))
        for f in RESULTS + ["historical_e_check.json"]:
            if not os.path.exists(os.path.join(acc, "results", f)):
                continue
            z.write(os.path.join(acc, "results", f), "review_bundle/results/" + f)
        if a.preview_png:
            z.write(a.preview_png, "review_bundle/results/preview.png")
        cand_dir = os.path.join(acc, "candidate")
        for f in os.listdir(cand_dir):
            z.write(os.path.join(cand_dir, f), "review_bundle/candidate/" + f)
    print("wrote", a.zip, os.path.getsize(a.zip), "bytes")


if __name__ == "__main__":
    main()
