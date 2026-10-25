"""Desktop interaction harness for the real Proximus Tk shell.

This is NOT a PocketCHIP board, NAND, touch-controller or PMIC emulator.
Launch with: PYTHONPATH=pocketchip python3 -m simulator.pocketchip
"""
import argparse
from pathlib import Path
import tempfile
import tkinter as tk

from proximus_os.shell import MODULES, Shell


KEY_ROWS = (
    ("1", "2", "3", "4", "5", "6", "7", "8", "9", "0"),
    ("q", "w", "e", "r", "t", "y", "u", "i", "o", "p"),
    ("a", "s", "d", "f", "g", "h", "j", "k", "l"),
    ("z", "x", "c", "v", "b", "n", "m", ".", "/"),
)


def inject_key(shell, key):
    """Send a key to the focused Tk widget without OS-wide event injection."""
    window = shell.root
    focused = getattr(shell, "_sim_focus", None) or window.focus_get()\n    if focused is not None and not focused.winfo_exists():\n        focused = None
    if key == "HOME":
        shell.show_home()
        return
    if key == "ESC":
        shell.show_home()
        return
    if shell.page == "Home" and key in "123456" and len(key) == 1:
        shell.open(MODULES[int(key) - 1][0])
        return
    if focused is None:
        return
    if isinstance(focused, (tk.Entry, tk.Text)):
        if key == "BACK":
            if isinstance(focused, tk.Entry):
                if focused.selection_present():
                    focused.delete("sel.first", "sel.last")
                else:
                    pos = focused.index(tk.INSERT)
                    if pos:
                        focused.delete(pos - 1, pos)
            else:
                try:
                    focused.delete("sel.first", "sel.last")
                except tk.TclError:
                    try:
                        focused.delete("insert-1c")
                    except tk.TclError:
                        pass
        elif key == "SPACE":
            focused.insert(tk.INSERT, " ")
        elif key == "ENTER":
            focused.event_generate("<Return>")
        elif len(key) == 1:
            focused.insert(tk.INSERT, key)
    elif key == "ENTER" and isinstance(focused, tk.Button):
        focused.invoke()


def launch(data, config):
    screen = tk.Tk()
    screen.title("PROXIMUS OS / DISPLAY 480x272 (desktop simulation)")
    shell = Shell(screen, data, config)
    screen.geometry("480x272")
    screen.resizable(False, False)
    panel = tk.Toplevel(screen)
    panel.title("PocketCHIP / SIMULATOR CONTROLS")
    panel.configure(bg="#18201b")
    panel.geometry("650x360")
    heading = tk.Label(panel, text="POCKETCHIP / INPUT BENCH", bg="#18201b",
                       fg="#e0b563", font=("DejaVu Sans", 14, "bold"))
    heading.pack(pady=9)
    tk.Label(panel,
             text="Display is the REAL Proximus Tk shell; mouse clicks emulate taps.\n"
                  "Keys below inject input only into this Tk process. No board hardware is emulated.",
             bg="#18201b", fg="#ddd5bd", justify="center").pack(pady=3)
    keyboard = tk.Frame(panel, bg="#18201b")
    keyboard.pack(pady=8)
    for rownum, row in enumerate(KEY_ROWS):
        for colnum, key in enumerate(row):
            tk.Button(keyboard, text=key.upper(), width=5,
                      command=lambda k=key: inject_key(shell, k)).grid(
                          row=rownum, column=colnum, padx=2, pady=2)
    bottom = tk.Frame(panel, bg="#18201b")
    bottom.pack(pady=8)
    for title, key in (("HOME", "HOME"), ("ESC", "ESC"), ("SPACE", "SPACE"),
                       ("BACKSPACE", "BACK"), ("ENTER", "ENTER")):
        tk.Button(bottom, text=title, command=lambda k=key: inject_key(shell, k)).pack(
            side="left", padx=5)
    tk.Label(panel, text="Host WLAN / battery readings are real host values, NOT simulated PocketCHIP telemetry.",
             bg="#18201b", fg="#d6ad82").pack(pady=5)
    panel.protocol("WM_DELETE_WINDOW", screen.destroy)
    screen.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="proximus-sim-") as temp:
        # Simulated usage must not change the user's normal shell archive.
        data = args.data or Path(temp) / "archive"
        config = args.config or Path(temp) / "missing-client.json"
        launch(data, config)


if __name__ == "__main__":
    main()
