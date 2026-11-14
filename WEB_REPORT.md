# Web app (Farm Mode) — test results

Browser: Chromium via Playwright, iPhone 13 viewport. Inputs: the private shark slice and profile (not included). Server: `python3 -m quadro_studio.web`.

| Flow | Result |
|---|---|
| Product page | modes shown: Farm Mode Available; Copy Mode Coming soon; Dual Color Copy Mode Coming soon |
| Success: upload → layout → generate → independent verify → download (40 copies) | PASS — heading "Software checks passed"; download links before verification: 0; after: 1; downloaded 48115511 bytes, SHA-256 equals the verified hash: True |
| Preview | phases: All copies, Layers 1–5 · Head 1, Layers 1–5 · Head 2, Layers 1–5 · Head 3, Layers 1–5 · Head 4, Layers 6–83 · all heads; 42 shapes drawn (40 copies + 2 hidden-phase overlays), 1 marked mesh point |
| Changed input after success | old result hidden: True; download links left: 0 |
| Verification failure (fault injected after generation: one model acceleration removed) | PASS — heading "Verification failed — download blocked"; download links: 0; direct download URL: HTTP 403; unverified file deleted from the job folder |
| Reasons shown | An acceleration setting differs from the sliced model. — T0 layer 3: acceleration differs from slice at slicer line 3251 (slice (4000.0, 2000.0), output (7000.0, 3500.0)) / Some model moves of a copy were missing or changed. — 1 part visits do not reproduce the slice |
| Count that does not fit (44) | PASS — "That many copies of this model do not fit on the measured bed area."; alternative offered: Use 40 copies (full layout checks passed), layout shown after one tap: True |

Automated tests: `QUADRO_FIXTURES=<fixtures> python3 -m unittest discover -s tests` → 78 tests OK (8 web tests incl. success download hash, 403 before verification, 403 after failed verification, new job on changed inputs, model-size-driven spacing, verified alternative count).

Notes: the 40-copy layout in the web app is computed from the model size (auto): 2 columns per head, head spacing 66.0 mm, smallest gap 3.24 mm. It differs from the earlier reference grid (72/35/58 mm), which remains available in the CLI as `--preset reference-300`. Software checks passed is a statement about the generated file, not about a print on hardware.
