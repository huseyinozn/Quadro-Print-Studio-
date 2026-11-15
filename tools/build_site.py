"""Build the static public site (everything runs in the visitor's browser with Pyodide).

python3 tools/build_site.py --pyodide <dir with pyodide.js, pyodide.asm.js, pyodide.asm.wasm,
        python_stdlib.zip, pyodide-lock.json> --numpy <numpy-1.26.4-...wasm32.whl> --out <dir>

The output folder holds index.html (page content, the host adds the document skeleton),
worker.js, py_bundle.json (the quadro_studio Python sources), pyodide/, the numpy wheel and
the public example slices (as .txt). No printer profile, mesh or reference print is included.
"""
import argparse
import json
import os
import re
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "quadro_studio", "web")
PYODIDE_FILES = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]


def theme_css(css):
    """Viewer theme: light on :root, dark under prefers-color-scheme unless data-theme=light, and again
    under data-theme=dark (the host's explicit toggle)."""
    m = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}\s*\}", css, re.S)
    dark = m.group(1)
    css = css.replace(m.group(0), "@media (prefers-color-scheme: dark) {\n  :root:not([data-theme=\"light\"]) {%s color-scheme: dark; }\n}\n"
                      ":root[data-theme=\"dark\"] {%s color-scheme: dark; }" % (dark, dark))
    css = css.replace(".wrap { max-width: 920px; margin: 0 auto; padding: 0 16px; }",
                      ".wrap { max-width: 920px; margin: 0 auto; padding-left: max(16px, env(safe-area-inset-left, 0px)); "
                      "padding-right: max(16px, env(safe-area-inset-right, 0px)); }")
    return css + "\n.boot { color: var(--muted); font-size: .9rem; margin: 6px 0 0; }\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pyodide", required=True)
    ap.add_argument("--numpy", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "pyodide"))
    os.makedirs(os.path.join(out, "examples"))
    # binary parts are published as base64 text (every published file is reviewable text);
    # the worker decodes them in the browser
    import base64
    for f in PYODIDE_FILES:
        src = os.path.join(a.pyodide, f)
        if f.endswith((".wasm", ".zip")):
            with open(src, "rb") as fh, open(os.path.join(out, "pyodide", f + ".b64.txt"), "w") as fo:
                fo.write(base64.b64encode(fh.read()).decode())
        else:
            shutil.copy(src, os.path.join(out, "pyodide", f))
    with open(a.numpy, "rb") as fh, open(os.path.join(out, "pyodide", "numpy-1.26.4.whl.b64.txt"), "w") as fo:
        fo.write(base64.b64encode(fh.read()).decode())
    shutil.copy(os.path.join(ROOT, "site", "worker.js"), os.path.join(out, "worker.js"))
    bundle = {}
    for r, ds, fs in os.walk(os.path.join(ROOT, "quadro_studio")):
        ds[:] = [d for d in ds if d not in ("__pycache__", "static", "examples")]
        for f in sorted(fs):
            if f.endswith(".py"):
                full = os.path.join(r, f)
                bundle[os.path.relpath(full, ROOT).replace(os.sep, "/")] = open(full, encoding="utf-8").read()
    with open(os.path.join(out, "py_bundle.json"), "w") as f:
        json.dump(bundle, f)
    for f in os.listdir(os.path.join(WEB, "examples")):
        shutil.copy(os.path.join(WEB, "examples", f), os.path.join(out, "examples", f + ".txt"))
    for f in os.listdir(os.path.join(WEB, "static", "examples")):
        shutil.copy(os.path.join(WEB, "static", "examples", f), os.path.join(out, "examples", f))
    html = open(os.path.join(WEB, "static", "index.html"), encoding="utf-8").read()
    body = html.split("<body>", 1)[1].split("</body>", 1)[0]
    body = body.replace('<script src="/app.js"></script>', "")
    body = body.replace('src="/examples/', 'src="examples/')
    body = body.replace("Everything runs on this computer; nothing is sent to your printer.",
                        "Everything runs in your browser: your files are not uploaded anywhere and nothing is sent to your printer.")
    body = body.replace('<p class="status" id="plan-status" role="status" aria-live="polite"></p>',
                        '<p class="status" id="plan-status" role="status" aria-live="polite"></p>\n    '
                        '<p class="boot" id="boot">Loading the converter in your browser (about 25 MB, once)…</p>', 1)
    app = open(os.path.join(WEB, "static", "app.js"), encoding="utf-8").read()
    app = app.replace(">Download G-code</a>", ">Download G-code (ZIP)</a>")
    css = theme_css(open(os.path.join(WEB, "static", "style.css"), encoding="utf-8").read())
    page = ("<title>Quadro Print Studio</title>\n"
            '<meta name="claude:viewport-fit-sides" content="cover">\n'
            '<meta name="description" content="Prepare multi-head prints for the Co Print Quadro.">\n'
            "<style>\n%s\n</style>\n%s\n<script>\n%s\n</script>\n<script>\n%s\n</script>\n"
            "<script>\nwindow.QPS_READY.then(() => { const b = document.getElementById('boot'); if (b) b.textContent = 'Converter ready.'; })"
            ".catch((e) => { const b = document.getElementById('boot'); if (b) b.textContent = 'The converter could not start in this browser: ' + e.message; });\n"
            "</script>\n" % (css, body, open(os.path.join(ROOT, "site", "local_api.js"), encoding="utf-8").read(), app))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    total = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(out) for f in fs)
    print("site written to", out, "files", sum(len(fs) for _, _, fs in os.walk(out)), "bytes", total)


if __name__ == "__main__":
    main()
