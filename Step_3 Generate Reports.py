# -*- coding: utf-8 -*-

"""
Step 3 - Generate Reports
=========================

Batch-produces a per-building damage-assessment summary report (PDF) from the
cleaned outputs of Step 2.

Each report contains:
  * A header band (repeated on every page) with the building's basic info and a
    plan-view placeholder box.
  * A wall-results section with three tables (CIAR1 strain metrics, CIAR2 strain
    metrics + damage categories), one row per wall. When a building has too many
    walls to fit on one page the tables automatically continue on the next
    page(s); the charts then follow on a fresh page.
  * A charts page with the Building Damage Interaction charts (Boscardin &
    Cording 1989 and Son & Cording 2005) and a displacement profile
    (chainage vs. dz) for the most critical wall across construction phases.

Inputs (all produced by Steps 1-2 / provided):
  * cleaned_output/data/*.csv   - per-phase building-damage tables (Step 2)
  * cleaned_output/lines/*.csv  - per-phase displacement-line tables (Step 2)
  * Building Inputs Summary.csv - wall geometry (length, height, E/G, base elev.)
"""

import os
import re
import glob
import math
import time
import traceback
from io import BytesIO
from functools import partial

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")  # headless backend - we only save figures to memory
import matplotlib.pyplot as plt

# Use Arial everywhere in the charts (falls back to DejaVu Sans if unavailable).
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "DejaVu Sans"]

import tkinter as tk
from tkinter import filedialog, messagebox

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, LongTable, TableStyle, Paragraph, Spacer, Image, PageBreak,
)


# =============================================================================
# 0. Look & feel (fonts, colours and sizing shared across the whole report)
# =============================================================================

def _register_report_fonts():
    """
    Register Arial (regular + bold) from the Windows font folder so reportlab can
    use it. Returns the (regular, bold) font names actually available, falling
    back to the built-in Helvetica if Arial cannot be found.
    """
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    try:
        pdfmetrics.registerFont(TTFont("Arial", os.path.join(fonts_dir, "arial.ttf")))
        pdfmetrics.registerFont(TTFont("Arial-Bold", os.path.join(fonts_dir, "arialbd.ttf")))
        pdfmetrics.registerFontFamily("Arial", normal="Arial", bold="Arial-Bold")
        return "Arial", "Arial-Bold"
    except Exception:
        return "Helvetica", "Helvetica-Bold"


# Font names used for every piece of text in the PDF.
FONT_REGULAR, FONT_BOLD = _register_report_fonts()

# The report's signature red (RGB 230, 30, 40) - titles, rules and headings.
REPORT_RED = colors.Color(230 / 255.0, 30 / 255.0, 40 / 255.0)

# One shared font size for every piece of text inside any table.
TABLE_FONT_SIZE = 8

# Fixed row heights so the two wall-results tables line up identically.
HEADER_ROW_HEIGHT = 40   # first (column-title) row of a content table
DATA_ROW_HEIGHT = 24     # every data row of a content table
INFO_ROW_HEIGHT = 18     # each row of the header info table


# =============================================================================
# 1. Configuration
# =============================================================================

# Default folders / files (resolved next to this script).
DEFAULT_CLEANED_DIRNAME = "CIAR2 Processed XDISP Output"     # Step 2 output (CIAR2)
DEFAULT_CIAR1_CLEANED_DIRNAME = "CIAR1 Processed XDISP Output"  # Step 2 output (CIAR1)
DEFAULT_SUMMARY_FILENAME = "Building Inputs Summary.csv"
DEFAULT_OUTPUT_DIRNAME = "Report"                   # created by this script

DATA_SUBDIR = "data"
LINES_SUBDIR = "lines"

# Only the un-smoothed base-model rows are used (matches the Cording workflow).
BASE_MODEL_STAGE = "Base Model"

# --- Column names in the cleaned "data" tables ------------------------------
COL_STAGE = "Stage: Name"
COL_BUILDING = "Specific Building: Name"
COL_WALL = "Sub-building Name"
COL_DR = "Deflection Ratio [%]"
COL_EPS_H = "Ave. Horizontal Strain (1e-3)"
COL_BEND = "Max Bending Strain (1e-3)"
COL_DIAG = "Max Diagonal Strain (1e-3)"
COL_BETA = "Angular Distortion (1e-3)"
COL_CRIT = "Epsilon Critical Max (1e-3)"
COL_BURLAND = "Damage Category (Burland 1995)"
COL_BC89 = "Damage Category (Boscardin and Cording, 1989)"
COL_SC05 = "Damage Category (Son and Cording, 2005) Based on Critical Strain"
COL_SC05M = "Damage Category (Son and Cording, 2005) Modified Curves"

# --- Column names in the cleaned "lines" tables -----------------------------
LCOL_STAGE = "Stage: Name"
LCOL_WALL = "Disp. Line: Name"
LCOL_CHAINAGE = "Chainage [m]"
LCOL_DZ = "dz [mm]"

# --- Column names in "Building Inputs Summary.csv" --------------------------
SUM_BUILDING = "Building Name"
SUM_WALL = "Wall Line"
SUM_LENGTH = "Length (m)"
SUM_EG = "E/G"
SUM_HEIGHT = "Height (m)"
SUM_BASE = "Base elevation (m)"

# --- Damage-category severity orders (least -> most severe) -----------------
BURLAND_ORDER = [
    "0 (Negligible)", "1 (Very Slight)", "2 (Slight)", "3 (Moderate)", "4 (Severe)",
]
CORDING_ORDER = [
    "Negligible Damage", "Very Slight Damage", "Slight Damage",
    "Moderate to Severe Damage", "Severe to Very Severe Damage",
]


def script_dir():
    """Return the directory that contains this script (fallback: cwd)."""
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def phase_number(path):
    """Extract the numeric phase from a filename ('[Phase_29]' -> 29).

    The initial phase file ('[InitialPhase]') has no number, so it maps to 0.
    """
    name = os.path.basename(path)
    if "InitialPhase" in name:
        return 0
    m = re.search(r"Phase_(\d+)", name)
    return int(m.group(1)) if m else -1


def wall_sort_key(wall):
    """Natural sort key so '..._2' comes before '..._10'."""
    text = str(wall)
    m = re.search(r"_(\d+)$", text)
    prefix = re.sub(r"_\d+$", "", text)
    return (prefix, int(m.group(1)) if m else 0)


def worst_category(values, order):
    """Return the most severe category found in `values` per the given order."""
    rank = {c: i for i, c in enumerate(order)}
    best_rank, best = -1, ""
    for v in values.dropna():
        v = str(v).strip()
        if v in rank and rank[v] > best_rank:
            best_rank, best = rank[v], v
    return best


def fnum(value, kind="g"):
    """Format a number for a table cell ('N/A' for missing)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    if kind == "mm":
        return f"{value:.1f}"
    return f"{value:.4g}"


# =============================================================================
# 2. Data loading & aggregation
# =============================================================================

def load_data_tables(data_dir):
    """Read and concatenate all cleaned 'data' CSVs (Base Model rows only)."""
    files = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
    if not files:
        raise FileNotFoundError(f"No data CSVs found in: {data_dir}")

    frames = [pd.read_csv(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df = df[df[COL_STAGE] == BASE_MODEL_STAGE].copy()
    return df, len(files)


def aggregate_walls(df):
    """
    Aggregate per (building, wall): max of each strain metric, the worst damage
    category for each method, and the (beta, eps_h) point at the row of maximum
    combined (critical) strain (used for the interaction charts).

    Returns a dict: building -> {wall -> metrics-dict}.
    """
    numeric_cols = [COL_DR, COL_EPS_H, COL_BEND, COL_DIAG, COL_BETA, COL_CRIT]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    results = {}
    for (building, wall), g in df.groupby([COL_BUILDING, COL_WALL]):
        # Point used on the interaction charts = row with the max critical strain.
        if g[COL_CRIT].notna().any():
            imax = g[COL_CRIT].idxmax()
            pt_beta = g.loc[imax, COL_BETA]
            pt_eps_h = g.loc[imax, COL_EPS_H]
        else:
            pt_beta = pt_eps_h = np.nan

        metrics = {
            "dr": g[COL_DR].max(),
            "eps_h": g[COL_EPS_H].max(),
            "bend": g[COL_BEND].max(),
            "diag": g[COL_DIAG].max(),
            "beta": g[COL_BETA].max(),
            "crit": g[COL_CRIT].max(),
            "burland": worst_category(g[COL_BURLAND], BURLAND_ORDER),
            "bc89": worst_category(g[COL_BC89], CORDING_ORDER),
            "sc05": worst_category(g[COL_SC05], CORDING_ORDER),
            "sc05m": worst_category(g[COL_SC05M], CORDING_ORDER),
            "pt_beta": pt_beta,
            "pt_eps_h": pt_eps_h,
        }
        results.setdefault(building, {})[wall] = metrics
    return results


def load_settlement(lines_dir):
    """Return {wall -> max settlement (mm)} over all phases (dz +ve = settlement)."""
    files = sorted(glob.glob(os.path.join(lines_dir, "*.csv")))
    if not files:
        return {}

    frames = [pd.read_csv(f) for f in files]
    df = pd.concat(frames, ignore_index=True)
    df = df[df[LCOL_STAGE] == BASE_MODEL_STAGE].copy()
    df[LCOL_DZ] = pd.to_numeric(df[LCOL_DZ], errors="coerce")
    return df.groupby(LCOL_WALL)[LCOL_DZ].max().to_dict()


def select_phase_line_files(lines_dir):
    """
    Choose which phases to draw on the displacement chart: the initial phase, the
    final phase, and every 5th phase in between (e.g. Initial, 5, 10, 15, 20, 25,
    Final). Returns a sorted list of (phase_number, file_path).
    """
    files = sorted(glob.glob(os.path.join(lines_dir, "*.csv")))
    by_num = {}
    for f in files:
        n = phase_number(f)
        if n >= 0:
            by_num[n] = f  # one file per phase number

    nums = sorted(by_num)
    if not nums:
        return []

    lo, hi = nums[0], nums[-1]
    keep = sorted({lo, hi} | {n for n in nums if n % 5 == 0})
    return [(n, by_num[n]) for n in keep]


def load_phase_lines(lines_dir):
    """
    Load the displacement-line data for the selected phases only.
    Returns a list of (phase_number, label, dataframe) with Base Model rows.
    """
    selected = select_phase_line_files(lines_dir)
    phase_lines = []
    for n, path in selected:
        df = pd.read_csv(path)
        df = df[df[LCOL_STAGE] == BASE_MODEL_STAGE].copy()
        df[LCOL_CHAINAGE] = pd.to_numeric(df[LCOL_CHAINAGE], errors="coerce")
        df[LCOL_DZ] = pd.to_numeric(df[LCOL_DZ], errors="coerce")
        label = "Initial" if n == 0 else f"Phase {n}"
        phase_lines.append((n, label, df))
    return phase_lines


def load_summary(summary_path):
    """
    Return two lookups from 'Building Inputs Summary.csv':
      * building_info: {building -> (height, base_elev, e_over_g)}
      * wall_length:   {wall -> length_m}
    """
    s = pd.read_csv(summary_path)
    building_info, wall_length = {}, {}
    for _, row in s.iterrows():
        building = str(row[SUM_BUILDING]).strip()
        wall = str(row[SUM_WALL]).strip()
        wall_length[wall] = float(row[SUM_LENGTH])
        if building not in building_info:
            building_info[building] = (
                float(row[SUM_HEIGHT]),
                float(row[SUM_BASE]),
                float(row[SUM_EG]),
            )
    return building_info, wall_length


# =============================================================================
# 3. Damage-interaction curves (ported from the Cording XDisp notebook)
# =============================================================================

def _filter_curve(beta, epsilon):
    """Keep only the portion of a boundary curve where epsilon >= 0."""
    mask = epsilon >= 0
    return beta[mask], epsilon[mask]


def _boscardin_cording_curves(beta):
    """Boscardin & Cording (1989) boundary curves (label, epsilon, color)."""
    return [
        ("Very Slight",        -0.3196 * beta**2 - 0.0345 * beta + 0.471,   "green"),
        ("Slight",             -0.2697 * beta**2 + 0.023 * beta + 0.7329,   "#8B8000"),
        ("Moderate to Severe", -0.1293 * beta**2 + 0.0052 * beta + 1.5444,  "orange"),
        ("Severe to Very Severe",
         0.0008 * beta**4 - 0.0085 * beta**3 - 0.0427 * beta**2 + 0.0095 * beta + 2.9997,
         "red"),
    ]


def _son_cording_curves(beta):
    """Son & Cording (2005) boundary curves (label, epsilon, color)."""
    return [
        ("Very Slight",        -0.527 * beta**2 + 0.0116 * beta + 0.4925,  "green"),
        ("Slight",             -0.3414 * beta**2 - 0.0018 * beta + 0.7464, "#8B8000"),
        ("Moderate to Severe", -0.1499 * beta**2 - 0.0064 * beta + 1.6709, "orange"),
        ("Severe to Very Severe", -0.0757 * beta**2 + 0.0022 * beta + 3.3333, "red"),
    ]


def make_interaction_figure(points, wall_colors):
    """
    Build the two side-by-side interaction charts.

    `points` is a list of (wall, beta, eps_h) - one worst-case point per wall.
    Returns a matplotlib Figure.
    """
    beta_curve = np.linspace(0, 8, 200)
    panels = [
        ("Boscardin and Cording (1989)", _boscardin_cording_curves(beta_curve)),
        ("Son and Cording (2005)", _son_cording_curves(beta_curve)),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(9.2, 4.4))
    for ax, (title, curves) in zip(axes, panels):
        # Damage-boundary curves.
        for label, eps, color in curves:
            bx, by = _filter_curve(beta_curve, eps)
            ax.plot(bx, by, color=color, linewidth=1.2, label=label)
        # One worst-case point per wall.
        for wall, beta, eps_h in points:
            if not (math.isnan(beta) or math.isnan(eps_h)):
                ax.scatter(beta, eps_h, color=wall_colors[wall], marker="x", s=28)
        ax.set_title(f"{title}\nDamage vs Angular Distortion & Horizontal Extension",
                     fontsize=8)
        ax.set_xlabel(r"Angular Distortion, $\beta$ (x10e-3)", fontsize=8)
        ax.set_ylabel(r"Horizontal Strain, $\epsilon_h$ (x10e-3)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, linewidth=0.4, alpha=0.6)
        ax.set_ylim(bottom=0)
        ax.set_xlim(left=0)
        ax.legend(fontsize=6, loc="upper right", title="Damage boundary", title_fontsize=6)

    fig.tight_layout()
    return fig


def make_displacement_figure(phase_lines, critical_wall):
    """
    Chainage vs dz for the single most critical wall of the building, drawn once
    per selected construction phase (Initial, every 5th phase, and Final).
    Earlier phases are darker, later phases lighter (viridis colour ramp).
    """
    fig, ax = plt.subplots(figsize=(9.2, 3.9))
    n_phases = max(1, len(phase_lines))
    for i, (num, label, df) in enumerate(phase_lines):
        sub = df[df[LCOL_WALL].astype(str) == str(critical_wall)]
        sub = sub.sort_values(LCOL_CHAINAGE)
        if sub.empty:
            continue
        ax.plot(sub[LCOL_CHAINAGE], sub[LCOL_DZ], marker="o", markersize=2.5,
                linewidth=1.0, color=plt.cm.viridis(i / n_phases), label=label)

    ax.set_title(f"Vertical Displacement of Most Critical Wall ({critical_wall}) "
                 f"across Construction Phases", fontsize=9)
    ax.set_xlabel("Chainage (m)", fontsize=8)
    ax.set_ylabel("dz (mm)", fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(True, linewidth=0.4, alpha=0.6)
    ax.legend(fontsize=6, ncol=min(7, n_phases), loc="upper center",
              bbox_to_anchor=(0.5, -0.18), title="Construction stage")
    fig.tight_layout()
    return fig


def figure_to_image(fig, width_pt):
    """Render a matplotlib Figure into a reportlab Image scaled to `width_pt`."""
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    iw, ih = ImageReader(buf).getSize()
    buf.seek(0)
    return Image(buf, width=width_pt, height=width_pt * ih / iw)


# =============================================================================
# 4. PDF report building (reportlab)
# =============================================================================

# Paragraph styles - every table cell uses the same font and size (Arial 8).
_CELL = ParagraphStyle("cell", fontName=FONT_REGULAR, fontSize=TABLE_FONT_SIZE,
                       leading=TABLE_FONT_SIZE + 2, alignment=1)  # 1 = centre
_CELL_HEAD = ParagraphStyle("cellhead", parent=_CELL, fontName=FONT_BOLD)
# Section headings (e.g. "Wall Damage Assessment Results").
_HEADING = ParagraphStyle("heading", fontName=FONT_BOLD, fontSize=11,
                          spaceAfter=6, textColor=REPORT_RED)

# Header-band geometry (points). The content frame starts below TOP_MARGIN.
TOP_MARGIN = 205
SIDE_MARGIN = 36
BOTTOM_MARGIN = 44
CONTENT_WIDTH = letter[0] - 2 * SIDE_MARGIN  # 540 pt

# Shared grid / padding / font used by both the info table and content tables.
_BASE_TABLE_COMMANDS = [
    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ("FONTNAME", (0, 0), (-1, -1), FONT_REGULAR),
    ("FONTSIZE", (0, 0), (-1, -1), TABLE_FONT_SIZE),
    ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
]

# Content tables (wall results) shade the first ROW (the column titles).
_CONTENT_TABLE_STYLE = TableStyle(
    _BASE_TABLE_COMMANDS
    + [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8"))]
)

# The header info table shades the first COLUMN (the field labels) instead.
_INFO_TABLE_STYLE = TableStyle(
    _BASE_TABLE_COMMANDS
    + [("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8E8E8"))]
)


def _p(text, head=False):
    """Wrap text in a Paragraph so long values wrap inside a cell."""
    return Paragraph(str(text), _CELL_HEAD if head else _CELL)


def _draw_header(canvas, doc, info):
    """Draw the repeated header band + footer on every page."""
    canvas.saveState()
    page_w, page_h = doc.pagesize

    # --- Title + red underline rule ---
    canvas.setFont(FONT_BOLD, 16)
    canvas.setFillColor(REPORT_RED)
    canvas.drawString(SIDE_MARGIN, page_h - 40, "CIAR 2 Summary Sheet")
    canvas.setStrokeColor(REPORT_RED)
    canvas.setLineWidth(1.5)
    canvas.line(SIDE_MARGIN, page_h - 46, page_w - SIDE_MARGIN, page_h - 46)

    # --- Building info table (left) ---
    rows = [
        ("Building Name", info["name"]),
        ("Building Height (m)", fnum(info["height"])),
        ("Base Elevation (m)", fnum(info["base"])),
        ("E/G Ratio", fnum(info["eg"])),
        ("Number of Walls Evaluated", str(info["n_walls"])),
        ("Maximum Length of Wall Evaluated (m)", fnum(info["max_len"])),
        ("Minimum Length of Wall Evaluated (m)", fnum(info["min_len"])),
        ("Number of Phases Evaluated", str(info["n_phases"])),
    ]
    # First column (field labels) is shaded via _INFO_TABLE_STYLE.
    info_tbl = LongTable([[_p(k, head=True), _p(v)] for k, v in rows],
                         colWidths=[168, 116],
                         rowHeights=[INFO_ROW_HEIGHT] * len(rows))
    info_tbl.setStyle(_INFO_TABLE_STYLE)
    tw, th = info_tbl.wrapOn(canvas, 300, 200)
    top_y = page_h - 54
    info_tbl.drawOn(canvas, SIDE_MARGIN, top_y - th)

    # --- Plan-view placeholder box (right) ---
    box_x0 = SIDE_MARGIN + 300
    box_x1 = page_w - SIDE_MARGIN
    box_y0 = top_y - th
    box_y1 = top_y
    canvas.setStrokeColor(colors.grey)
    canvas.setLineWidth(0.8)
    canvas.rect(box_x0, box_y0, box_x1 - box_x0, box_y1 - box_y0)
    canvas.setFillColor(colors.grey)
    canvas.setFont(FONT_REGULAR, 10)
    canvas.drawCentredString((box_x0 + box_x1) / 2, (box_y0 + box_y1) / 2 - 4,
                             "Plan view - to be added")

    # --- Footer (no page number, per request) ---
    canvas.setFillColor(colors.black)
    canvas.setFont(FONT_REGULAR, 7)
    canvas.drawString(SIDE_MARGIN, 26,
                      "S-108B-GEO-0018-300-002-B-C2  |  Draft  |  Arup Canada Inc.")
    canvas.restoreState()


def _strain_table(walls, metrics, settlement):
    """Build the strain-metrics LongTable (header repeats across pages)."""
    header = [
        "Wall ID", "Max Deflection Ratio (%)", "Max Settlement (mm)",
        "Max Horizontal Strain (1e-3)", "Max Bending Strain (1e-3)",
        "Max Diagonal Strain (1e-3)", "Max Angular Distortion (1e-3)",
        "Max Combined Strain (1e-3)",
    ]
    data = [[_p(h, head=True) for h in header]]
    for wall in walls:
        m = metrics[wall]
        # Wrap every value in a Paragraph so all cells share the same Arial size.
        data.append([
            _p(wall),
            _p(fnum(m["dr"])),
            _p(fnum(settlement.get(wall), kind="mm")),
            _p(fnum(m["eps_h"])),
            _p(fnum(m["bend"])),
            _p(fnum(m["diag"])),
            _p(fnum(m["beta"])),
            _p(fnum(m["crit"])),
        ])
    col_widths = [96, 63, 63, 63, 63, 63, 63, 66]
    row_heights = [HEADER_ROW_HEIGHT] + [DATA_ROW_HEIGHT] * len(walls)
    tbl = LongTable(data, colWidths=col_widths, rowHeights=row_heights, repeatRows=1)
    tbl.setStyle(_CONTENT_TABLE_STYLE)
    return tbl


def _damage_table(walls, metrics):
    """Build the damage-category LongTable (header repeats across pages)."""
    header = [
        "Wall ID", "Damage Category (Burland 1995)",
        "Damage Category (Boscardin and Cording, 1989)",
        "Damage Category (Son and Cording, 2005)",
        "Damage Category (Son and Cording, 2005) Modified",
    ]
    data = [[_p(h, head=True) for h in header]]
    for wall in walls:
        m = metrics[wall]
        data.append([
            _p(wall), _p(m["burland"]), _p(m["bc89"]), _p(m["sc05"]), _p(m["sc05m"]),
        ])
    col_widths = [96, 111, 111, 111, 111]
    # Same row-height configuration as the strain table so the two align.
    row_heights = [HEADER_ROW_HEIGHT] + [DATA_ROW_HEIGHT] * len(walls)
    tbl = LongTable(data, colWidths=col_widths, rowHeights=row_heights, repeatRows=1)
    tbl.setStyle(_CONTENT_TABLE_STYLE)
    return tbl


def build_building_report(out_path, info, walls, metrics, settlement,
                          interaction_img, displacement_img,
                          ciar1_walls, ciar1_metrics, ciar1_settlement):
    """Assemble and write one building's PDF report."""
    doc = SimpleDocTemplate(
        out_path, pagesize=letter,
        leftMargin=SIDE_MARGIN, rightMargin=SIDE_MARGIN,
        topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
    )

    story = [
        # CIAR1 strain table (same layout as CIAR2, computed from the CIAR1 folder).
        Paragraph("Wall Damage Assessment Results CIAR1", _HEADING),
        _strain_table(ciar1_walls, ciar1_metrics, ciar1_settlement),
        Spacer(1, 20),
        Paragraph("Wall Damage Assessment Results CIAR2", _HEADING),
        _strain_table(walls, metrics, settlement),
        Spacer(1, 20),
        Paragraph("Damage Category Evaluation CIAR1", _HEADING),
        _damage_table(ciar1_walls, ciar1_metrics),
        Spacer(1, 20),
        Paragraph("Damage Category Evaluation CIAR 2", _HEADING),
        _damage_table(walls, metrics),
        PageBreak(),
        Paragraph("Building Damage Interaction Charts", _HEADING),
        interaction_img,
        Spacer(1, 12),
        Paragraph("Displacement Profile - Most Critical Wall", _HEADING),
        displacement_img,
    ]

    header = partial(_draw_header, info=info)
    doc.build(story, onFirstPage=header, onLaterPages=header)


# =============================================================================
# 5. Orchestration (one report per building)
# =============================================================================

def _wall_color_map(walls):
    """Assign a distinct colour to each wall (works for many walls)."""
    n = max(1, len(walls))
    cmap = plt.cm.nipy_spectral
    return {wall: cmap(i / n) for i, wall in enumerate(walls)}


def _pick_critical_wall(wall_metrics, walls):
    """
    Return the 'most critical' wall for the building - the one with the largest
    Maximum Combined (critical) Strain. Used for the displacement chart.
    """
    best_wall, best_val = walls[0], -math.inf
    for wall in walls:
        val = wall_metrics[wall]["crit"]
        if val is not None and not (isinstance(val, float) and math.isnan(val)) \
                and val > best_val:
            best_wall, best_val = wall, val
    return best_wall


def process_all(cleaned_dir, summary_path, output_dir, ciar1_dir):
    """Generate a report for every building found in the cleaned data."""
    data_dir = os.path.join(cleaned_dir, DATA_SUBDIR)
    lines_dir = os.path.join(cleaned_dir, LINES_SUBDIR)

    # Load everything once.
    df, n_phases = load_data_tables(data_dir)
    results = aggregate_walls(df)
    settlement = load_settlement(lines_dir)
    phase_lines = load_phase_lines(lines_dir)   # selected phases for the disp. chart
    building_info, wall_length = load_summary(summary_path)

    # CIAR1 uses the same aggregation logic, only a different source folder.
    ciar1_results = {}
    ciar1_settlement = {}
    ciar1_data_dir = os.path.join(ciar1_dir, DATA_SUBDIR)
    if os.path.isdir(ciar1_data_dir):
        ciar1_df, _ = load_data_tables(ciar1_data_dir)
        ciar1_results = aggregate_walls(ciar1_df)
        ciar1_settlement = load_settlement(os.path.join(ciar1_dir, LINES_SUBDIR))
    else:
        print(f"WARNING: CIAR1 data folder not found - CIAR1 table will be empty: "
              f"{ciar1_data_dir}")

    os.makedirs(output_dir, exist_ok=True)
    print(f"Found {len(results)} building(s). Phases evaluated: {n_phases}\n")

    for building, wall_metrics in results.items():
        try:
            walls = sorted(wall_metrics.keys(), key=wall_sort_key)
            wall_colors = _wall_color_map(walls)

            # Matching CIAR1 walls/metrics for this building (may be absent).
            ciar1_wall_metrics = ciar1_results.get(building, {})
            ciar1_walls = sorted(ciar1_wall_metrics.keys(), key=wall_sort_key)

            # Header info (geometry from the building summary).
            height, base, eg = building_info.get(building, (np.nan, np.nan, np.nan))
            lengths = [wall_length[w] for w in walls if w in wall_length]
            info = {
                "name": building,
                "height": height,
                "base": base,
                "eg": eg,
                "n_walls": len(walls),
                "max_len": max(lengths) if lengths else np.nan,
                "min_len": min(lengths) if lengths else np.nan,
                "n_phases": n_phases,
            }

            # --- Interaction charts: one worst-case point per wall ---
            points = [(w, wall_metrics[w]["pt_beta"], wall_metrics[w]["pt_eps_h"])
                      for w in walls]
            interaction_fig = make_interaction_figure(points, wall_colors)

            # --- Displacement chart: the single most critical wall across phases ---
            critical_wall = _pick_critical_wall(wall_metrics, walls)
            displacement_fig = make_displacement_figure(phase_lines, critical_wall)

            interaction_img = figure_to_image(interaction_fig, CONTENT_WIDTH)
            displacement_img = figure_to_image(displacement_fig, CONTENT_WIDTH)

            # Write the PDF.
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", building)
            out_path = os.path.join(output_dir, f"{safe_name}.pdf")
            build_building_report(out_path, info, walls, wall_metrics, settlement,
                                  interaction_img, displacement_img,
                                  ciar1_walls, ciar1_wall_metrics, ciar1_settlement)
            print(f"  report: {safe_name}.pdf  ({len(walls)} walls)")
        except Exception:
            print(f"  ERROR building report for: {building}")
            traceback.print_exc()


# =============================================================================
# 6. Simple GUI
# =============================================================================

class ReportGUI:
    """Window to confirm the cleaned-output folder, summary file and output folder."""

    def __init__(self, root):
        self.root = root
        self.root.title("Step 3 - Generate Reports")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        base = script_dir()
        self.ciar1_var = tk.StringVar(value=os.path.join(base, DEFAULT_CIAR1_CLEANED_DIRNAME))
        self.cleaned_var = tk.StringVar(value=os.path.join(base, DEFAULT_CLEANED_DIRNAME))
        self.summary_var = tk.StringVar(value=os.path.join(base, DEFAULT_SUMMARY_FILENAME))
        self.output_var = tk.StringVar(value=os.path.join(base, DEFAULT_OUTPUT_DIRNAME))

        pad = {"padx": 8, "pady": 6}

        tk.Label(root, text="CIAR1 cleaned output folder (Step 2, contains data/ & lines/):").grid(
            row=0, column=0, columnspan=2, sticky="w", **pad)
        tk.Entry(root, textvariable=self.ciar1_var, width=64).grid(
            row=1, column=0, sticky="we", **pad)
        tk.Button(root, text="Browse...", command=self._browse_ciar1).grid(
            row=1, column=1, **pad)

        tk.Label(root, text="CIAR2 cleaned output folder (Step 2, contains data/ & lines/):").grid(
            row=2, column=0, columnspan=2, sticky="w", **pad)
        tk.Entry(root, textvariable=self.cleaned_var, width=64).grid(
            row=3, column=0, sticky="we", **pad)
        tk.Button(root, text="Browse...", command=self._browse_cleaned).grid(
            row=3, column=1, **pad)

        tk.Label(root, text="Building Inputs Summary.csv:").grid(
            row=4, column=0, columnspan=2, sticky="w", **pad)
        tk.Entry(root, textvariable=self.summary_var, width=64).grid(
            row=5, column=0, sticky="we", **pad)
        tk.Button(root, text="Browse...", command=self._browse_summary).grid(
            row=5, column=1, **pad)

        tk.Label(root, text="Output folder (reports):").grid(
            row=6, column=0, columnspan=2, sticky="w", **pad)
        tk.Entry(root, textvariable=self.output_var, width=64).grid(
            row=7, column=0, sticky="we", **pad)
        tk.Button(root, text="Browse...", command=self._browse_output).grid(
            row=7, column=1, **pad)

        button_frame = tk.Frame(root)
        button_frame.grid(row=8, column=0, columnspan=2, sticky="e", **pad)
        tk.Button(button_frame, text="Generate", width=12, command=self._run).pack(
            side="left", padx=4)
        tk.Button(button_frame, text="Quit", width=8, command=root.destroy).pack(
            side="left", padx=4)

    def _browse_ciar1(self):
        folder = filedialog.askdirectory(
            title="Select the Step 2 CIAR1 cleaned_output folder",
            initialdir=self.ciar1_var.get() or script_dir())
        if folder:
            self.ciar1_var.set(folder)

    def _browse_cleaned(self):
        folder = filedialog.askdirectory(
            title="Select the Step 2 CIAR2 cleaned_output folder",
            initialdir=self.cleaned_var.get() or script_dir())
        if folder:
            self.cleaned_var.set(folder)

    def _browse_summary(self):
        path = filedialog.askopenfilename(
            title="Select Building Inputs Summary.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.summary_var.get()) or script_dir())
        if path:
            self.summary_var.set(path)

    def _browse_output(self):
        folder = filedialog.askdirectory(
            title="Select the output folder for reports",
            initialdir=self.output_var.get() or script_dir())
        if folder:
            self.output_var.set(folder)

    def _run(self):
        ciar1_dir = self.ciar1_var.get().strip()
        cleaned_dir = self.cleaned_var.get().strip()
        summary_path = self.summary_var.get().strip()
        output_dir = self.output_var.get().strip()

        if not os.path.isdir(os.path.join(cleaned_dir, DATA_SUBDIR)):
            messagebox.showerror("Step 3", "The CIAR2 cleaned folder must contain a 'data' sub-folder.")
            return
        if not os.path.isdir(os.path.join(ciar1_dir, DATA_SUBDIR)):
            messagebox.showerror("Step 3", "The CIAR1 cleaned folder must contain a 'data' sub-folder.")
            return
        if not os.path.isfile(summary_path):
            messagebox.showerror("Step 3", "Please select a valid Building Inputs Summary.csv.")
            return
        if not output_dir:
            output_dir = os.path.join(script_dir(), DEFAULT_OUTPUT_DIRNAME)
            self.output_var.set(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        print(f"CIAR1 folder   : {ciar1_dir}")
        print(f"CIAR2 folder   : {cleaned_dir}")
        print(f"Summary file   : {summary_path}")
        print(f"Output folder  : {output_dir}\n")

        start_time = time.time()
        try:
            process_all(cleaned_dir, summary_path, output_dir, ciar1_dir)
        except Exception:
            traceback.print_exc()
            messagebox.showerror("Step 3", "An error occurred - see the console for details.")
            return
        tot_time = round((time.time() - start_time) / 60, 2)

        print("\nDone")
        print("Elapsed time:", tot_time, "mins")
        messagebox.showinfo(
            "Step 3",
            f"Report generation complete.\n\nElapsed time: {tot_time} mins\n\n"
            f"Reports saved to:\n{output_dir}")


# =============================================================================
# 7. Entry point
# =============================================================================

def main():
    root = tk.Tk()
    ReportGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
