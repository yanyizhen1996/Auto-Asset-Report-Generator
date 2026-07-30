# CIAR2 – Building Damage Assessment Toolkit

A three-step Python workflow that turns Oasys **XDISP** ground-movement models into
per-building damage-assessment PDF reports.

The pipeline:

1. **Step 1 – Export Results** → run every XDISP model and export the raw results tables to CSV.
2. **Step 2 – Process Output File** → clean the raw CSVs and append derived strain / damage-category columns.
3. **Step 3 – Generate Reports** → build a per-building PDF damage-assessment report with tables and charts.

Each script opens a small Tkinter window so you can pick the input/output folders. Defaults are resolved next to the scripts, so you can usually just click through.

---

## Core inputs

Only **two** things are required to drive the whole tool:

| Input | Description |
| --- | --- |
| **`Building Inputs Summary.csv`** | One summary file describing every wall line: `Building Name`, `Wall Line`, `Length (m)`, `Poisson's Ratio`, `E/G`, `Height (m)`, `Base elevation (m)`. Used in Steps 2 and 3 to attach geometry to each result row. |
| **XDISP analysis files** | The XDISP models to run, one file per construction phase, in `.json` or `.xdd` format (e.g. the files in `XDISP File/`). Consumed by Step 1. |

Everything else (the `data/` and `lines/` CSVs, the cleaned outputs, the PDFs) is generated automatically by the scripts.

---

## Inputs / outputs per step

### Step 1 — `Step_1 Export_Results.py`
- **Input:** a folder of XDISP model files (`.json` / `.xdd`).
- **Output:** `XDISP Output/` containing:
  - `data/` – building-damage detail tables (one CSV per model).
  - `lines/` – displacement-line tables (one CSV per model).
- **Requires XDISP installed** (uses the XDISP COM API `XDispAuto_20_2.ComAuto`).

### Step 2 — `Step_2 Process Output File.py`
- **Inputs:**
  - `XDISP Output/` (the `data/` + `lines/` folders from Step 1).
  - `Building Inputs Summary.csv` (wall geometry).
- **Output:** `Processed XDISP Output/` with the same `data/` + `lines/` layout, where the `data/` tables are cleaned (re-encoded to UTF-8, banner/marker lines stripped) and gain derived columns:
  - Max Bending / Diagonal / Ave. Horizontal Strain, Angular Distortion, Epsilon Critical Max.
  - Damage categories (Boscardin & Cording 1989; Son & Cording 2005 – critical strain and modified curves).

### Step 3 — `Step_3 Generate Reports.py`
- **Inputs:**
  - `Processed XDISP Output/data/*.csv` and `Processed XDISP Output/lines/*.csv` (from Step 2).
  - `Building Inputs Summary.csv` (wall geometry).
- **Output:** `Report/` – one PDF per building, each with a header band, per-wall result tables, and damage-interaction / displacement charts.

---

## Dependencies

### Python
- **Python 3.8+** (Windows).

### Third-party packages
Install with pip:

```powershell
pip install pandas numpy matplotlib reportlab comtypes pywin32
```

| Package | Used by |
| --- | --- |
| `pandas`, `numpy` | Steps 2 and 3 |
| `matplotlib` | Step 3 (charts) |
| `reportlab` | Step 3 (PDF generation) |
| `comtypes`, `pywin32` | Step 1 (XDISP COM automation) |

`tkinter` ships with the standard Windows Python installer, so no separate install is needed.

### External software
- **Oasys XDISP** must be installed and licensed to run **Step 1** – it drives the application through its COM API (`XDispAuto_20_2.ComAuto`). Steps 2 and 3 do **not** need XDISP and can run on any machine.
- **Arial font** (standard on Windows) is used in the Step 3 PDFs; it falls back to Helvetica / DejaVu Sans if unavailable.

---

## Python environment (optional)

A virtual environment is **recommended but not required**. The scripts only use
`import` statements, so they run fine against a **global Python installation** as long
as the packages above are available on that interpreter.

**Option A – global Python (simplest):**

```powershell
pip install pandas numpy matplotlib reportlab comtypes pywin32
```

**Option B – isolated virtual environment (keeps dependencies self-contained):**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install pandas numpy matplotlib reportlab comtypes pywin32
```

---

## Running the tool

Run the steps in order. Each opens folder-picker dialogs; accept the defaults or point them at your own folders.

```powershell
python ".\Step_1 Export_Results.py"
python ".\Step_2 Process Output File.py"
python ".\Step_3 Generate Reports.py"
```

> **Note:** Step 1 must run on a machine with XDISP installed. Once the CSVs exist, Steps 2 and 3 can be run anywhere.
