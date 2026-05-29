import time
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import os
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty

import mss
from PIL import Image

# Global mouse/keyboard listeners (captures clicks/scroll/enter anywhere)
from pynput import mouse, keyboard


# -----------------------------
# Globals
# -----------------------------
screenshot_thread = None
stop_event = None
is_capturing = False
current_output_dir = None
base_output_dir = None

interval_var = None
mode_var = None
cooldown_var = None
name_var = None
monitor_var = None

status_label = None
start_button = None
stop_button = None
save_dir_label = None

monitor_options = []
mss_monitors = []


# -----------------------------
# Helpers
# -----------------------------
def get_default_base_dir() -> str:
    """
    Prefer ~/Documents/ScreenCaptures if Documents exists, otherwise ~/ScreenCaptures.
    This avoids issues with paths next to an EXE in Program Files / restricted folders.
    """
    home = Path.home()
    docs = home / "Documents"
    base = (docs / "ScreenCaptures") if docs.exists() else (home / "ScreenCaptures")
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def sanitize_prefix(s: str) -> str:
    bad = '<>:"/\\|?*'
    s = (s or "").strip()
    for ch in bad:
        s = s.replace(ch, "_")
    return s


def create_output_folder(prefix: str | None) -> str:
    """
    Creates a timestamped folder inside base_output_dir.
    Example: <base>\MyJob_2026-01-29_15-22-10
    """
    global base_output_dir

    if not base_output_dir:
        base_output_dir = get_default_base_dir()

    Path(base_output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder_name = f"{prefix}_{timestamp}" if prefix else f"captures_{timestamp}"

    output_dir = os.path.join(base_output_dir, folder_name)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def grab_and_save_one(output_dir: str, monitor_index: int, filename_prefix: str, i: int) -> str:
    """
    Capture one screenshot using MSS and save it as PNG.
    """
    with mss.mss() as sct:
        mon = sct.monitors[monitor_index]  # dict: left, top, width, height
        raw = sct.grab(mon)                # BGRA
        img = Image.frombytes("RGB", raw.size, raw.rgb)

    filename = f"{filename_prefix}_{i:04d}.png"
    filepath = os.path.join(output_dir, filename)
    img.save(filepath)
    return filepath


# -----------------------------
# Capture threads
# -----------------------------
def take_screenshots_interval(output_dir: str, stop_event: threading.Event, interval: float,
                             monitor_index: int, filename_prefix: str):
    i = 0
    while not stop_event.is_set():
        time.sleep(interval)
        try:
            grab_and_save_one(output_dir, monitor_index, filename_prefix, i)
            i += 1
        except Exception:
            # Keep running even if one capture fails
            pass


def take_screenshots_on_events(output_dir: str, stop_event: threading.Event, monitor_index: int,
                               filename_prefix: str, cooldown_s: float):
    """
    Takes screenshots on:
      - any mouse button press (left/right/middle)
      - wheel scroll (up/down/horizontal)
      - Enter key
    Uses cooldown to avoid spamming from rapid inputs.
    """
    q: Queue[str] = Queue()
    last_shot = {"t": 0.0}
    counter = {"i": 0}

    def maybe_enqueue(reason: str):
        now = time.time()
        if now - last_shot["t"] < cooldown_s:
            return
        last_shot["t"] = now
        q.put(reason)

    def worker():
        while not stop_event.is_set():
            try:
                _reason = q.get(timeout=0.2)
            except Empty:
                continue
            try:
                i = counter["i"]
                grab_and_save_one(output_dir, monitor_index, filename_prefix, i)
                counter["i"] += 1
            except Exception:
                pass
            finally:
                q.task_done()

    def on_click(x, y, button, pressed):
        # Screenshot on press of ANY mouse button
        if pressed:
            try:
                bname = button.name  # left/right/middle
            except Exception:
                bname = "button"
            maybe_enqueue(f"click_{bname}")

    def on_scroll(x, y, dx, dy):
        # Screenshot on any wheel movement
        maybe_enqueue("scroll")

    def on_press(key):
        # Screenshot on Enter
        if key == keyboard.Key.enter:
            maybe_enqueue("enter")

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    mouse_listener = mouse.Listener(on_click=on_click, on_scroll=on_scroll)
    key_listener = keyboard.Listener(on_press=on_press)

    mouse_listener.start()
    key_listener.start()

    # Keep alive until stopped
    while not stop_event.is_set():
        time.sleep(0.1)

    # Stop listeners
    try:
        mouse_listener.stop()
    except Exception:
        pass
    try:
        key_listener.stop()
    except Exception:
        pass


# -----------------------------
# UI actions
# -----------------------------
def start_capture():
    global screenshot_thread, stop_event, is_capturing, current_output_dir

    if is_capturing:
        return

    # Mode
    mode = mode_var.get()  # "interval" or "events"

    # Project name / prefix
    prefix = sanitize_prefix(name_var.get())
    filename_prefix = prefix if prefix else "screenshot"

    # Monitor selection -> index
    selected = monitor_var.get()
    try:
        monitor_index = monitor_options.index(selected)
    except ValueError:
        monitor_index = 0

    # Create folder
    try:
        current_output_dir = create_output_folder(prefix if prefix else None)
    except Exception as e:
        messagebox.showerror("Error", f"Could not create output folder:\n{e}")
        return

    stop_event = threading.Event()
    is_capturing = True

    if mode == "interval":
        # Interval (min 1.0)
        try:
            interval = float(interval_var.get())
        except ValueError:
            interval = 2.5

        if interval < 1.0:
            interval = 1.0
            interval_var.set(f"{interval:.1f}")

        status_label.config(
            text=f"Mode: Interval\nCapture: {selected}\nEvery {interval:.1f}s\nSaving to:\n{current_output_dir}"
        )

        screenshot_thread = threading.Thread(
            target=take_screenshots_interval,
            args=(current_output_dir, stop_event, interval, monitor_index, filename_prefix),
            daemon=True
        )
        screenshot_thread.start()

    else:
        # Event-driven (click/scroll/enter)
        try:
            cooldown_s = float(cooldown_var.get())
        except ValueError:
            cooldown_s = 0.20

        if cooldown_s < 0.0:
            cooldown_s = 0.0
            cooldown_var.set("0.20")

        status_label.config(
            text=f"Mode: Click/Scroll/Enter\nCapture: {selected}\nCooldown {cooldown_s:.2f}s\nSaving to:\n{current_output_dir}"
        )

        screenshot_thread = threading.Thread(
            target=take_screenshots_on_events,
            args=(current_output_dir, stop_event, monitor_index, filename_prefix, cooldown_s),
            daemon=True
        )
        screenshot_thread.start()

    start_button.config(state=tk.DISABLED)
    stop_button.config(state=tk.NORMAL)


def stop_capture():
    global is_capturing, stop_event

    if not is_capturing:
        return

    if stop_event is not None:
        stop_event.set()

    is_capturing = False
    status_label.config(text="Stopped.")
    start_button.config(state=tk.NORMAL)
    stop_button.config(state=tk.DISABLED)


def change_base_folder():
    global base_output_dir

    initial = base_output_dir if base_output_dir else get_default_base_dir()
    new_dir = filedialog.askdirectory(
        initialdir=initial,
        title="Select base folder for screenshots"
    )
    if new_dir:
        base_output_dir = new_dir
        save_dir_label.config(text=f"Save root:\n{base_output_dir}")
        if not is_capturing:
            status_label.config(text="Folder changed. Press Start to begin capturing.")


def open_base_folder():
    global base_output_dir

    if not base_output_dir:
        base_output_dir = get_default_base_dir()

    Path(base_output_dir).mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(base_output_dir)  # Windows
    except Exception as e:
        messagebox.showerror("Error", f"Could not open folder:\n{e}")


def on_close():
    global stop_event
    if stop_event is not None:
        stop_event.set()
    root.destroy()


# -----------------------------
# GUI setup
# -----------------------------
root = tk.Tk()
root.title("Screenshot Controller")
root.minsize(650, 420)

base_output_dir = get_default_base_dir()

status_label = tk.Label(root, text="Press Start to begin capturing.")
status_label.pack(pady=10)

# Save directory row
save_dir_frame = tk.Frame(root)
save_dir_frame.pack(pady=5, fill="x")

save_dir_label = tk.Label(save_dir_frame, text=f"Save root:\n{base_output_dir}", anchor="w", justify="left")
save_dir_label.grid(row=0, column=0, padx=5, sticky="w")

tk.Button(save_dir_frame, text="Change folder...", command=change_base_folder).grid(row=0, column=1, padx=5, sticky="e")
tk.Button(save_dir_frame, text="Open folder", command=open_base_folder).grid(row=0, column=2, padx=5, sticky="e")

# Mode
mode_frame = tk.Frame(root)
mode_frame.pack(pady=5)

tk.Label(mode_frame, text="Mode:").grid(row=0, column=0, padx=5, sticky="w")
mode_var = tk.StringVar(value="interval")
tk.Radiobutton(mode_frame, text="Interval", variable=mode_var, value="interval").grid(row=0, column=1, padx=5, sticky="w")
tk.Radiobutton(mode_frame, text="Click/Scroll/Enter", variable=mode_var, value="events").grid(row=0, column=2, padx=5, sticky="w")

# Interval (for interval mode)
interval_frame = tk.Frame(root)
interval_frame.pack(pady=5)

tk.Label(interval_frame, text="Interval (seconds, min 1.0, recommended 2.5):").grid(row=0, column=0, padx=5)
interval_var = tk.StringVar(value="2.5")
tk.Entry(interval_frame, width=7, textvariable=interval_var).grid(row=0, column=1, padx=5)

# Cooldown (for event mode)
cooldown_frame = tk.Frame(root)
cooldown_frame.pack(pady=5)

tk.Label(cooldown_frame, text="Event cooldown (seconds, prevents double shots):").grid(row=0, column=0, padx=5)
cooldown_var = tk.StringVar(value="0.20")
tk.Entry(cooldown_frame, width=7, textvariable=cooldown_var).grid(row=0, column=1, padx=5)

# Project name
name_frame = tk.Frame(root)
name_frame.pack(pady=5)

tk.Label(name_frame, text="Project Name (optional):").grid(row=0, column=0, padx=5)
name_var = tk.StringVar(value="")
tk.Entry(name_frame, width=30, textvariable=name_var).grid(row=0, column=1, padx=5)

# Monitor dropdown (from MSS)
monitor_frame = tk.Frame(root)
monitor_frame.pack(pady=5)

tk.Label(monitor_frame, text="Capture region:").grid(row=0, column=0, padx=5, sticky="w")

with mss.mss() as sct:
    mss_monitors = sct.monitors  # index 0 = all monitors, 1..N = each monitor dict

monitor_options = ["All monitors"]
for i in range(1, len(mss_monitors)):
    m = mss_monitors[i]
    monitor_options.append(f"Monitor {i} ({m['width']}x{m['height']} at {m['left']},{m['top']})")

monitor_var = tk.StringVar(value=monitor_options[0])
tk.OptionMenu(monitor_frame, monitor_var, *monitor_options).grid(row=0, column=1, padx=5, sticky="w")

# Buttons
button_frame = tk.Frame(root)
button_frame.pack(pady=12)

start_button = tk.Button(button_frame, text="Start", width=12, command=start_capture)
start_button.grid(row=0, column=0, padx=5)

stop_button = tk.Button(button_frame, text="Stop", width=12, command=stop_capture, state=tk.DISABLED)
stop_button.grid(row=0, column=1, padx=5)

root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()