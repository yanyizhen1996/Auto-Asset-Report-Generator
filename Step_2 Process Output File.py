# -*- coding: utf-8 -*-

"""
Step 2 - Process Output Files
=============================

Cleans up the raw XDISP results CSVs produced by "Step_1 Export Results" and,
for the building-damage ("data") tables, appends a set of derived strain columns
used for damage-category assessment.

What this script fixes in every raw CSV
---------------------------------------
* Removes the redundant banner lines above the table (Oasys header, file name,
  export timestamp, XDisp version, table title, START_TABLE marker).
* Removes the awkward blank line that sits between the header and the first data
  row.
* Removes the trailing END_TABLE marker and the explanatory note line(s).
* Re-writes the file as proper UTF-8 (the raw files are cp1252-encoded, which is
  why characters such as the degree sign appear corrupted).

Extra columns added to the "data" tables only
----------------------------------------------
Using the wall geometry from "Building Inputs Summary.csv" (matched on the
sub-building / wall-line name) together with the Deflection Ratio and Average
Horizontal Strain from each row, the following derived columns are appended:

    "Max Bending Strain (1e-3)"      -> eps_b_max
    "Max Diagonal Strain (1e-3)"     -> eps_d_max
    "Ave. Horizontal Strain (1e-3)"  -> eps_h
    "Angular Distortion (1e-3)"      -> beta
    "Epsilon Critical Max (1e-3)"    -> eps_crit_max

From those derived values three empirical damage categories are then classified
(logic ported from the Cording XDisp plug-in / DamageClasses.py):

    "Damage Category (Boscardin and Cording, 1989)"
    "Damage Category (Son and Cording, 2005) Based on Critical Strain"
    "Damage Category (Son and Cording, 2005) Modified Curves"

Cleaned files are written to a "cleaned_output" folder (with the same
data/ and lines/ sub-folder layout), created automatically if missing.
"""

import os
import csv
import time
import traceback

import numpy as np
import pandas as pd

import tkinter as tk
from tkinter import filedialog, messagebox

# =============================================================================
# 1. Configuration
# =============================================================================

# Folders / files are resolved relative to this script by default.
DEFAULT_INPUT_DIRNAME = "XDISP Output"   # produced by Step 1
DEFAULT_OUTPUT_DIRNAME = "Processed XDISP Output"          # created by this script
DEFAULT_SUMMARY_FILENAME = "Building Inputs Summary.csv"

# The two sub-folders written by Step 1.
DATA_SUBDIR = "data"     # building-damage detail tables (get derived columns)
LINES_SUBDIR = "lines"   # displacement-line tables (cleaned only)

# Encoding of the raw XDISP CSV exports (they are not UTF-8).
RAW_ENCODING = "cp1252"

# Markers used to locate the real table inside the raw export.
START_MARKER = "START_TABLE"
END_MARKER = "END_TABLE"

# Column names inside the raw "data" table that feed the calculations.
COL_SUBBUILDING = "Sub-building Name"          # matches summary "Wall Line"
COL_DEFLECTION_RATIO = "Deflection Ratio"      # dr, in %
COL_AVG_H_STRAIN = "Average Horizontal Strain" # eps_h_prcnt, in %

# Column names inside "Building Inputs Summary.csv".
SUM_WALL_LINE = "Wall Line"
SUM_LENGTH = "Length (m)"
SUM_HEIGHT = "Height (m)"
SUM_EOVERG = "E/G"

# Rename map applied to the cleaned header (original name -> new name).
HEADER_RENAMES = {
    "Damage Category": "Damage Category (Burland 1995)",
}

# New columns appended to the cleaned "data" tables (header -> variable).
NEW_COLUMNS = [
    "Max Bending Strain (1e-3)",
    "Max Diagonal Strain (1e-3)",
    "Ave. Horizontal Strain (1e-3)",
    "Angular Distortion (1e-3)",
    "Epsilon Critical Max (1e-3)",
    "Damage Category (Boscardin and Cording, 1989)",
    "Damage Category (Son and Cording, 2005) Based on Critical Strain",
    "Damage Category (Son and Cording, 2005) Modified Curves",
]


def script_dir():
    """Return the directory that contains this script (fallback: cwd)."""
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


# =============================================================================
# 2. Building geometry lookup
# =============================================================================

def load_building_lookup(summary_path):
    """
    Read "Building Inputs Summary.csv" and return a dict keyed by wall-line name:

        {wall_line_name: (length_m, height_m, e_over_g)}

    The wall-line name matches the "Sub-building Name" column in the data tables.
    """
    summary = pd.read_csv(summary_path)

    lookup = {}
    for _, row in summary.iterrows():
        wall_line = str(row[SUM_WALL_LINE]).strip()
        lookup[wall_line] = (
            float(row[SUM_LENGTH]),
            float(row[SUM_HEIGHT]),
            float(row[SUM_EOVERG]),
        )
    return lookup


# =============================================================================
# 3. Raw CSV parsing (strip the banner / footer, isolate the table)
# =============================================================================

def parse_raw_table(path):
    """
    Read one raw XDISP export and return (header_cols, units_cols, data_rows).

    * header_cols : list of column names (the row after START_TABLE)
    * units_cols  : list of unit labels (the row after the header), padded /
                    trimmed to the same width as header_cols
    * data_rows   : list of data rows (each already trimmed to header width),
                    with the blank gap, END_TABLE marker and note lines removed
    """
    # Raw exports are cp1252; read them explicitly so special characters survive.
    with open(path, "r", encoding=RAW_ENCODING) as f:
        lines = f.read().splitlines()

    # Locate the table: header is the line straight after START_TABLE.
    try:
        start_idx = next(
            i for i, line in enumerate(lines) if line.strip() == START_MARKER
        )
    except StopIteration:
        raise ValueError(f"No '{START_MARKER}' marker found in {path}")

    header_line = lines[start_idx + 1]
    units_line = lines[start_idx + 2]

    # Locate END_TABLE (everything after it - notes etc. - is discarded).
    end_idx = len(lines)
    for i in range(start_idx + 3, len(lines)):
        if lines[i].strip() == END_MARKER:
            end_idx = i
            break

    # Parse the header / units rows through the csv module (handles quoting).
    header_cols = next(csv.reader([header_line]))
    n_cols = len(header_cols)

    units_cols = next(csv.reader([units_line])) if units_line.strip() else []
    units_cols = _fit_width(units_cols, n_cols)

    # Collect the data rows, dropping the awkward blank gap line(s).
    data_rows = []
    for raw in lines[start_idx + 3:end_idx]:
        if raw.strip() == "":
            continue
        row = next(csv.reader([raw]))
        data_rows.append(_fit_width(row, n_cols))

    return header_cols, units_cols, data_rows


def _fit_width(row, n_cols):
    """Trim or pad a row so it has exactly n_cols fields."""
    if len(row) >= n_cols:
        return row[:n_cols]
    return row + [""] * (n_cols - len(row))


# =============================================================================
# 4. Derived strain calculations (data tables only)
# =============================================================================

def _fmt(value):
    """Format a computed float for CSV output ('' for missing values)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    return f"{value:.5g}"


# --- Empirical damage-category classifiers (ported from DamageClasses.py) -----
# All inputs are expressed in units of 1e-3 (beta, eps_h, critical strain).

def classify_boscardin_cording_1989(beta, epsilon_h):
    """Boscardin and Cording (1989) damage category from beta and eps_h."""
    if beta >= 7:
        return "Severe to Very Severe Damage"
    elif 3.47 <= beta < 7:
        if epsilon_h >= (-0.0629 * beta ** 2 + 0.00008 * beta + 3.0236):
            return "Severe to Very Severe Damage"
        else:
            return "Moderate to Severe Damage"
    elif 1.68 <= beta < 3.47:
        if epsilon_h >= (-0.0629 * beta ** 2 + 0.00008 * beta + 3.0236):
            return "Severe to Very Severe Damage"
        elif epsilon_h >= (-0.1293 * beta ** 2 + 0.0052 * beta + 1.544):
            return "Moderate to Severe Damage"
        else:
            return "Slight Damage"
    elif 1.15 <= beta < 1.68:
        if epsilon_h >= (-0.0629 * beta ** 2 + 0.00008 * beta + 3.0236):
            return "Severe to Very Severe Damage"
        elif epsilon_h >= (-0.1293 * beta ** 2 + 0.0052 * beta + 1.544):
            return "Moderate to Severe Damage"
        elif epsilon_h >= (-0.2697 * beta ** 2 + 0.023 * beta + 0.7329):
            return "Slight Damage"
        else:
            return "Very Slight Damage"
    elif beta < 1.15:
        if epsilon_h >= (-0.0629 * beta ** 2 + 0.00008 * beta + 3.0236):
            return "Severe to Very Severe Damage"
        elif epsilon_h >= (-0.1293 * beta ** 2 + 0.0052 * beta + 1.544):
            return "Moderate to Severe Damage"
        elif epsilon_h >= (-0.2697 * beta ** 2 + 0.023 * beta + 0.7329):
            return "Slight Damage"
        elif epsilon_h >= (-0.3196 * beta ** 2 + 0.0345 * beta + 0.471):
            return "Very Slight Damage"
        else:
            return "Negligible Damage"
    return ""


def classify_son_cording_2005_critical(critical_max):
    """Son and Cording (2005) damage category based on critical strain."""
    if critical_max > 3.33:
        return "Severe to Very Severe Damage"
    elif 1.67 < critical_max <= 3.33:
        return "Moderate to Severe Damage"
    elif 0.75 < critical_max <= 1.67:
        return "Slight Damage"
    elif 0.5 < critical_max <= 0.75:
        return "Very Slight Damage"
    else:
        return "Negligible Damage"


def classify_son_cording_2005_modified(beta, epsilon_h):
    """Son and Cording (2005) damage category from the modified curves."""
    if beta >= 6.65:
        return "Severe to Very Severe Damage"
    elif beta >= 3.31:
        if epsilon_h >= (-0.0757 * beta ** 2 + 0.0022 * beta + 3.333):
            return "Severe to Very Severe Damage"
        else:
            return "Moderate to Severe Damage"
    elif beta >= 1.47:
        if epsilon_h >= (-0.0757 * beta ** 2 + 0.0022 * beta + 3.333):
            return "Severe to Very Severe Damage"
        elif epsilon_h >= (-0.1499 * beta ** 2 - 0.0064 * beta + 1.6709):
            return "Moderate to Severe Damage"
        else:
            return "Slight Damage"
    elif beta >= 0.97:
        if epsilon_h >= (-0.0757 * beta ** 2 + 0.0022 * beta + 3.333):
            return "Severe to Very Severe Damage"
        elif epsilon_h >= (-0.1499 * beta ** 2 - 0.0064 * beta + 1.6709):
            return "Moderate to Severe Damage"
        elif epsilon_h >= (-0.3414 * beta ** 2 - 0.0018 * beta + 0.7464):
            return "Slight Damage"
        else:
            return "Very Slight Damage"
    else:
        if epsilon_h >= (-0.0757 * beta ** 2 + 0.0022 * beta + 3.333):
            return "Severe to Very Severe Damage"
        elif epsilon_h >= (-0.1499 * beta ** 2 - 0.0064 * beta + 1.6709):
            return "Moderate to Severe Damage"
        elif epsilon_h >= (-0.3414 * beta ** 2 - 0.0018 * beta + 0.7464):
            return "Slight Damage"
        elif epsilon_h >= (-0.527 * beta ** 2 + 0.0116 * beta + 0.4925):
            return "Very Slight Damage"
        else:
            return "Negligible Damage"


def compute_strain_columns(header_cols, data_rows, building_lookup):
    """
    Compute the derived strain columns and damage categories for a "data" table.

    Returns a list (one entry per data row) of string lists (matching the order
    of NEW_COLUMNS), ready to be appended to each cleaned row. Rows whose
    sub-building is not found in the building summary receive blank values.
    """
    # Work on a DataFrame so the maths can be vectorised and NaN-safe.
    df = pd.DataFrame(data_rows, columns=header_cols)

    # Inputs coming from each individual data row (percent values).
    dr = pd.to_numeric(df[COL_DEFLECTION_RATIO], errors="coerce")
    eps_h_prcnt = pd.to_numeric(df[COL_AVG_H_STRAIN], errors="coerce")

    # Wall geometry, looked up per row from the building summary.
    geom = df[COL_SUBBUILDING].map(
        lambda name: building_lookup.get(str(name).strip(), (np.nan, np.nan, np.nan))
    )
    wall_length = geom.map(lambda t: t[0]).astype(float)
    wall_height = geom.map(lambda t: t[1]).astype(float)
    e_over_g = geom.map(lambda t: t[2]).astype(float)

    # --- Damage-assessment formulas (results expressed in units of 1e-3) ---
    eps_b_max = (dr / 100) / (
        0.083 * (wall_length / wall_height) + 1.3 * (wall_height / wall_length)
    ) * 1000

    eps_d_max = (dr / 100) / (
        0.064 * (wall_length ** 2 / wall_height ** 2) + 1
    ) * 1000

    eps_h = (eps_h_prcnt / 100) * 1000

    beta = 3 * (dr / 100) * (
        (1 + 4 * e_over_g * (wall_height ** 2) / (wall_length ** 2)) /
        (1 + 6 * e_over_g * (wall_height ** 2) / (wall_length ** 2))
    ) * 1000

    eps_crit_max = (eps_b_max / 1000 + eps_h_prcnt / 100) * 1000

    # Assemble one formatted row per data row (order matches NEW_COLUMNS).
    extra_rows = []
    for i in range(len(df)):
        beta_i = beta.iloc[i]
        eps_h_i = eps_h.iloc[i]
        eps_crit_i = eps_crit_max.iloc[i]

        # Damage categories require valid inputs; leave blank otherwise.
        if pd.isna(beta_i) or pd.isna(eps_h_i):
            bc89 = smod = ""
        else:
            bc89 = classify_boscardin_cording_1989(beta_i, eps_h_i)
            smod = classify_son_cording_2005_modified(beta_i, eps_h_i)
        scrit = "" if pd.isna(eps_crit_i) else classify_son_cording_2005_critical(eps_crit_i)

        extra_rows.append([
            _fmt(eps_b_max.iloc[i]),
            _fmt(eps_d_max.iloc[i]),
            _fmt(eps_h_i),
            _fmt(beta_i),
            _fmt(eps_crit_i),
            bc89,
            scrit,
            smod,
        ])
    return extra_rows


# =============================================================================
# 5. Writing the cleaned CSV (proper UTF-8)
# =============================================================================

def merge_header_units(header_cols, units_cols):
    """
    Combine the header row and the units row into a single header row.

    Each column name gets its unit appended (e.g. "Deflection Ratio [%]").
    Columns without a unit keep their name unchanged. A few columns are also
    renamed via HEADER_RENAMES (e.g. "Damage Category" -> Burland 1995).
    """
    merged = []
    for i, name in enumerate(header_cols):
        name = HEADER_RENAMES.get(name, name)
        unit = units_cols[i].strip() if i < len(units_cols) else ""
        merged.append(f"{name} {unit}" if unit else name)
    return merged


def write_clean_csv(out_path, header, rows):
    """Write a cleaned table (single header row + data rows) as UTF-8."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


# =============================================================================
# 6. Per-folder processing
# =============================================================================

def process_data_folder(in_dir, out_dir, building_lookup):
    """Clean every 'data' CSV and append the derived strain columns."""
    files = [f for f in os.listdir(in_dir) if f.lower().endswith(".csv")]
    print(f"[data] Processing {len(files)} file(s)...")

    for filename in files:
        in_path = os.path.join(in_dir, filename)
        out_path = os.path.join(out_dir, filename)
        try:
            header_cols, units_cols, data_rows = parse_raw_table(in_path)

            # Compute and append the five derived columns.
            extra_rows = compute_strain_columns(header_cols, data_rows, building_lookup)
            header = merge_header_units(header_cols, units_cols) + NEW_COLUMNS
            rows = [base + extra for base, extra in zip(data_rows, extra_rows)]

            write_clean_csv(out_path, header, rows)
            print(f"  cleaned: {filename}")
        except Exception:
            print(f"  ERROR on {filename}")
            traceback.print_exc()


def process_lines_folder(in_dir, out_dir):
    """Clean every 'lines' CSV (no extra columns added)."""
    files = [f for f in os.listdir(in_dir) if f.lower().endswith(".csv")]
    print(f"[lines] Processing {len(files)} file(s)...")

    for filename in files:
        in_path = os.path.join(in_dir, filename)
        out_path = os.path.join(out_dir, filename)
        try:
            header_cols, units_cols, data_rows = parse_raw_table(in_path)
            header = merge_header_units(header_cols, units_cols)
            write_clean_csv(out_path, header, data_rows)
            print(f"  cleaned: {filename}")
        except Exception:
            print(f"  ERROR on {filename}")
            traceback.print_exc()


def process_all(input_dir, output_dir, summary_path):
    """Run the full clean-up over both the data and lines sub-folders."""
    building_lookup = load_building_lookup(summary_path)

    data_in = os.path.join(input_dir, DATA_SUBDIR)
    lines_in = os.path.join(input_dir, LINES_SUBDIR)
    data_out = os.path.join(output_dir, DATA_SUBDIR)
    lines_out = os.path.join(output_dir, LINES_SUBDIR)

    if os.path.isdir(data_in):
        process_data_folder(data_in, data_out, building_lookup)
    else:
        print(f"[data] Sub-folder not found, skipping: {data_in}")

    if os.path.isdir(lines_in):
        process_lines_folder(lines_in, lines_out)
    else:
        print(f"[lines] Sub-folder not found, skipping: {lines_in}")


# =============================================================================
# 7. Simple GUI (confirm folders / summary file, then run)
# =============================================================================

class ProcessorGUI:
    """Window to confirm the input folder, summary file and output folder."""

    def __init__(self, root):
        self.root = root
        self.root.title("Step 2 - Process Output Files")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        base = script_dir()
        self.input_var = tk.StringVar(value=os.path.join(base, DEFAULT_INPUT_DIRNAME))
        self.summary_var = tk.StringVar(value=os.path.join(base, DEFAULT_SUMMARY_FILENAME))
        self.output_var = tk.StringVar(value=os.path.join(base, DEFAULT_OUTPUT_DIRNAME))

        pad = {"padx": 8, "pady": 6}

        # --- Input folder (Export_Results_Output) ---
        tk.Label(root, text="Input folder (Step 1 output):").grid(
            row=0, column=0, columnspan=2, sticky="w", **pad)
        tk.Entry(root, textvariable=self.input_var, width=64).grid(
            row=1, column=0, sticky="we", **pad)
        tk.Button(root, text="Browse...", command=self._browse_input).grid(
            row=1, column=1, **pad)

        # --- Building Inputs Summary.csv ---
        tk.Label(root, text="Building Inputs Summary.csv:").grid(
            row=2, column=0, columnspan=2, sticky="w", **pad)
        tk.Entry(root, textvariable=self.summary_var, width=64).grid(
            row=3, column=0, sticky="we", **pad)
        tk.Button(root, text="Browse...", command=self._browse_summary).grid(
            row=3, column=1, **pad)

        # --- Output folder (cleaned_output) ---
        tk.Label(root, text="Output folder (cleaned results):").grid(
            row=4, column=0, columnspan=2, sticky="w", **pad)
        tk.Entry(root, textvariable=self.output_var, width=64).grid(
            row=5, column=0, sticky="we", **pad)
        tk.Button(root, text="Browse...", command=self._browse_output).grid(
            row=5, column=1, **pad)

        # --- Action buttons ---
        button_frame = tk.Frame(root)
        button_frame.grid(row=6, column=0, columnspan=2, sticky="e", **pad)
        tk.Button(button_frame, text="Run", width=12, command=self._run).pack(
            side="left", padx=4)
        tk.Button(button_frame, text="Quit", width=8, command=root.destroy).pack(
            side="left", padx=4)

    def _browse_input(self):
        folder = filedialog.askdirectory(
            title="Select the Step 1 output folder",
            initialdir=self.input_var.get() or script_dir())
        if folder:
            self.input_var.set(folder)

    def _browse_summary(self):
        path = filedialog.askopenfilename(
            title="Select Building Inputs Summary.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.summary_var.get()) or script_dir())
        if path:
            self.summary_var.set(path)

    def _browse_output(self):
        folder = filedialog.askdirectory(
            title="Select the output folder for cleaned results",
            initialdir=self.output_var.get() or script_dir())
        if folder:
            self.output_var.set(folder)

    def _run(self):
        input_dir = self.input_var.get().strip()
        summary_path = self.summary_var.get().strip()
        output_dir = self.output_var.get().strip()

        # Validate the required inputs before doing any work.
        if not os.path.isdir(input_dir):
            messagebox.showerror("Step 2", "Please select a valid input folder.")
            return
        if not os.path.isfile(summary_path):
            messagebox.showerror("Step 2", "Please select a valid Building Inputs Summary.csv.")
            return
        if not output_dir:
            output_dir = os.path.join(script_dir(), DEFAULT_OUTPUT_DIRNAME)
            self.output_var.set(output_dir)

        # Create the output folder if it does not already exist.
        os.makedirs(output_dir, exist_ok=True)

        print(f"Input folder : {input_dir}")
        print(f"Summary file : {summary_path}")
        print(f"Output folder: {output_dir}\n")

        start_time = time.time()
        process_all(input_dir, output_dir, summary_path)
        tot_time = round((time.time() - start_time) / 60, 2)

        print("\nDone")
        print("Elapsed time:", tot_time, "mins")

        messagebox.showinfo(
            "Step 2",
            f"Processing complete.\n\nElapsed time: {tot_time} mins\n\n"
            f"Cleaned files saved to:\n{output_dir}")


# =============================================================================
# 8. Entry point
# =============================================================================

def main():
    root = tk.Tk()
    ProcessorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
