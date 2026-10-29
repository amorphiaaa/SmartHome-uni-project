
import time
import getpass
import imaplib
import smtplib
import sqlite3
import serial

from pathlib import Path
from datetime import datetime, date, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ===== CONFIGURATION =====

PORT = "COM4"
BAUD = 9600

IMAP_HOST = "imap.gmail.com"
SMTP_HOST = "smtp.gmail.com"

THINGSPEAK_URL = "https://api.thingspeak.com/update"
THINGSPEAK_INTERVAL = 20
MAIL_CHECK_INTERVAL = 10

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "smarthome_data.db"
REPORT_DIR = BASE_DIR / "reports"

COMMANDS = {
    "LIGHT ON", "LIGHT OFF",
    "LIGHT AUTO ON", "LIGHT AUTO OFF",
    "SECURE ON", "SECURE OFF",
    "AUTO TEMP ON", "AUTO TEMP OFF",
    "HEAT ON", "HEAT OFF",
    "COOL ON", "COOL OFF",
    "STOP", "STATUS",
    "EMERGENCY OFF"
}


# ===== USER CREDENTIALS =====

mailbox = input(
    "Project Gmail address: "
).strip()

password = getpass.getpass(
    "Gmail app password: "
).replace(" ", "")

allowed_sender = input(
    "Authorized sender email: "
).strip().lower()

prefix = input(
    "Secret subject prefix (min 8 characters): "
).strip().upper()

thingspeak_key = getpass.getpass(
    "ThingSpeak Write API Key: "
).strip()

if not all((mailbox, password, allowed_sender, thingspeak_key)):
    raise SystemExit("Missing configuration")

if len(prefix) < 8:
    raise SystemExit("Prefix must contain at least 8 characters")


# ===== DATABASE =====

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS samples (
    ts TEXT NOT NULL,
    temperature REAL NOT NULL,
    illumination INTEGER NOT NULL,
    motion_count INTEGER NOT NULL,
    heater INTEGER NOT NULL,
    motor INTEGER NOT NULL,
    led INTEGER NOT NULL,
    secure INTEGER NOT NULL,
    light_auto INTEGER NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS daily_reports (
    day TEXT PRIMARY KEY
)
""")

db.commit()


# ===== SEND EMAIL =====

def send_email(subject, body, attachments=()):

    msg = EmailMessage()

    msg["From"] = mailbox
    msg["To"] = allowed_sender
    msg["Subject"] = subject
    msg.set_content(body)

    for path in attachments:

        msg.add_attachment(
            path.read_bytes(),
            maintype="image",
            subtype="png",
            filename=path.name
        )

    with smtplib.SMTP_SSL(
        SMTP_HOST, 465, timeout=20
    ) as smtp:

        smtp.login(mailbox, password)
        smtp.send_message(msg)


# ===== CONNECT TO GMAIL =====

def open_mail():

    mail = imaplib.IMAP4_SSL(
        IMAP_HOST, timeout=15
    )

    mail.login(mailbox, password)
    mail.select("INBOX")

    return mail


def latest_uid():

    with open_mail() as mail:

        status, data = mail.uid(
            "SEARCH", None, "ALL"
        )

        if status != "OK" or not data or not data[0]:
            return 0

        return int(data[0].split()[-1])


# ===== PARSE ARDUINO DATA =====

def parse_data(line):

    parts = line.split(",")

    if len(parts) != 9 or parts[0] != "DATA":
        return None

    try:

        temperature = float(parts[1])

        values = [
            int(value) for value in parts[2:]
        ]

    except ValueError:
        return None

    illumination = values[0]
    motion_count = values[1]
    states = values[2:]

    if not (-20 <= temperature <= 125):
        return None

    if not (0 <= illumination <= 100):
        return None

    if motion_count < 0:
        return None

    if any(state not in (0, 1) for state in states):
        return None

    return (temperature, *values)


# ===== SAVE SENSOR DATA =====

def save_sample(values):

    timestamp = datetime.now().isoformat(
        timespec="seconds"
    )

    db.execute(
        "INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?)",
        (timestamp, *values)
    )

    db.commit()


# ===== THINGSPEAK =====

def send_thingspeak(values):

    fields = {
        "api_key": thingspeak_key
    }

    for index, value in enumerate(values, start=1):
        fields[f"field{index}"] = value

    request = Request(
        THINGSPEAK_URL,
        data=urlencode(fields).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    with urlopen(request, timeout=10) as response:

        entry = response.read().decode().strip()

    if not entry.isdigit() or int(entry) == 0:

        print("ThingSpeak upload rejected:", entry)
        return False

    print("ThingSpeak upload OK! Entry:", entry)

    return True


# ===== DAILY REPORT =====

def send_report(day, demo=False):

    rows = db.execute(
        """
        SELECT * FROM samples
        WHERE substr(ts,1,10)=?
        ORDER BY ts
        """,
        (day,)
    ).fetchall()

    if not rows:
        print("No data for report:", day)
        return False

    timestamps = [
        datetime.fromisoformat(row["ts"])
        for row in rows
    ]

    temperatures = [
        float(row["temperature"])
        for row in rows
    ]

    illuminations = [
        int(row["illumination"])
        for row in rows
    ]

    folder = REPORT_DIR / day
    folder.mkdir(parents=True, exist_ok=True)

    # Calculate hourly motion counts
    motion_by_hour = [0] * 24

    # Include the first counter value if it starts
    # above zero; this is still an estimate.
    previous_count = None

    for row, timestamp in zip(rows, timestamps):

        count = int(row["motion_count"])

        if previous_count is not None:

            difference = max(
                0,
                count - previous_count
            )

            motion_by_hour[timestamp.hour] += difference

        previous_count = count

    total_motion = sum(motion_by_hour)

    # Estimate mode duration from consecutive samples
    def mode_hours(column):

        seconds = 0.0

        for first, second in zip(rows, rows[1:]):

            start = datetime.fromisoformat(first["ts"])
            end = datetime.fromisoformat(second["ts"])

            duration = (end - start).total_seconds()

            # Ignore long offline gaps
            if (
                0 <= duration <= 60
                and int(first[column]) == 1
            ):
                seconds += duration

        return seconds / 3600.0

    secure_hours = mode_hours("secure")
    light_auto_hours = mode_hours("light_auto")

    # Create time-series graphs
    def line_chart(values, title, ylabel, filename):

        fig, ax = plt.subplots(figsize=(9, 4))

        ax.plot(
            timestamps,
            values,
            marker=".",
            markersize=3
        )

        ax.set_title(title)
        ax.set_xlabel("Time")
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.3)

        ax.xaxis.set_major_formatter(
            mdates.DateFormatter("%H:%M")
        )

        fig.autofmt_xdate()
        fig.tight_layout()

        path = folder / filename
        fig.savefig(path, dpi=130)

        plt.close(fig)

        return path

    temp_chart = line_chart(
        temperatures,
        "Daily Temperature",
        "Temperature (C)",
        "temperature.png"
    )

    light_chart = line_chart(
        illuminations,
        "Daily Illumination",
        "Illumination (%)",
        "illumination.png"
    )

    # Create motion graph
    fig, ax = plt.subplots(figsize=(9, 4))

    ax.bar(range(24), motion_by_hour)

    ax.set_title("Motion Detections by Hour")
    ax.set_xlabel("Hour")
    ax.set_ylabel("Detections")

    ax.set_xticks(range(0, 24, 2))
    fig.tight_layout()

    motion_chart = folder / "motion.png"
    fig.savefig(motion_chart, dpi=130)

    plt.close(fig)

    # Generate the twelve required report items
    report = f"""Smart Home Daily Report - {day}

1. Minimum daily temperature:
   {min(temperatures):.2f} C

2. Maximum daily temperature:
   {max(temperatures):.2f} C

3. Average daily temperature:
   {sum(temperatures) / len(temperatures):.2f} C

4. Temperature graph:
   temperature.png

5. Minimum daily illumination:
   {min(illuminations)} %

6. Maximum daily illumination:
   {max(illuminations)} %

7. Average daily illumination:
   {sum(illuminations) / len(illuminations):.2f} %

8. Illumination graph:
   illumination.png

9. Motion detections (estimated):
   {total_motion}

10. Motion detections by hour:
    motion.png

11. Home Secure Mode duration:
    {secure_hours:.3f} hours

12. Light Auto Mode duration:
    {light_auto_hours:.3f} hours

Recorded measurements: {len(rows)}
Report type: {"DEMO" if demo else "DAILY"}

Notes:
- Telemetry interval: 20 seconds.
- Motion counts are estimated from Arduino counters.
- Mode durations are approximated from samples.
- Missing measurements may affect accuracy.
"""

    report_path = folder / "report.txt"
    report_path.write_text(report, encoding="utf-8")

    subject = (
        f"Smart Home {'Demo' if demo else 'Daily'} "
        f"Report - {day}"
    )

    send_email(
        subject,
        report,
        [temp_chart, light_chart, motion_chart]
    )

    print("Daily report email sent:", day)

    return True


# ===== AUTOMATIC DAILY REPORT =====

def check_daily_report():

    yesterday = (
        date.today() - timedelta(days=1)
    ).isoformat()

    already_sent = db.execute(
        "SELECT 1 FROM daily_reports WHERE day=?",
        (yesterday,)
    ).fetchone()

    if already_sent:
        return

    exists = db.execute(
        """
        SELECT 1 FROM samples
        WHERE substr(ts,1,10)=?
        LIMIT 1
        """,
        (yesterday,)
    ).fetchone()

    if not exists:
        return

    if send_report(yesterday):

        db.execute(
            "INSERT OR IGNORE INTO daily_reports VALUES (?)",
            (yesterday,)
        )

        db.commit()


# ===== EMAIL COMMANDS =====

def check_mail(arduino, last_uid):

    with open_mail() as mail:

        status, data = mail.uid(
            "SEARCH",
            None,
            f"UID {last_uid + 1}:*"
        )

        if status != "OK" or not data or not data[0]:
            return last_uid

        for uid_bytes in data[0].split():

            uid = int(uid_bytes)

            if uid <= last_uid:
                continue

            status, parts = mail.uid(
                "FETCH",
                uid_bytes,
                "(BODY.PEEK[HEADER])"
            )

            if status != "OK":
                continue

            header = next(
                (
                    part[1] for part in parts
                    if isinstance(part, tuple)
                ),
                None
            )

            if not header:
                continue

            msg = message_from_bytes(header)

            sender = parseaddr(
                msg.get("From", "")
            )[1].lower()

            subject = str(
                make_header(
                    decode_header(
                        msg.get("Subject", "")
                    )
                )
            ).strip().upper()

            # Validate authorized sender and secret prefix
            if (
                sender != allowed_sender
                or not subject.startswith(prefix + " ")
            ):
                last_uid = uid
                continue

            command = subject[len(prefix) + 1:].strip()

            try:

                # Generate a demo report immediately
                if command == "REPORT NOW":

                    send_report(
                        date.today().isoformat(),
                        demo=True
                    )

                # Reset emergency through an authorized email
                elif command == "EMERGENCY OFF":

                    arduino.write(
                        b"REMOTE EMERGENCY OFF\n"
                    )

                    arduino.flush()

                    print("EMAIL COMMAND: EMERGENCY OFF")

                # Forward other commands to Arduino
                elif command in COMMANDS:

                    arduino.write(
                        (command + "\n").encode("ascii")
                    )

                    arduino.flush()

                    print("EMAIL COMMAND:", command)

                else:

                    print("Unknown command:", command)

            except Exception as error:

                print("Email command error:", error)
                return last_uid

            last_uid = uid

    return last_uid


# ===== MAIN PROGRAM =====

print("Connecting to Gmail...")

last_uid = latest_uid()

print("Gmail connected!")
print("Waiting for new emails...")

try:

    with serial.Serial(
        PORT,
        BAUD,
        timeout=0.3
    ) as arduino:

        time.sleep(2)
        arduino.reset_input_buffer()

        print("Arduino connected on", PORT)

        last_mail_check = 0.0
        last_alert = -100.0
        last_upload = -100.0
        last_report_check = 0.0

        latest_values = None
        upload_pending = False

        while True:

            # Read data and events from Arduino
            line = arduino.readline().decode(
                "utf-8",
                errors="replace"
            ).strip()

            if line:

                print("ARDUINO:", line)

                # Save telemetry to SQLite
                if line.startswith("DATA,"):

                    values = parse_data(line)

                    if values is not None:

                        save_sample(values)

                        latest_values = values
                        upload_pending = True

                    else:

                        print("Invalid DATA message")

                # Send an emergency notification
                if line == "EVENT,EMERGENCY":

                    try:

                        send_email(
                            "Smart Home: EMERGENCY ALERT",
                            "Emergency button D11 was pressed.\n"
                            "Heating and cooling are disabled.\n"
                            "Home Secure Mode is active.\n\n"
                            "To reset, send an email with subject:\n"
                            f"{prefix} EMERGENCY OFF"
                        )

                        print("Emergency email sent!")

                    except Exception as error:

                        print("Emergency email error:", error)

                # Send a motion notification
                if line == "EVENT,SECURITY_ALERT":

                    now = time.monotonic()

                    if now - last_alert >= 10:

                        last_alert = now

                        try:

                            send_email(
                                "Smart Home: Motion Alert",
                                "Movement detected.\n"
                                "Home Secure Mode is active."
                            )

                            print("Motion alert email sent!")

                        except Exception as error:

                            print("Motion email error:", error)

            now = time.monotonic()

            # Upload telemetry every 20 seconds
            if (
                upload_pending
                and latest_values is not None
                and now - last_upload >= THINGSPEAK_INTERVAL
            ):

                last_upload = now

                try:

                    if send_thingspeak(latest_values):
                        upload_pending = False

                except Exception as error:

                    print("ThingSpeak error:", error)

            # Check incoming Gmail commands
            if now - last_mail_check >= MAIL_CHECK_INTERVAL:

                last_mail_check = now

                try:

                    last_uid = check_mail(
                        arduino,
                        last_uid
                    )

                except Exception as error:

                    print("IMAP error:", error)

            # Send reports for completed days
            if now - last_report_check >= 60:

                last_report_check = now

                try:

                    check_daily_report()

                except Exception as error:

                    print("Daily report error:", error)

except KeyboardInterrupt:

    print("Program stopped")

finally:

    db.close()
