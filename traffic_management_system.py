"""
===============================================================================
  PYTHON-BASED TRAFFIC MANAGEMENT SYSTEM
  Open Source Project — Powered by Liveupx.com & xHost
  GitHub: github.com/liveupx/Python-Based-Traffic-Management-System
===============================================================================

  A fully functional, adaptive traffic management system built in Python.
  Works on Windows, macOS, and Linux.

  Features:
    1. Real-time traffic signal control (simulation)
    2. Vehicle detection & counting (via OpenCV — optional)
    3. Adaptive signal timing based on traffic density
    4. Intersection management for multiple junctions
    5. Traffic data logging & analytics dashboard (Tkinter GUI)
    6. Emergency vehicle priority override
    7. Congestion detection & alerts
    8. CSV report generation

  Requirements:
    - Python 3.9+
    - Tkinter (bundled with Python on most platforms)
    - Optional: pip install opencv-python numpy matplotlib Pillow

  Usage:
    python traffic_management_system.py

  License: MIT
===============================================================================
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import time
import random
import csv
import os
import sys
import math
import platform
import datetime
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Callable

# ---------------------------------------------------------------------------
#  CONFIGURATION (can be overridden via config.py)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "CONGESTION_THRESHOLD": 15,
    "MIN_GREEN_TIME": 10,
    "MAX_GREEN_TIME": 60,
    "BASE_GREEN_TIME": 30,
    "YELLOW_DURATION": 5,
    "EMERGENCY_PRIORITY_TIME": 20,
    "TICK_INTERVAL": 1.0,        # seconds per simulation tick
    "GUI_REFRESH_RATE": 500,     # milliseconds
    "MAX_VEHICLES_PER_TICK": 3,  # max vehicles released per green tick
    "VEHICLE_SPAWN_RATE": 0.55,  # probability threshold for spawn
    "MAX_LOG_DISPLAY": 50,       # log entries shown in GUI
}

# Try to load user config
try:
    from config import CONFIG
    for k, v in CONFIG.items():
        DEFAULT_CONFIG[k] = v
except ImportError:
    pass

CFG = DEFAULT_CONFIG


# ---------------------------------------------------------------------------
#  ENUMS & DATA CLASSES
# ---------------------------------------------------------------------------

class SignalState(Enum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"


class Direction(Enum):
    NORTH = "North"
    SOUTH = "South"
    EAST = "East"
    WEST = "West"


class VehicleType(Enum):
    CAR = "Car"
    BUS = "Bus"
    TRUCK = "Truck"
    MOTORCYCLE = "Motorcycle"
    EMERGENCY = "Emergency"


@dataclass
class Vehicle:
    """Represents a single vehicle in the traffic system."""
    vehicle_id: int
    vehicle_type: VehicleType
    direction: Direction
    arrival_time: float
    wait_time: float = 0.0
    is_emergency: bool = False

    def __post_init__(self):
        if self.vehicle_type == VehicleType.EMERGENCY:
            self.is_emergency = True


@dataclass
class TrafficSignal:
    """Represents a single traffic signal for one direction."""
    direction: Direction
    state: SignalState = SignalState.RED
    green_duration: int = 30
    yellow_duration: int = 5
    red_duration: int = 30
    time_remaining: int = 30
    vehicles_passed: int = 0


@dataclass
class TrafficLane:
    """Manages the vehicle queue and statistics for one lane/direction."""
    direction: Direction
    queue: deque = field(default_factory=deque)
    density: float = 0.0
    total_vehicles_entered: int = 0
    total_vehicles_passed: int = 0
    avg_wait_time: float = 0.0
    _wait_times: list = field(default_factory=list)

    def add_vehicle(self, vehicle: Vehicle):
        """Add a vehicle to the back of the queue."""
        self.queue.append(vehicle)
        self.total_vehicles_entered += 1
        self._update_density()

    def release_vehicle(self) -> Optional[Vehicle]:
        """Release the front vehicle from the queue (FIFO)."""
        if self.queue:
            v = self.queue.popleft()
            v.wait_time = time.time() - v.arrival_time
            self._wait_times.append(v.wait_time)
            self.total_vehicles_passed += 1
            self._update_density()
            return v
        return None

    def has_emergency(self) -> bool:
        """Check if any emergency vehicle is waiting in this lane."""
        return any(v.is_emergency for v in self.queue)

    def _update_density(self):
        """Recalculate density and rolling average wait time."""
        self.density = len(self.queue)
        if self._wait_times:
            recent = self._wait_times[-50:]
            self.avg_wait_time = sum(recent) / len(recent)


@dataclass
class Intersection:
    """A four-way intersection with lanes, signals, and state tracking."""
    intersection_id: int
    name: str
    lanes: Dict[Direction, TrafficLane] = field(default_factory=dict)
    signals: Dict[Direction, TrafficSignal] = field(default_factory=dict)
    is_emergency_mode: bool = False
    total_congestion_events: int = 0

    def __post_init__(self):
        for d in Direction:
            self.lanes[d] = TrafficLane(direction=d)
            self.signals[d] = TrafficSignal(direction=d)


@dataclass
class LogEntry:
    """A single event log entry."""
    timestamp: str
    intersection: str
    direction: str
    event: str
    details: str


# ---------------------------------------------------------------------------
#  CORE ENGINE — Adaptive Traffic Controller
# ---------------------------------------------------------------------------

class AdaptiveTrafficController:
    """
    The brain of the traffic management system.

    Controls traffic signals adaptively based on real-time density,
    emergency vehicle detection, and congestion thresholds.

    Architecture:
        - Event-driven with publish/subscribe callbacks
        - Thread-safe simulation running in a daemon thread
        - Supports multiple independent intersections
    """

    def __init__(self):
        self.intersections: Dict[int, Intersection] = {}
        self.log: List[LogEntry] = []
        self.running = False
        self.cycle_count = 0
        self.vehicle_id_counter = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.callbacks: Dict[str, List[Callable]] = {
            "on_signal_change": [],
            "on_vehicle_pass": [],
            "on_congestion": [],
            "on_emergency": [],
            "on_log": [],
        }

    # --- Setup ---

    def add_intersection(self, name: str) -> Intersection:
        """Create and register a new intersection."""
        iid = len(self.intersections) + 1
        intersection = Intersection(intersection_id=iid, name=name)
        # Initial state: North/South gets GREEN
        intersection.signals[Direction.NORTH].state = SignalState.GREEN
        intersection.signals[Direction.NORTH].time_remaining = CFG["BASE_GREEN_TIME"]
        intersection.signals[Direction.SOUTH].state = SignalState.GREEN
        intersection.signals[Direction.SOUTH].time_remaining = CFG["BASE_GREEN_TIME"]
        self.intersections[iid] = intersection
        self._add_log(name, "-", "INIT", f"Intersection '{name}' created.")
        return intersection

    # --- Event system ---

    def on(self, event: str, callback: Callable):
        """Register a callback for a specific event type."""
        if event in self.callbacks:
            self.callbacks[event].append(callback)

    def _emit(self, event: str, *args):
        """Fire all registered callbacks for an event."""
        for cb in self.callbacks.get(event, []):
            try:
                cb(*args)
            except Exception:
                pass

    def _add_log(self, intersection: str, direction: str, event: str, details: str):
        """Append a log entry (thread-safe)."""
        entry = LogEntry(
            timestamp=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            intersection=intersection,
            direction=direction,
            event=event,
            details=details,
        )
        with self._lock:
            self.log.append(entry)
        self._emit("on_log", entry)

    # --- Vehicle generation (simulation) ---

    def _generate_vehicles(self, intersection: Intersection):
        """Randomly generate vehicles to simulate real traffic flow."""
        for direction in Direction:
            rate = random.uniform(0, 1)
            if rate > CFG["VEHICLE_SPAWN_RATE"]:
                self.vehicle_id_counter += 1
                vtype = random.choices(
                    list(VehicleType),
                    weights=[60, 10, 10, 18, 2],  # Car, Bus, Truck, Moto, Emergency
                    k=1
                )[0]
                v = Vehicle(
                    vehicle_id=self.vehicle_id_counter,
                    vehicle_type=vtype,
                    direction=direction,
                    arrival_time=time.time(),
                )
                intersection.lanes[direction].add_vehicle(v)

    # --- Adaptive timing calculation ---

    def _compute_green_time(self, intersection: Intersection, direction: Direction) -> int:
        """
        Calculate green phase duration proportional to queue density.

        Formula:
            green = MIN + (density / THRESHOLD) * (MAX - MIN)
            clamped to [MIN_GREEN_TIME, MAX_GREEN_TIME]
        """
        lane = intersection.lanes[direction]
        density = len(lane.queue)
        green = CFG["MIN_GREEN_TIME"] + int(
            (density / max(CFG["CONGESTION_THRESHOLD"], 1)) *
            (CFG["MAX_GREEN_TIME"] - CFG["MIN_GREEN_TIME"])
        )
        return max(CFG["MIN_GREEN_TIME"], min(CFG["MAX_GREEN_TIME"], green))

    # --- Emergency handling ---

    def _check_emergency(self, intersection: Intersection):
        """Scan all lanes for emergency vehicles and override signals."""
        for direction in Direction:
            if intersection.lanes[direction].has_emergency():
                if not intersection.is_emergency_mode:
                    intersection.is_emergency_mode = True
                    self._add_log(
                        intersection.name, direction.value,
                        "EMERGENCY",
                        "Emergency vehicle detected — overriding signals."
                    )
                    self._emit("on_emergency", intersection, direction)
                    # Set this direction to GREEN, all others RED
                    for d in Direction:
                        if d == direction or self._is_complementary(d, direction):
                            intersection.signals[d].state = SignalState.GREEN
                            intersection.signals[d].time_remaining = CFG["EMERGENCY_PRIORITY_TIME"]
                        else:
                            intersection.signals[d].state = SignalState.RED
                            intersection.signals[d].time_remaining = CFG["EMERGENCY_PRIORITY_TIME"]
                return
        intersection.is_emergency_mode = False

    @staticmethod
    def _is_complementary(d1: Direction, d2: Direction) -> bool:
        """Check if two directions are complementary (can have green simultaneously)."""
        pairs = {Direction.NORTH: Direction.SOUTH, Direction.EAST: Direction.WEST}
        pairs.update({v: k for k, v in pairs.items()})
        return pairs.get(d1) == d2

    # --- Congestion detection ---

    def _check_congestion(self, intersection: Intersection):
        """Alert when any lane exceeds the congestion threshold."""
        for direction in Direction:
            q = len(intersection.lanes[direction].queue)
            if q >= CFG["CONGESTION_THRESHOLD"]:
                intersection.total_congestion_events += 1
                self._add_log(
                    intersection.name, direction.value,
                    "CONGESTION",
                    f"Queue length {q} exceeds threshold {CFG['CONGESTION_THRESHOLD']}."
                )
                self._emit("on_congestion", intersection, direction, q)

    # --- Signal cycle management ---

    def _advance_signals(self, intersection: Intersection):
        """Tick down timers and transition signal states."""
        if intersection.is_emergency_mode:
            return

        for d in Direction:
            sig = intersection.signals[d]
            sig.time_remaining -= 1

            if sig.time_remaining <= 0:
                if sig.state == SignalState.GREEN:
                    sig.state = SignalState.YELLOW
                    sig.time_remaining = CFG["YELLOW_DURATION"]
                    self._emit("on_signal_change", intersection, d, SignalState.YELLOW)
                elif sig.state == SignalState.YELLOW:
                    sig.state = SignalState.RED
                    sig.time_remaining = sig.red_duration
                    self._emit("on_signal_change", intersection, d, SignalState.RED)
                elif sig.state == SignalState.RED:
                    pass  # handled by cycle scheduler

        # Check if all signals are RED — start a new green phase
        all_red = all(
            intersection.signals[d].state == SignalState.RED
            for d in Direction
        )
        if all_red:
            self._start_next_green_phase(intersection)

    def _start_next_green_phase(self, intersection: Intersection):
        """Pick the complementary pair with highest density for the next green."""
        pairs = [(Direction.NORTH, Direction.SOUTH), (Direction.EAST, Direction.WEST)]
        densities = []
        for p in pairs:
            total = sum(len(intersection.lanes[d].queue) for d in p)
            densities.append((total, p))
        densities.sort(key=lambda x: x[0], reverse=True)
        chosen_pair = densities[0][1]

        green_time = max(
            self._compute_green_time(intersection, chosen_pair[0]),
            self._compute_green_time(intersection, chosen_pair[1]),
        )
        for d in chosen_pair:
            intersection.signals[d].state = SignalState.GREEN
            intersection.signals[d].time_remaining = green_time
            self._emit("on_signal_change", intersection, d, SignalState.GREEN)

        other_pair = densities[1][1]
        red_time = green_time + CFG["YELLOW_DURATION"]
        for d in other_pair:
            intersection.signals[d].state = SignalState.RED
            intersection.signals[d].time_remaining = red_time

        self.cycle_count += 1

    # --- Vehicle release ---

    def _process_vehicle_flow(self, intersection: Intersection):
        """Release vehicles from green lanes."""
        for d in Direction:
            if intersection.signals[d].state == SignalState.GREEN:
                lane = intersection.lanes[d]
                for _ in range(random.randint(1, CFG["MAX_VEHICLES_PER_TICK"])):
                    v = lane.release_vehicle()
                    if v:
                        intersection.signals[d].vehicles_passed += 1
                        self._emit("on_vehicle_pass", intersection, d, v)

    # --- Main simulation loop ---

    def start(self):
        """Start the simulation engine in a background thread."""
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the simulation engine."""
        self.running = False

    def _run_loop(self):
        """Core loop — runs every TICK_INTERVAL seconds."""
        while self.running:
            for iid, intersection in self.intersections.items():
                self._generate_vehicles(intersection)
                self._check_emergency(intersection)
                self._check_congestion(intersection)
                self._advance_signals(intersection)
                self._process_vehicle_flow(intersection)
            time.sleep(CFG["TICK_INTERVAL"])

    # --- Reporting ---

    def export_log_csv(self, filepath: str):
        """Write all log entries to a CSV file."""
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Intersection", "Direction", "Event", "Details"])
            for entry in self.log:
                writer.writerow([
                    entry.timestamp, entry.intersection,
                    entry.direction, entry.event, entry.details
                ])

    def get_statistics(self, intersection: Intersection) -> dict:
        """Get current stats for all directions of an intersection."""
        stats = {}
        for d in Direction:
            lane = intersection.lanes[d]
            stats[d.value] = {
                "queue_length": len(lane.queue),
                "total_entered": lane.total_vehicles_entered,
                "total_passed": lane.total_vehicles_passed,
                "avg_wait": round(lane.avg_wait_time, 2),
                "signal": intersection.signals[d].state.value,
                "timer": intersection.signals[d].time_remaining,
            }
        return stats


# ---------------------------------------------------------------------------
#  GUI — Tkinter Dashboard
# ---------------------------------------------------------------------------

class TrafficDashboard:
    """
    Full-featured Tkinter GUI for monitoring and controlling the system.

    Features:
        - Real-time intersection visualization on Canvas
        - Live statistics panel with per-direction metrics
        - Event log with colour-coded entries
        - Start/Stop, Emergency trigger, CSV export controls
        - Multi-intersection selector
    """

    # Colour palette (dark theme)
    BG = "#1a1a2e"
    PANEL_BG = "#16213e"
    ACCENT = "#0f3460"
    TEXT = "#e0e0e0"
    GREEN = "#27ae60"
    RED = "#e74c3c"
    YELLOW = "#f39c12"
    BLUE = "#3498db"
    GREY = "#7f8c8d"

    SIGNAL_COLORS = {
        SignalState.RED: "#e74c3c",
        SignalState.YELLOW: "#f39c12",
        SignalState.GREEN: "#27ae60",
    }

    def __init__(self):
        self.controller = AdaptiveTrafficController()
        self.root = tk.Tk()
        self.root.title("Traffic Management System — Powered by Liveupx.com & xHost")
        self.root.geometry("1280x780")
        self.root.configure(bg=self.BG)
        self.root.minsize(1100, 700)

        # Platform-specific tweaks
        if platform.system() == "Darwin":
            # macOS: use native appearance
            try:
                self.root.tk.call("tk::unsupported::MacWindowStyle",
                                  "style", self.root._w, "moveableModal", "")
            except tk.TclError:
                pass

        # Add default intersections
        self.intersection = self.controller.add_intersection("Main Junction A")
        self.intersection2 = self.controller.add_intersection("Highway Cross B")
        self.intersection3 = self.controller.add_intersection("College Road C")

        self.selected_intersection = self.intersection

        self._build_ui()
        self._register_callbacks()
        self._update_loop()

    # --- UI Construction ---

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self.root, bg=self.ACCENT, height=54)
        top.pack(fill="x")
        top.pack_propagate(False)

        tk.Label(
            top, text="  TRAFFIC MANAGEMENT SYSTEM",
            bg=self.ACCENT, fg="#ffffff", font=("Segoe UI", 16, "bold"),
            anchor="w"
        ).pack(side="left", padx=10, pady=10)

        right_brand = tk.Frame(top, bg=self.ACCENT)
        right_brand.pack(side="right", padx=10)
        tk.Label(
            right_brand, text="Powered by ",
            bg=self.ACCENT, fg=self.GREY, font=("Segoe UI", 9)
        ).pack(side="left")
        tk.Label(
            right_brand, text="Liveupx.com",
            bg=self.ACCENT, fg=self.BLUE, font=("Segoe UI", 10, "bold")
        ).pack(side="left")
        tk.Label(
            right_brand, text=" & ",
            bg=self.ACCENT, fg=self.GREY, font=("Segoe UI", 9)
        ).pack(side="left")
        tk.Label(
            right_brand, text="xHost",
            bg=self.ACCENT, fg="#e67e22", font=("Segoe UI", 10, "bold")
        ).pack(side="left")

        # Main content area
        main = tk.Frame(self.root, bg=self.BG)
        main.pack(fill="both", expand=True, padx=10, pady=10)

        # Left: Intersection view
        left = tk.Frame(main, bg=self.BG)
        left.pack(side="left", fill="both", expand=True)
        self._build_intersection_view(left)

        # Right: Controls + Log
        right = tk.Frame(main, bg=self.BG, width=440)
        right.pack(side="right", fill="both", expand=False, padx=(10, 0))
        right.pack_propagate(False)
        self._build_controls(right)
        self._build_stats_panel(right)
        self._build_log_panel(right)

        # Bottom bar
        bottom = tk.Frame(self.root, bg=self.ACCENT, height=28)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        tk.Label(
            bottom,
            text=f"  Platform: {platform.system()} {platform.release()}  |  "
                 f"Python {platform.python_version()}  |  "
                 f"github.com/liveupx/Python-Based-Traffic-Management-System",
            bg=self.ACCENT, fg=self.GREY, font=("Consolas", 8), anchor="w"
        ).pack(side="left", padx=6, pady=4)

    def _build_intersection_view(self, parent):
        frame = tk.LabelFrame(
            parent, text=" Intersection View ", bg=self.PANEL_BG,
            fg=self.TEXT, font=("Segoe UI", 11, "bold"), bd=1, relief="groove",
            labelanchor="n"
        )
        frame.pack(fill="both", expand=True)

        # Intersection selector
        sel_frame = tk.Frame(frame, bg=self.PANEL_BG)
        sel_frame.pack(fill="x", padx=10, pady=5)
        tk.Label(sel_frame, text="Intersection:", bg=self.PANEL_BG, fg=self.TEXT,
                 font=("Segoe UI", 10)).pack(side="left")
        self.intersection_var = tk.StringVar(value=self.intersection.name)
        names = [i.name for i in self.controller.intersections.values()]
        combo = ttk.Combobox(sel_frame, textvariable=self.intersection_var,
                             values=names, state="readonly", width=28)
        combo.pack(side="left", padx=8)
        combo.bind("<<ComboboxSelected>>", self._on_intersection_change)

        # Status indicator
        self.status_label = tk.Label(
            sel_frame, text="  STOPPED", bg=self.PANEL_BG,
            fg=self.RED, font=("Consolas", 10, "bold")
        )
        self.status_label.pack(side="right", padx=10)

        # Canvas for intersection drawing
        self.canvas = tk.Canvas(frame, bg="#0d1117", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=10)

    def _build_controls(self, parent):
        frame = tk.LabelFrame(
            parent, text=" Controls ", bg=self.PANEL_BG,
            fg=self.TEXT, font=("Segoe UI", 11, "bold"), bd=1, relief="groove"
        )
        frame.pack(fill="x", pady=(0, 8))

        btn_frame = tk.Frame(frame, bg=self.PANEL_BG)
        btn_frame.pack(fill="x", padx=10, pady=8)

        self.start_btn = tk.Button(
            btn_frame, text="▶  Start", bg=self.GREEN, fg="white",
            font=("Segoe UI", 10, "bold"), relief="flat", padx=14, pady=4,
            cursor="hand2", command=self._start
        )
        self.start_btn.pack(side="left", padx=(0, 5))

        self.stop_btn = tk.Button(
            btn_frame, text="■  Stop", bg=self.RED, fg="white",
            font=("Segoe UI", 10, "bold"), relief="flat", padx=14, pady=4,
            cursor="hand2", command=self._stop, state="disabled"
        )
        self.stop_btn.pack(side="left", padx=(0, 5))

        tk.Button(
            btn_frame, text="⚠  Emergency", bg=self.YELLOW, fg="black",
            font=("Segoe UI", 10, "bold"), relief="flat", padx=14, pady=4,
            cursor="hand2", command=self._trigger_emergency
        ).pack(side="left", padx=(0, 5))

        tk.Button(
            btn_frame, text="📄 Export CSV", bg=self.ACCENT, fg="white",
            font=("Segoe UI", 10), relief="flat", padx=14, pady=4,
            cursor="hand2", command=self._export_csv
        ).pack(side="left")

    def _build_stats_panel(self, parent):
        frame = tk.LabelFrame(
            parent, text=" Live Statistics ", bg=self.PANEL_BG,
            fg=self.TEXT, font=("Segoe UI", 11, "bold"), bd=1, relief="groove"
        )
        frame.pack(fill="x", pady=(0, 8))

        self.stats_labels = {}
        for d in Direction:
            row = tk.Frame(frame, bg=self.PANEL_BG)
            row.pack(fill="x", padx=10, pady=3)

            # Signal indicator dot
            self.signal_dots = {}
            dot = tk.Canvas(row, width=12, height=12, bg=self.PANEL_BG, highlightthickness=0)
            dot.pack(side="left", padx=(0, 4))
            dot.create_oval(1, 1, 11, 11, fill=self.RED, outline="", tags="dot")
            self.signal_dots = getattr(self, '_signal_dots', {})
            self._signal_dots = getattr(self, '_signal_dots', {})
            self._signal_dots[d] = dot

            tk.Label(row, text=f"{d.value}:", bg=self.PANEL_BG, fg=self.BLUE,
                     font=("Segoe UI", 10, "bold"), width=7, anchor="w").pack(side="left")
            lbl = tk.Label(row, text="Queue: 0 | Passed: 0 | Wait: 0.0s",
                           bg=self.PANEL_BG, fg=self.TEXT, font=("Consolas", 9),
                           anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            self.stats_labels[d] = lbl

        self.cycle_label = tk.Label(frame, text="Cycle: 0  |  Congestion events: 0",
                                     bg=self.PANEL_BG, fg=self.GREY, font=("Segoe UI", 9))
        self.cycle_label.pack(padx=10, pady=(2, 6), anchor="w")

    def _build_log_panel(self, parent):
        frame = tk.LabelFrame(
            parent, text=" Event Log ", bg=self.PANEL_BG,
            fg=self.TEXT, font=("Segoe UI", 11, "bold"), bd=1, relief="groove"
        )
        frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            frame, bg="#0d1117", fg=self.TEXT, font=("Consolas", 9),
            wrap="word", bd=0, state="disabled", height=10
        )
        scrollbar = tk.Scrollbar(frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    # --- Callbacks ---

    def _register_callbacks(self):
        self.controller.on("on_log", lambda e: None)  # handled in update loop

    def _on_intersection_change(self, event):
        name = self.intersection_var.get()
        for i in self.controller.intersections.values():
            if i.name == name:
                self.selected_intersection = i
                break

    # --- Actions ---

    def _start(self):
        self.controller.start()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_label.config(text="  RUNNING", fg=self.GREEN)

    def _stop(self):
        self.controller.stop()
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="  STOPPED", fg=self.RED)

    def _trigger_emergency(self):
        directions = list(Direction)
        d = random.choice(directions)
        self.controller.vehicle_id_counter += 1
        v = Vehicle(
            vehicle_id=self.controller.vehicle_id_counter,
            vehicle_type=VehicleType.EMERGENCY,
            direction=d,
            arrival_time=time.time(),
        )
        self.selected_intersection.lanes[d].add_vehicle(v)
        self.controller._add_log(
            self.selected_intersection.name, d.value,
            "EMERGENCY", "Manual emergency vehicle injected."
        )

    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            title="Export Traffic Log"
        )
        if path:
            self.controller.export_log_csv(path)
            messagebox.showinfo("Export Complete", f"Log exported to:\n{path}")

    # --- Drawing ---

    def _draw_intersection(self):
        c = self.canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 100 or h < 100:
            return

        cx, cy = w // 2, h // 2
        road_w = 70
        inter = self.selected_intersection

        # Roads
        c.create_rectangle(cx - road_w, 0, cx + road_w, h, fill="#2c3e50", outline="")
        c.create_rectangle(0, cy - road_w, w, cy + road_w, fill="#2c3e50", outline="")

        # Road center dashes
        for offset in range(0, max(h, w), 24):
            # Vertical
            if offset < cy - road_w or offset > cy + road_w:
                c.create_line(cx, offset, cx, min(offset + 12, h), fill="#f1c40f",
                              width=2, dash=(1,))
            # Horizontal
            if offset < cx - road_w or offset > cx + road_w:
                c.create_line(offset, cy, min(offset + 12, w), cy, fill="#f1c40f",
                              width=2, dash=(1,))

        # Road edge lines
        c.create_line(cx - road_w, 0, cx - road_w, cy - road_w, fill="#95a5a6", width=1)
        c.create_line(cx + road_w, 0, cx + road_w, cy - road_w, fill="#95a5a6", width=1)
        c.create_line(cx - road_w, cy + road_w, cx - road_w, h, fill="#95a5a6", width=1)
        c.create_line(cx + road_w, cy + road_w, cx + road_w, h, fill="#95a5a6", width=1)
        c.create_line(0, cy - road_w, cx - road_w, cy - road_w, fill="#95a5a6", width=1)
        c.create_line(0, cy + road_w, cx - road_w, cy + road_w, fill="#95a5a6", width=1)
        c.create_line(cx + road_w, cy - road_w, w, cy - road_w, fill="#95a5a6", width=1)
        c.create_line(cx + road_w, cy + road_w, w, cy + road_w, fill="#95a5a6", width=1)

        # Center intersection box
        c.create_rectangle(cx - road_w, cy - road_w, cx + road_w, cy + road_w,
                           fill="#34495e", outline="#7f8c8d", width=1)

        # Crosswalk stripes
        for i in range(-road_w + 8, road_w - 4, 12):
            c.create_line(cx + i, cy - road_w - 8, cx + i, cy - road_w - 2,
                          fill="white", width=3)
            c.create_line(cx + i, cy + road_w + 2, cx + i, cy + road_w + 8,
                          fill="white", width=3)
            c.create_line(cx - road_w - 8, cy + i, cx - road_w - 2, cy + i,
                          fill="white", width=3)
            c.create_line(cx + road_w + 2, cy + i, cx + road_w + 8, cy + i,
                          fill="white", width=3)

        # Signals & Queues
        signal_positions = {
            Direction.NORTH: (cx + road_w + 22, cy - road_w - 22),
            Direction.SOUTH: (cx - road_w - 22, cy + road_w + 22),
            Direction.EAST: (cx + road_w + 22, cy + road_w + 22),
            Direction.WEST: (cx - road_w - 22, cy - road_w - 22),
        }

        for d, (sx, sy) in signal_positions.items():
            sig = inter.signals[d]
            color = self.SIGNAL_COLORS[sig.state]

            # Signal housing
            c.create_rectangle(sx - 14, sy - 22, sx + 14, sy + 22,
                               fill="#2c3e50", outline="#95a5a6", width=1)
            # Three lights
            for li, (lc, state) in enumerate(
                [(self.RED, SignalState.RED),
                 (self.YELLOW, SignalState.YELLOW),
                 ("#27ae60", SignalState.GREEN)]
            ):
                ly = sy - 14 + li * 14
                fill = lc if sig.state == state else "#1a1a1a"
                c.create_oval(sx - 5, ly - 5, sx + 5, ly + 5, fill=fill, outline="#555")

            # Timer
            c.create_text(sx, sy + 32, text=f"{sig.time_remaining}s",
                          fill=color, font=("Consolas", 9, "bold"))

            # Direction label
            label_pos = {
                Direction.NORTH: (cx, 20),
                Direction.SOUTH: (cx, h - 16),
                Direction.EAST: (w - 30, cy),
                Direction.WEST: (30, cy),
            }
            lx, ly = label_pos[d]
            c.create_text(lx, ly, text=d.value.upper(), fill=self.BLUE,
                          font=("Segoe UI", 10, "bold"))

            # Vehicle queue visualization
            lane = inter.lanes[d]
            q_len = min(len(lane.queue), 10)
            for i in range(q_len):
                if d == Direction.NORTH:
                    vx, vy = cx + 25, cy - road_w - 50 - i * 16
                elif d == Direction.SOUTH:
                    vx, vy = cx - 25, cy + road_w + 50 + i * 16
                elif d == Direction.EAST:
                    vx, vy = cx + road_w + 50 + i * 20, cy + 25
                else:
                    vx, vy = cx - road_w - 50 - i * 20, cy - 25

                queue_list = list(lane.queue)
                has_em = i < len(queue_list) and queue_list[i].is_emergency
                vc = "#e74c3c" if has_em else "#3498db"
                c.create_rectangle(vx - 7, vy - 5, vx + 7, vy + 5,
                                   fill=vc, outline="#1a1a2e", width=1)

            # Queue count badge
            badge_pos = {
                Direction.NORTH: (cx + 55, cy - road_w - 30),
                Direction.SOUTH: (cx - 55, cy + road_w + 30),
                Direction.EAST: (cx + road_w + 90, cy + 45),
                Direction.WEST: (cx - road_w - 90, cy - 45),
            }
            bx, by = badge_pos[d]
            q_count = len(lane.queue)
            badge_color = self.RED if q_count >= CFG["CONGESTION_THRESHOLD"] else "#555"
            c.create_rectangle(bx - 18, by - 9, bx + 18, by + 9,
                               fill=badge_color, outline="")
            c.create_text(bx, by, text=f"Q:{q_count}", fill="white",
                          font=("Consolas", 8, "bold"))

        # Title
        c.create_text(w // 2, 40, text=inter.name, fill="white",
                      font=("Segoe UI", 14, "bold"))

        # Emergency mode overlay
        if inter.is_emergency_mode:
            c.create_rectangle(cx - 80, cy - 14, cx + 80, cy + 14,
                               fill="#e74c3c", outline="#c0392b", width=2)
            c.create_text(cx, cy, text="EMERGENCY MODE", fill="white",
                          font=("Segoe UI", 11, "bold"))

    # --- Periodic update ---

    def _update_loop(self):
        self._draw_intersection()
        self._update_stats()
        self._update_log_display()
        self.root.after(CFG["GUI_REFRESH_RATE"], self._update_loop)

    def _update_stats(self):
        inter = self.selected_intersection
        stats = self.controller.get_statistics(inter)
        for d in Direction:
            s = stats[d.value]
            self.stats_labels[d].config(
                text=f"Queue: {s['queue_length']:>3} | Passed: {s['total_passed']:>4} "
                     f"| Wait: {s['avg_wait']:.1f}s | [{s['signal']} {s['timer']}s]"
            )
            # Update signal dot
            dot_color = self.SIGNAL_COLORS.get(
                inter.signals[d].state, self.RED
            )
            if d in self._signal_dots:
                self._signal_dots[d].delete("dot")
                self._signal_dots[d].create_oval(1, 1, 11, 11, fill=dot_color,
                                                  outline="", tags="dot")

        self.cycle_label.config(
            text=f"Cycle: {self.controller.cycle_count}  |  "
                 f"Congestion events: {inter.total_congestion_events}"
        )

    def _update_log_display(self):
        with self.controller._lock:
            entries = list(self.controller.log[-200:])
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        for e in reversed(entries[-CFG["MAX_LOG_DISPLAY"]:]):
            line = (f"[{e.timestamp}] {e.intersection} | "
                    f"{e.direction} | {e.event}: {e.details}\n")
            self.log_text.insert("end", line, e.event)
        self.log_text.tag_configure("EMERGENCY", foreground=self.RED)
        self.log_text.tag_configure("CONGESTION", foreground=self.YELLOW)
        self.log_text.tag_configure("INIT", foreground=self.BLUE)
        self.log_text.config(state="disabled")

    # --- Run ---

    def run(self):
        """Launch the dashboard."""
        self.root.mainloop()


# ---------------------------------------------------------------------------
#  ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 65)
    print("  Python-Based Traffic Management System")
    print("  Powered by Liveupx.com & xHost")
    print(f"  Platform: {platform.system()} | Python {platform.python_version()}")
    print("  Starting GUI Dashboard...")
    print("=" * 65)
    app = TrafficDashboard()
    app.run()
