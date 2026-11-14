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

## Update: example prints and mesh marks (review 03)

| Check | Result |
|---|---|
| Mesh marks come 1:1 from the planner's affected-copy list | planner: 0/8, 0/9, 1/8, 1/9, 2/8, 2/9, 3/8, 3/9; screen: 0/8, 0/9, 1/8, 1/9, 2/8, 2/9, 3/8, 3/9; equal: True |
| Tap on a copy shows the two calculated effects separately | e.g. Copy 1.9: first layer up to 0.000 mm, common height at the end of layer 5 up to 0.096 mm; no error wording: True |
| Clean mesh (test-only copy of the profile with that point set to its neighbour median, never used for a print) | planner list empty, marks on screen: 0, equal: True |
| Phone flow: choose "40 Sharks" → upload only the current profile → 40-copy layout → generate and verify → download | PASS — count prefilled 40, model input shown: False, file choosers for a model: 0, heading "Software checks passed", downloaded SHA-256 equals the verified hash: True |
| G-code unchanged by this update | downloaded SHA-256 42e44d7f20a4fe8dad44a6af18b93b66451fd52d22be2665ca9ea228c0b1b717, identical to the previous web run |
| OrcaCube (Copy Mode), Two-Color Fidgets (Dual Color Copy Mode) | shown with their real sliced example images; "Coming soon", not selectable; the API refuses them (HTTP 409) |
| Automated tests | 83 tests OK (13 web tests incl. example-only plan, coming-soon refusal, marks == planner list, clean mesh → no marks) |

The internal reference file from the example archive was not added to the project, the site or the repository.
