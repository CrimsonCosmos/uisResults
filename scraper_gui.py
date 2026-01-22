#!/usr/bin/env python3
"""
UIS Athletics Scraper - GUI Launcher
Simple interface to run the scraper with different options.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import subprocess
import threading
import os
import re
import sys


class ScraperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("UIS Athletics Scraper")
        self.root.geometry("450x420")
        self.root.resizable(False, False)

        # Center window on screen
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 450) // 2
        y = (self.root.winfo_screenheight() - 420) // 2
        self.root.geometry(f"450x420+{x}+{y}")

        self.create_widgets()

    def create_widgets(self):
        # Main frame with padding
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Title
        title_label = ttk.Label(main_frame, text="UIS Athletics Scraper",
                                font=('Helvetica', 18, 'bold'))
        title_label.pack(pady=(0, 20))

        # Sports selection frame
        sports_frame = ttk.LabelFrame(main_frame, text="Sports to Check", padding="10")
        sports_frame.pack(fill=tk.X, pady=(0, 15))

        self.xc_var = tk.BooleanVar(value=False)
        self.indoor_var = tk.BooleanVar(value=True)
        self.outdoor_var = tk.BooleanVar(value=True)

        ttk.Checkbutton(sports_frame, text="Cross Country",
                        variable=self.xc_var).pack(anchor=tk.W)
        ttk.Checkbutton(sports_frame, text="Indoor Track & Field",
                        variable=self.indoor_var).pack(anchor=tk.W)
        ttk.Checkbutton(sports_frame, text="Outdoor Track & Field",
                        variable=self.outdoor_var).pack(anchor=tk.W)

        # Days selection frame
        days_frame = ttk.LabelFrame(main_frame, text="Time Range", padding="10")
        days_frame.pack(fill=tk.X, pady=(0, 15))

        days_inner = ttk.Frame(days_frame)
        days_inner.pack(fill=tk.X)

        ttk.Label(days_inner, text="Check last").pack(side=tk.LEFT)

        self.days_var = tk.StringVar(value="7")
        days_spinbox = ttk.Spinbox(days_inner, from_=1, to=30, width=5,
                                    textvariable=self.days_var)
        days_spinbox.pack(side=tk.LEFT, padx=5)

        ttk.Label(days_inner, text="days").pack(side=tk.LEFT)

        # Progress frame
        progress_frame = ttk.LabelFrame(main_frame, text="Progress", padding="10")
        progress_frame.pack(fill=tk.X, pady=(0, 15))

        # Progress bar
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var,
                                             maximum=100, mode='determinate')
        self.progress_bar.pack(fill=tk.X, pady=(0, 5))

        # Status label (shows current action)
        self.status_var = tk.StringVar(value="Ready")
        self.status_label = ttk.Label(progress_frame, textvariable=self.status_var,
                                       foreground="gray")
        self.status_label.pack()

        # Run button
        self.run_button = ttk.Button(main_frame, text="Run Scraper",
                                      command=self.run_scraper)
        self.run_button.pack(pady=(10, 5))

    def run_scraper(self):
        # Validate at least one sport is selected
        if not (self.xc_var.get() or self.indoor_var.get() or self.outdoor_var.get()):
            messagebox.showwarning("No Sport Selected",
                                   "Please select at least one sport to check.")
            return

        # Build command - use sys.executable to ensure same Python interpreter
        cmd = [sys.executable, "-u", "scraper.py"]  # -u for unbuffered output

        # Add days
        cmd.extend(["--days", self.days_var.get()])

        # Add sports
        if self.xc_var.get():
            cmd.append("--xc")
        if self.indoor_var.get():
            cmd.append("--indoor")
        if self.outdoor_var.get():
            cmd.append("--outdoor")

        # Always save to Desktop
        cmd.append("--desktop")

        # Reset progress
        self.progress_var.set(0)
        self.status_var.set("Starting...")

        # Disable button
        self.run_button.config(state=tk.DISABLED)
        self.root.update()

        # Run in thread to keep UI responsive
        thread = threading.Thread(target=self.execute_scraper, args=(cmd,))
        thread.start()

    def execute_scraper(self, cmd):
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))

            # Use Popen to capture output line by line
            process = subprocess.Popen(
                cmd,
                cwd=script_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            output_lines = []
            filepath = None

            # Read output line by line
            for line in process.stdout:
                line = line.strip()
                output_lines.append(line)

                # Parse progress updates
                self.parse_progress(line)

                # Capture filepath
                if "Results saved to:" in line:
                    filepath = line.split("Results saved to:")[-1].strip()

            process.wait()

            # Check result
            full_output = '\n'.join(output_lines)

            if "SUCCESS" in full_output or filepath:
                self.root.after(0, lambda: self.on_success(filepath))
            elif "No results found" in full_output:
                self.root.after(0, self.on_no_results)
            else:
                self.root.after(0, lambda: self.on_error(full_output[-500:]))

        except Exception as e:
            self.root.after(0, lambda: self.on_error(str(e)))

    def parse_progress(self, line):
        """Parse scraper output and update progress bar."""

        # Starting browser
        if "Starting browser" in line:
            self.root.after(0, lambda: self.update_progress(2, "Starting browser..."))

        # Checking ChromeDriver
        elif "Checking ChromeDriver" in line:
            self.root.after(0, lambda: self.update_progress(4, "Checking ChromeDriver..."))

        # Launching Chrome
        elif "Launching Chrome" in line:
            self.root.after(0, lambda: self.update_progress(8, "Launching Chrome..."))

        # Loading team page
        elif "Loading team page" in line:
            self.root.after(0, lambda: self.update_progress(12, "Loading team page..."))

        # Capturing API tokens
        elif "Capturing API tokens" in line:
            self.root.after(0, lambda: self.update_progress(16, "Capturing API tokens..."))

        # API tokens captured
        elif "API tokens captured" in line:
            self.root.after(0, lambda: self.update_progress(18, "API ready!"))

        # Checking sport
        elif "Checking" in line and ("Cross Country" in line or "Track & Field" in line):
            sport = line.split("Checking")[-1].split("...")[0].strip()
            self.root.after(0, lambda s=sport: self.update_progress(20, f"Checking {s}..."))

        # Checking athletes progress (e.g., "[1] Athlete Name" or "[1/55] Athlete Name")
        elif re.search(r'\[(\d+)\]', line):
            # New format: [N] Name (no total, just count of active athletes)
            match = re.search(r'\[(\d+)\]\s*(.*)', line)
            if match:
                current = int(match.group(1))
                name = match.group(2).strip()
                # Estimate progress (assume ~30 active athletes typical)
                pct = min(80, 20 + (current * 2))  # 20-80% range
                self.root.after(0, lambda p=pct, c=current, n=name:
                    self.update_progress(p, f"[{c}] {n}"))

        # Found results
        elif "Found" in line and ("total results" in line or "results from" in line):
            match = re.search(r'Found (\d+)', line)
            if match:
                count = match.group(1)
                self.root.after(0, lambda c=count:
                    self.update_progress(85, f"Found {c} results, processing..."))

        # Saving
        elif "Results saved to" in line:
            self.root.after(0, lambda: self.update_progress(95, "Saving spreadsheet..."))

        # Success
        elif "SUCCESS" in line:
            self.root.after(0, lambda: self.update_progress(100, "Complete!"))

    def update_progress(self, percent, status):
        """Update progress bar and status label."""
        self.progress_var.set(percent)
        self.status_var.set(status)

    def on_success(self, filepath):
        self.run_button.config(state=tk.NORMAL)
        self.progress_var.set(100)
        self.status_var.set("Complete! Opening results...")

        if filepath and os.path.exists(filepath):
            subprocess.run(["open", filepath])
            self.status_var.set("Complete!")
        else:
            self.status_var.set("Complete! Check Desktop for results.")

    def on_no_results(self):
        self.run_button.config(state=tk.NORMAL)
        self.progress_var.set(100)
        self.status_var.set("No results found.")
        messagebox.showinfo("No Results",
                           "No results were found in the specified time period.")

    def on_error(self, error_msg):
        self.run_button.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.status_var.set("Error occurred")
        messagebox.showerror("Error", f"An error occurred:\n\n{error_msg[:500]}")


def main():
    root = tk.Tk()
    app = ScraperGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
