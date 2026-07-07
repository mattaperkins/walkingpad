import asyncio
import csv
import datetime as dt
import logging
import os
import threading
import webbrowser
from pathlib import Path
from threading import Timer
import time
from collections import deque

from bleak import BleakScanner
from flask import Flask, render_template, redirect, url_for, jsonify, make_response, request
from ph4_walkingpad.pad import Controller, WalkingPad

# ── Logging Setup ────────────────────────────────────────────────────────
# All print() statements will be replaced with this logging configuration.
# It provides timed, leveled output. Set level=logging.DEBUG to see verbose messages.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

# ── Conversion constants ─────────────────────────────────────────────────
KJ_PER_KM = 247  # rough kilojoules per kilometer

# Speed control constants
MAX_SPEED_KMH = 6.0
MIN_SPEED_KMH = 1.0
SPEED_STEP = 0.6  # Speed change per button press in km/h
SLOW_WALK_SPEED_KMH = 4.5



def kilojoule_estimate(kilometers: float) -> float:
    return KJ_PER_KM * kilometers

# In app.py
def format_seconds_to_hms(total_seconds):
    """Converts total seconds to H:MM:SS string format."""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours}:{minutes:02}:{seconds:02}"


# ── Flask & global state ────────────────────────────────────────────────
app = Flask(__name__)
HISTORY_PATH = Path.home() / "history.csv"

connected = connecting = connection_failed = False
ble_loop: asyncio.AbstractEventLoop | None = None
controller: Controller | None = None
_pad_address: str | None = None
_auto_pause_grace_until = 0
speed_history = deque(maxlen=15)

session_active = belt_running = False
resume_speed_kmh = 2.0  # default if none yet

current_speed_kmh = current_distance_km = 0.0
current_steps = 0
current_kilojoules = 0.0
current_session_active_seconds = 0 
session_started_at: dt.datetime | None = None
session_min_speed_kmh: float | None = None
session_max_speed_kmh: float | None = None

_last_dev_dist = _last_dev_steps = 0


# ── Context processor so templates always know flags ────────────────────
@app.context_processor
def inject_flags():
    return dict(connected=connected, connecting=connecting, connection_failed=connection_failed)


# ── BLE helpers ─────────────────────────────────────────────────────────
def _register_disconnect_callback():
    """Register a disconnect callback across Bleak API versions."""
    if not controller or not getattr(controller, "client", None):
        return

    client = controller.client

    if hasattr(client, "set_disconnected_callback"):
        client.set_disconnected_callback(_handle_disconnect)
        return

    backend = getattr(client, "_backend", None)
    if hasattr(backend, "set_disconnected_callback"):
        backend.set_disconnected_callback(lambda: _handle_disconnect(client))
        return

    logging.warning("Bleak client does not support disconnected callbacks.")


async def _connect_to_pad() -> bool:
    global controller, _pad_address
    dev = None

    if _pad_address:
        logging.info(f"Attempting to connect to known address: {_pad_address}")
        try:
            dev = await BleakScanner.find_device_by_address(_pad_address, timeout=5)
        except Exception as exc:
            logging.warning(f"Failed to find device by address: {exc}")
            dev = None

    if not dev:
        logging.info("Scanning for device by name 'WalkingPad'...")
        try:
            dev = await BleakScanner.find_device_by_name("WalkingPad", timeout=10)
        except Exception as exc:
            logging.warning(f"Failed to find device by name: {exc}")
            dev = None

    if not dev:
        logging.error("Could not find WalkingPad. Ensure it is on and in range.")
        _pad_address = None
        return False

    _pad_address = dev.address
    logging.info(f"Device found! Address: {_pad_address}")

    controller = Controller()
    await controller.run(dev.address)

    _register_disconnect_callback()

    await controller.switch_mode(WalkingPad.MODE_MANUAL)

    def _status_cb(_sender, st):
        try:
            if isinstance(st, dict):
                dist = st.get("dist", 0)
                steps = st.get("steps", 0)
                speed = st.get("speed", 0)
            else:
                dist = getattr(st, "dist", 0)
                steps = getattr(st, "steps", 0)
                speed = getattr(st, "speed", 0)
            process_status_packet(dist, steps, speed)
            logging.debug(f"Push d={dist} s={steps} v={speed}")
        except Exception as exc:
            logging.warning(f"status_cb error: {exc}")

    controller.on_cur_status_received = _status_cb

    if hasattr(controller, "enable_notifications"):
        try:
            await controller.enable_notifications()
        except Exception as exc:
            logging.warning(f"enable_notifications failed: {exc}")
    return True


def process_status_packet(dev_dist, dev_steps, dev_speed):
    """Update cumulative stats from raw values AND handle auto-pause."""
    global belt_running, resume_speed_kmh, _auto_pause_grace_until
    global current_speed_kmh, current_distance_km, current_steps, current_kilojoules
    global session_min_speed_kmh, session_max_speed_kmh
    global _last_dev_dist, _last_dev_steps

    new_reported_speed_kmh = dev_speed / 10.0

    # Continuously populate the speed history with stable, non-zero speeds.
    if belt_running and new_reported_speed_kmh > MIN_SPEED_KMH:
        speed_history.append(new_reported_speed_kmh)

    if session_active and new_reported_speed_kmh > 0:
        if session_min_speed_kmh is None or new_reported_speed_kmh < session_min_speed_kmh:
            session_min_speed_kmh = new_reported_speed_kmh
        if session_max_speed_kmh is None or new_reported_speed_kmh > session_max_speed_kmh:
            session_max_speed_kmh = new_reported_speed_kmh

    # AUTO-PAUSE LOGIC
    if time.time() > _auto_pause_grace_until:
        if belt_running and new_reported_speed_kmh == 0 and current_speed_kmh > 0:
            logging.info("Belt has stopped unexpectedly. Auto-pausing session.")
            
            # Use the OLDEST speed from history to ignore the deceleration phase.
            if speed_history:
                resume_speed_kmh = speed_history[0] # Use the first (oldest) item
            else:
                # Fallback if pause happens too quickly after starting
                resume_speed_kmh = MIN_SPEED_KMH

            belt_running = False

    if dev_dist < _last_dev_dist:
        _last_dev_dist = 0
    distance_delta_km = (dev_dist - _last_dev_dist) / 100.0
    _last_dev_dist = dev_dist

    if dev_steps < _last_dev_steps:
        _last_dev_steps = 0
    steps_delta = dev_steps - _last_dev_steps
    _last_dev_steps = dev_steps

    if session_active:
        current_distance_km += distance_delta_km
        current_steps += steps_delta
        current_kilojoules = kilojoule_estimate(current_distance_km)

    current_speed_kmh = new_reported_speed_kmh


async def _stats_monitor():
    """Active monitor: explicitly request a status packet every second."""
    global current_session_active_seconds
    logging.info("Stats monitor started")
    while belt_running:
        
        if belt_running: # Double check, as belt_running can change between await calls
            current_session_active_seconds += 1
        
        try:
            status = await controller.ask_stats()
            if status:
                if isinstance(status, dict):
                    dist = status.get("dist", 0)
                    steps = status.get("steps", 0)
                    speed = status.get("speed", 0)
                else:
                    dist = getattr(status, "dist", 0)
                    steps = getattr(status, "steps", 0)
                    speed = getattr(status, "speed", 0)
                process_status_packet(dist, steps, speed)
                logging.debug(f"Poll {status}")
        except Exception as exc:
            logging.warning(f"ask_stats error: {exc}")
        await asyncio.sleep(1)


def _ble_thread():
    global connected, connecting, connection_failed, ble_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ble_loop = loop

    if not loop.run_until_complete(_connect_to_pad()):
        connecting = False
        connection_failed = True
        return

    connected = True
    connecting = False
    try:
        loop.run_forever()
    finally:
        connected = False
        loop.close()


def _start_ble_thread():
    global connecting, connection_failed
    if connected or connecting:
        return
    connecting = True
    connection_failed = False
    threading.Thread(target=_ble_thread, daemon=True).start()

def _handle_disconnect(client):
    """Callback function to handle unexpected disconnections."""
    global connected, belt_running, connecting, connection_failed
    if connected: # Only log if we thought we were connected
        logging.warning("Device has disconnected unexpectedly.")
    connected = False
    belt_running = False
    connecting = False
    connection_failed = True

# ── Flask routes ────────────────────────────────────────────────────────
@app.route("/")
def root():
    if not connected:
        return render_template("connecting.html") #

    time_active_display = "0:00:00" # Default for start/paused if not running
    if session_active: # Only calculate if a session is or was active
        time_active_display = format_seconds_to_hms(current_session_active_seconds)

    if not session_active:
        # For start_session, always show 0 time initially
        return render_template("start_session.html", time_active="0:00:00")

    template = "active_session.html" if belt_running else "paused_session.html"

    return render_template(
        template,
        speed=current_speed_kmh,
        distance=current_distance_km,
        steps=current_steps,
        kilojoules=current_kilojoules,
        time_active=time_active_display 
    )


@app.route("/reconnect", endpoint="reconnect")
@app.route("/manual_reconnect", endpoint="manual_reconnect")
def reconnect():
    if not connected and not connecting:
        _start_ble_thread()
    return redirect(url_for("root"))


def load_session_history():
    if not HISTORY_PATH.exists():
        return []

    with HISTORY_PATH.open(newline="") as csv_file:
        rows = []
        for row in csv.DictReader(csv_file):
            try:
                row["_date_obj"] = dt.datetime.strptime(row.get("date", ""), "%Y-%m-%d").date()
                row["_time_obj"] = dt.datetime.strptime(row.get("time", "00:00:00"), "%H:%M:%S").time()
                row["_duration_seconds"] = parse_duration_seconds(row.get("duration", "0:00:00"))
                row["_distance_km"] = float(row.get("distance_km", 0) or 0)
                row["_steps"] = int(row.get("steps", 0) or 0)
                row["_kilojoules"] = int(row.get("kilojoules", 0) or 0)
            except (TypeError, ValueError):
                continue
            if row["_distance_km"] == 0 and row["_steps"] == 0 and row["_kilojoules"] == 0:
                continue
            rows.append(row)

    rows.sort(key=lambda row: (row["_date_obj"], row["_time_obj"]), reverse=True)
    return rows


def parse_duration_seconds(duration):
    parts = [int(part) for part in duration.split(":")]
    if len(parts) != 3:
        raise ValueError("duration must be H:MM:SS")
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def empty_history_group(label, sort_key):
    return {
        "label": label,
        "sort_key": sort_key,
        "sessions": 0,
        "duration_seconds": 0,
        "duration": "0:00:00",
        "distance_km": 0.0,
        "steps": 0,
        "kilojoules": 0,
    }


def session_history_detail(row):
    return {
        "time": row.get("time", ""),
        "duration": row.get("duration", "0:00:00"),
        "speed_range_kmh": row.get("speed_range_kmh", "0.0-0.0"),
        "distance_km": row["_distance_km"],
        "steps": row["_steps"],
        "kilojoules": row["_kilojoules"],
    }


def add_history_row(group, row):
    group["sessions"] += 1
    group["duration_seconds"] += row["_duration_seconds"]
    group["distance_km"] += row["_distance_km"]
    group["steps"] += row["_steps"]
    group["kilojoules"] += row["_kilojoules"]
    group["duration"] = format_seconds_to_hms(group["duration_seconds"])


def history_summary(rows):
    total_distance = 0.0
    total_steps = 0
    total_kilojoules = 0
    total_seconds = 0

    for row in rows:
        total_distance += row["_distance_km"]
        total_steps += row["_steps"]
        total_kilojoules += row["_kilojoules"]
        total_seconds += row["_duration_seconds"]

    return {
        "sessions": len(rows),
        "duration": format_seconds_to_hms(total_seconds),
        "distance_km": total_distance,
        "steps": total_steps,
        "kilojoules": total_kilojoules,
    }


def aggregate_history(rows):
    months = {}
    weeks = {}
    days = {}
    today = dt.date.today()
    today_sessions = []

    for row in rows:
        session_date = row["_date_obj"]
        iso_year, iso_week, _weekday = session_date.isocalendar()
        week_start = session_date - dt.timedelta(days=session_date.weekday())
        week_end = week_start + dt.timedelta(days=6)
        week_key = (iso_year, iso_week)
        month_key = (session_date.year, session_date.month)

        if month_key not in months:
            months[month_key] = empty_history_group(session_date.strftime("%B %Y"), month_key)

        if week_key not in weeks:
            weeks[week_key] = empty_history_group(
                f"{week_start.strftime('%d %b')} - {week_end.strftime('%d %b %Y')}",
                week_start,
            )
            weeks[week_key]["days"] = {}

        if session_date not in days:
            days[session_date] = empty_history_group(session_date.strftime("%a %d %b %Y"), session_date)

        add_history_row(months[month_key], row)
        add_history_row(weeks[week_key], row)
        add_history_row(days[session_date], row)
        if session_date == today:
            today_sessions.append(session_history_detail(row))

    for day_key, day_group in days.items():
        week_key = day_key.isocalendar()[:2]
        weeks[week_key]["days"][day_key] = day_group

    week_groups = sorted(weeks.values(), key=lambda group: group["sort_key"], reverse=True)
    for week in week_groups:
        week["days"] = sorted(week["days"].values(), key=lambda group: group["sort_key"], reverse=True)

    return {
        "months": sorted(months.values(), key=lambda group: group["sort_key"], reverse=True),
        "weeks": week_groups,
        "today_total": days.get(today),
        "today_sessions": today_sessions,
    }


@app.route("/history")
def history():
    rows = load_session_history()
    return render_template(
        "history.html",
        summary=history_summary(rows),
        grouped_history=aggregate_history(rows),
    )


@app.route("/start")
def start_session():
    """Begin a new session: reset counters, start belt, launch stats monitor."""
    global session_active, belt_running, current_distance_km, current_steps, current_kilojoules, resume_speed_kmh
    global current_session_active_seconds, session_started_at, session_min_speed_kmh, session_max_speed_kmh

    if not connected:
        return redirect(url_for("root"))

    current_distance_km = current_steps = current_kilojoules = 0.0
    current_session_active_seconds = 0
    session_started_at = dt.datetime.now()
    session_min_speed_kmh = None
    session_max_speed_kmh = None
    resume_speed_kmh = 2.0
    speed_history.clear() 

    session_active = True
    belt_running = True

    async def seq():
        try:
            await controller.start_belt()
            await asyncio.sleep(0.5)
            asyncio.create_task(_stats_monitor())
        except Exception as exc:
            logging.error(f"Start sequence error: {exc}")

    asyncio.run_coroutine_threadsafe(seq(), ble_loop)
    return redirect(url_for("root"))


def reset_session_state():
    global session_active, belt_running, resume_speed_kmh
    global current_speed_kmh, current_distance_km, current_steps, current_kilojoules
    global current_session_active_seconds, session_started_at, session_min_speed_kmh, session_max_speed_kmh

    session_active = False
    belt_running = False
    resume_speed_kmh = 2.0
    current_speed_kmh = current_distance_km = current_kilojoules = 0.0
    current_steps = 0
    current_session_active_seconds = 0
    session_started_at = None
    session_min_speed_kmh = None
    session_max_speed_kmh = None
    speed_history.clear()


@app.route("/end_session", methods=["POST"])
def end_session():
    """Save the current session and reset counters without stopping the server."""
    if not session_active:
        return redirect(url_for("root"))

    save_session_history()
    stop_belt_before_shutdown()
    reset_session_state()
    return redirect(url_for("root"))


# ── Pause / Resume ───────────────────────────────────────────────────────

@app.route("/pause", endpoint="pause")
@app.route("/pause_session", endpoint="pause_session")
def pause_session():
    global belt_running, resume_speed_kmh
    if not belt_running:
        return redirect(url_for("root"))
    
    # Use the most recent speed from our history for manual pause
    if speed_history:
        resume_speed_kmh = speed_history[-1]

    belt_running = False
    asyncio.run_coroutine_threadsafe(controller.stop_belt(), ble_loop)
    return redirect(url_for("root"))


@app.route("/resume", endpoint="resume")
@app.route("/resume_session", endpoint="resume_session")
def resume_session():
    global belt_running, _auto_pause_grace_until, session_active # session_active ensures we only resume active sessions
    
    if not session_active: # Can't resume if no session was active
        logging.warning("Resume called but no active session.")
        return redirect(url_for("root"))

    if belt_running: # Already running, do nothing
        logging.info("Resume called but belt is already running.")
        return redirect(url_for("root"))

    # --- CRITICAL FIX: Optimistically set state for UI and grace period ---
    logging.info("Resume button clicked. Setting app state to active.")
    belt_running = True
    _auto_pause_grace_until = time.time() + 7 # Generous 7-second grace period for commands to take effect

    async def seq():
        try:
            logging.info("Attempting resume: Sending wake-up and start sequence to device...")
            
            # Standard wake-up and start sequence
            await controller.switch_mode(WalkingPad.MODE_STANDBY)
            await asyncio.sleep(0.5) 
            await controller.switch_mode(WalkingPad.MODE_MANUAL)
            await asyncio.sleep(0.5) 
            
            await controller.start_belt()
            await asyncio.sleep(0.5) 
            
            logging.info(f"Setting speed to {resume_speed_kmh:.1f} km/h.")
            await controller.change_speed(int(resume_speed_kmh * 10))
            await asyncio.sleep(0.5) # Allow speed change to propagate
            
            # Start the monitor if it wasn't running or to be sure
            asyncio.create_task(_stats_monitor())
            logging.info("Resume sequence commands sent, monitor ensured.")

        except Exception as exc:
            logging.error(f"Error during resume sequence, device may have disconnected: {exc}")
            _handle_disconnect(None) # This will set belt_running = False and connected = False
                                     # The frontend polling will then reload to the correct disconnected/connecting page.

    asyncio.run_coroutine_threadsafe(seq(), ble_loop)
    # The redirect will now happen after belt_running is True in the main thread.
    return redirect(url_for("root"))


# ── Speed Controls ───────────────────────────────────────────────────────
@app.route("/decrease_speed")
def decrease_speed():
    """Decrease the belt speed by one step."""
    if not belt_running:
        return redirect(url_for("root"))

    new_speed_kmh = max(MIN_SPEED_KMH, current_speed_kmh - SPEED_STEP)
    dev_speed = int(new_speed_kmh * 10)
    asyncio.run_coroutine_threadsafe(controller.change_speed(dev_speed), ble_loop)
    return redirect(url_for("root"))

@app.route("/slow_speed")
def slow_speed():
    """Set the belt speed to a predefined slow walk speed."""
    if not belt_running:
        return redirect(url_for("root"))
    
    dev_speed = int(SLOW_WALK_SPEED_KMH * 10)
    asyncio.run_coroutine_threadsafe(controller.change_speed(dev_speed), ble_loop)
    return redirect(url_for("root"))

@app.route("/increase_speed")
def increase_speed():
    """Increase the belt speed by one step."""
    if not belt_running:
        return redirect(url_for("root"))

    new_speed_kmh = min(MAX_SPEED_KMH, current_speed_kmh + SPEED_STEP)
    dev_speed = int(new_speed_kmh * 10)
    asyncio.run_coroutine_threadsafe(controller.change_speed(dev_speed), ble_loop)
    return redirect(url_for("root"))


@app.route("/max_speed")
def max_speed():
    """Set the belt speed to maximum."""
    if not belt_running:
        return redirect(url_for("root"))
    
    dev_speed = int(MAX_SPEED_KMH * 10)
    asyncio.run_coroutine_threadsafe(controller.change_speed(dev_speed), ble_loop)
    return redirect(url_for("root"))


# ── Live JSON endpoint ───────────────────────────────────────────────────
@app.route("/stats", endpoint="get_stats")
def stats_json():
    # Calculate formatted_time_active within the function scope
    formatted_time_active = format_seconds_to_hms(current_session_active_seconds)

    data = dict(
        is_connected=connected,      
        is_running=belt_running,     
        speed=round(current_speed_kmh, 1),
        distance=round(current_distance_km, 2),
        steps=current_steps,
        kilojoules=round(current_kilojoules),
        time_active=formatted_time_active 
    )

    resp = make_response(jsonify(data))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── Shutdown endpoint ──────────────────────────────────────────────────
def save_session_history():
    if not session_active:
        return
    if current_distance_km == 0 and current_steps == 0 and current_kilojoules == 0:
        logging.info("Skipping empty session history save.")
        return

    started_at = session_started_at or dt.datetime.now()
    min_speed = session_min_speed_kmh or 0.0
    max_speed = session_max_speed_kmh or 0.0
    row = {
        "date": started_at.strftime("%Y-%m-%d"),
        "time": started_at.strftime("%H:%M:%S"),
        "duration": format_seconds_to_hms(current_session_active_seconds),
        "speed_range_kmh": f"{min_speed:.1f}-{max_speed:.1f}",
        "distance_km": f"{current_distance_km:.2f}",
        "steps": int(current_steps),
        "kilojoules": round(current_kilojoules),
    }
    fieldnames = list(row.keys())
    write_header = not HISTORY_PATH.exists() or HISTORY_PATH.stat().st_size == 0

    with HISTORY_PATH.open("a", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)

    logging.info(f"Saved session history to {HISTORY_PATH}")


def stop_belt_before_shutdown():
    global belt_running
    if not belt_running or not controller or not ble_loop:
        return

    logging.info("Stopping belt before shutdown...")
    try:
        future = asyncio.run_coroutine_threadsafe(controller.stop_belt(), ble_loop)
        future.result(timeout=3)
    except Exception as exc:
        logging.warning(f"Could not confirm belt stop before shutdown: {exc}")
    finally:
        belt_running = False


@app.route("/shutdown", methods=['POST'])
def shutdown():
    """Save session history, stop the belt if needed, and shut down."""
    stop_belt_before_shutdown()
    save_session_history()
    logging.info("Server shutting down via forceful exit...")
    os._exit(0)


# ── Kick off BLE thread ──────────────────────────────────────────────────
# The server is no longer started here. This just pre-starts the BLE thread.
_start_ble_thread()
