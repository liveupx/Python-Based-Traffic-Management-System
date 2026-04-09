"""
Configuration for the Traffic Management System.
Override any default value by uncommenting and editing below.

Powered by Liveupx.com & xHost
"""

CONFIG = {
    # --- Signal Timing ---
    # "CONGESTION_THRESHOLD": 15,       # Queue length that triggers congestion alert
    # "MIN_GREEN_TIME": 10,             # Minimum green phase (seconds)
    # "MAX_GREEN_TIME": 60,             # Maximum green phase (seconds)
    # "BASE_GREEN_TIME": 30,            # Initial green time at startup
    # "YELLOW_DURATION": 5,             # Yellow phase duration (seconds)
    # "EMERGENCY_PRIORITY_TIME": 20,    # Emergency override duration (seconds)

    # --- Simulation ---
    # "TICK_INTERVAL": 1.0,             # Seconds per simulation tick
    # "MAX_VEHICLES_PER_TICK": 3,       # Max vehicles released per green tick
    # "VEHICLE_SPAWN_RATE": 0.55,       # Probability threshold for vehicle spawn (0-1)

    # --- GUI ---
    # "GUI_REFRESH_RATE": 500,          # Dashboard refresh rate (milliseconds)
    # "MAX_LOG_DISPLAY": 50,            # Max log entries shown in GUI
}
