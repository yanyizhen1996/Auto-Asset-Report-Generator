# -*- coding: utf-8 -*-

"""
Simple XDISP results exporter.

Reads every XDISP model file (.json or .xdd) in a chosen input folder, opens it
via the XDISP COM API, analyses it, and writes the results tables out to CSV.
No iteration / volume-loss logic - just a straight export of results.

A small Tkinter window is used to pick the input folder (containing the models)
and the output folder (where the CSV results are written). The output folder
defaults to an "Export_Results_Output" folder next to this script and is created
automatically if it does not already exist.
"""

import os
import time
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox

from comtypes.client import Constants, CreateObject
import win32com.client


# Model file extensions the XDISP API can open
SUPPORTED_EXTENSIONS = (".json", ".xdd")

# Default output folder name (created next to this script)
DEFAULT_OUTPUT_DIRNAME = "XDISP Output"


def script_dir():
    """Return the directory that contains this script."""
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        # __file__ is undefined (e.g. interactive) - fall back to cwd
        return os.getcwd()


def default_output_dir():
    """Return the default output folder path next to this script."""
    return os.path.join(script_dir(), DEFAULT_OUTPUT_DIRNAME)


def model_base_name(filename):
    """Strip the model extensions (.json / .xdd, including .json.xdd) from a name."""
    base = filename
    for ext in (".xdd", ".json"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
    return base


def export_results(models_dir, results_dir):
    """Open, analyse and export every supported model in models_dir."""
    data_dir = os.path.join(results_dir, "data")
    lines_dir = os.path.join(results_dir, "lines")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(lines_dir, exist_ok=True)

    # Process both JSON and XDD model files
    model_files = [
        f for f in os.listdir(models_dir)
        if f.lower().endswith(SUPPORTED_EXTENSIONS)
    ]

    if not model_files:
        print(f"No .json or .xdd model files found in: {models_dir}")
        return

    print(f"Found {len(model_files)} model file(s) to export.\n")

    # Stage / rail-track selectors (-1 = all), same as the batch runner
    iStage = -1
    iRailTrack = -1

    for filename in model_files:
        filepath = os.path.join(models_dir, filename)
        objCOM = None
        try:
            # Create the COM object and grab the constants
            objCOM = CreateObject("XDispAuto_20_2.ComAuto")
            xdisp_constants = Constants(objCOM)
            objCOM = win32com.client.Dispatch("XDispAuto_20_2.ComAuto")

            # Open and analyse the model
            objCOM.Open(filepath)
            print(f"'{filename}' opened. Analysing...")
            objCOM.Analyse()

            # Output labels (WriteResultsTable appends the .csv extension)
            base = model_base_name(filename)
            data_label = os.path.join(data_dir, f"{base}")
            lines_label = os.path.join(lines_dir, f"{base}_line")

            # Export the buildings detail table
            objCOM.WriteResultsTable(
                data_label,
                xdisp_constants.CSV,
                xdisp_constants.RESULTS_TABLE_BUILDINGS_SPECIFIC_UNCOMBINED_DETAIL,
                iStage,
                iRailTrack,
            )

            # Export the displacement lines table
            objCOM.WriteResultsTable(
                lines_label,
                xdisp_constants.CSV,
                xdisp_constants.RESULTS_TABLE_DISPLACEMENT_LINES,
                iStage,
                iRailTrack,
            )

            objCOM.Close()
            print(f"'{filename}' exported.\n")

        except Exception:
            print(f"\nAn error occurred on file: {filename}")
            traceback.print_exc()
            if objCOM is not None:
                try:
                    objCOM.Close()
                except Exception:
                    pass
        finally:
            objCOM = None


class ExporterGUI:
    """Small window to choose the input and output folders, then run the export."""

    def __init__(self, root):
        self.root = root
        self.root.title("XDISP Results Exporter")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar(value=default_output_dir())

        pad = {"padx": 8, "pady": 6}

        # --- Input folder row ---
        tk.Label(root, text="Input folder (.json / .xdd models):").grid(
            row=0, column=0, columnspan=2, sticky="w", **pad
        )
        tk.Entry(root, textvariable=self.input_var, width=60).grid(
            row=1, column=0, sticky="we", **pad
        )
        tk.Button(root, text="Browse...", command=self._browse_input).grid(
            row=1, column=1, **pad
        )

        # --- Output folder row ---
        tk.Label(root, text="Output folder (CSV results):").grid(
            row=2, column=0, columnspan=2, sticky="w", **pad
        )
        tk.Entry(root, textvariable=self.output_var, width=60).grid(
            row=3, column=0, sticky="we", **pad
        )
        tk.Button(root, text="Browse...", command=self._browse_output).grid(
            row=3, column=1, **pad
        )

        # --- Action buttons ---
        button_frame = tk.Frame(root)
        button_frame.grid(row=4, column=0, columnspan=2, sticky="e", **pad)
        tk.Button(button_frame, text="Run Export", width=12, command=self._run).pack(
            side="left", padx=4
        )
        tk.Button(button_frame, text="Quit", width=8, command=root.destroy).pack(
            side="left", padx=4
        )

    def _browse_input(self):
        folder = filedialog.askdirectory(
            title="Select the folder containing XDISP model (.json / .xdd) files",
            initialdir=self.input_var.get() or script_dir(),
        )
        if folder:
            self.input_var.set(folder)

    def _browse_output(self):
        folder = filedialog.askdirectory(
            title="Select the output folder for CSV results",
            initialdir=self.output_var.get() or script_dir(),
        )
        if folder:
            self.output_var.set(folder)

    def _run(self):
        models_dir = self.input_var.get().strip()
        results_dir = self.output_var.get().strip()

        if not models_dir or not os.path.isdir(models_dir):
            messagebox.showerror(
                "XDISP Export", "Please select a valid input folder."
            )
            return

        if not results_dir:
            results_dir = default_output_dir()
            self.output_var.set(results_dir)

        # Create the output folder if it does not already exist
        os.makedirs(results_dir, exist_ok=True)

        print(f"Models folder : {models_dir}")
        print(f"Results folder: {results_dir}\n")

        start_time = time.time()
        export_results(models_dir, results_dir)
        tot_time = round((time.time() - start_time) / 60, 1)

        print("Done")
        print("Elapsed time:", tot_time, "mins")

        messagebox.showinfo(
            "XDISP Export",
            f"Export complete.\n\nElapsed time: {tot_time} mins\n\n"
            f"Results saved to:\n{results_dir}",
        )


def main():
    root = tk.Tk()
    ExporterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
