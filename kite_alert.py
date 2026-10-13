#!/usr/bin/env python3

import os
import html
import json
import time
import argparse
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

API_URL = "https://api.open-meteo.com/v1/forecast"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
# Página web con el parte, para verlo desde el móvil.
SITE_FILE = os.path.join(BASE_DIR, "site", "index.html")

CHECK_EVERY_MINUTES = 30
FORECAST_DAYS = 3

# Modelos de Open-Meteo, por orden de preferencia. Si el primero no cubre
# el spot se usa el siguiente.
MODELS = ["meteofrance_seamless", "best_match"]

# Spots. Las coordenadas son aproximadas y configurables.
# dir_from / dir_to: sector de direcciones válidas en grados, en sentido
# horario (dirección DESDE la que viene el viento). None = cualquiera.
SPOTS = {
    "Oliva (Rabdells)": {
        "lat": 38.897, "lon": -0.052,
        # La costa mira al NE: se descarta el viento de tierra.
        "dir_from": 0, "dir_to": 135,
    },
    "Illa de Arousa": {
        "lat": 42.557, "lon": -8.866,
        "dir_from": None, "dir_to": None,
    },
    "Pantano de San Juan": {
        "lat": 40.374, "lon": -4.325,
        "dir_from": None, "dir_to": None,
    },
    "Valmayor": {
        "lat": 40.557, "lon": -4.049,
        "dir_from": None, "dir_to": None,
    },
    "El Atazar": {
        "lat": 40.952, "lon": -3.475,
        "dir_from": None, "dir_to": None,
    },
}

# Material actual del usuario.
# NO se incluye una Rebel 8 m porque todavía no la tiene.
KITES = {
    "Duotone Rebel 12 m": {
        "ideal_min": 15.0,
        "ideal_max": 22.0,
        "usable_min": 13.0,
        "usable_max": 25.0,
    },
    "Peter Lynn 10 m": {
        "ideal_min": 17.0,
        "ideal_max": 25.0,
        "usable_min": 15.0,
        "usable_max": 28.0,
    },
}

MAX_GUST_KT = 31.0
MAX_GUST_FACTOR = 1.45
RAIN_LIMIT_MM_H = 0.5

START_HOUR = 9
END_HOUR = 20

# Duración mínima de una sesión para que cuente.
MIN_SESSION_HOURS = 2

DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
]


def compass(degrees):
    return COMPASS[int((degrees + 11.25) / 22.5) % 16]


def in_sector(degrees, start, end):
    if start is None or end is None:
        return True
    return (degrees - start) % 360 <= (end - start) % 360


def fetch_forecast(lat, lon):
    for model in MODELS:
        response = requests.get(
            API_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "wind_speed_10m,wind_gusts_10m,"
                          "wind_direction_10m,precipitation,is_day",
                "wind_speed_unit": "kn",
                "timezone": "Europe/Madrid",
                "forecast_days": FORECAST_DAYS,
                "models": model,
            },
            timeout=30,
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Open-Meteo HTTP {response.status_code}: {response.text[:300]}"
            )

        data = response.json()
        if any(x is not None for x in data["hourly"]["wind_speed_10m"]):
            return data

    raise RuntimeError("ningún modelo devuelve viento para este punto")


def local_now(data):
    # Hora local del spot, sin tzinfo, igual que las horas que da el API.
    return (
        datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(seconds=data["utc_offset_seconds"])
    )


def parse(data):
    hourly = data["hourly"]
    now = local_now(data)

    rows = []

    for i, stamp in enumerate(hourly["time"]):
        dt = datetime.fromisoformat(stamp)
        wind = hourly["wind_speed_10m"][i]
        degrees = hourly["wind_direction_10m"][i]

        if dt < now or wind is None or degrees is None:
            continue

        gust = hourly["wind_gusts_10m"][i]
        rain = hourly["precipitation"][i]

        rows.append({
            "dt": dt,
            "wind": float(wind),
            "gust": float(gust) if gust is not None else float(wind),
            "degrees": float(degrees),
            "rain": float(rain) if rain is not None else 0.0,
            "is_day": hourly["is_day"][i] != 0,
        })

    return rows


def discard_reason(row, spot):
    """Devuelve por qué esa hora no vale, o None si es navegable."""
    wind = row["wind"]
    gust = row["gust"]

    if not row["is_day"] or not START_HOUR <= row["dt"].hour <= END_HOUR:
        return "fuera de horario"

    if not in_sector(row["degrees"], spot["dir_from"], spot["dir_to"]):
        return "dirección mala"

    if row["rain"] >= RAIN_LIMIT_MM_H:
        return "lluvia"

    if gust > MAX_GUST_KT:
        return "racha demasiado fuerte"

    if gust > wind * MAX_GUST_FACTOR:
        return "muy racheado"

    if not any(
        kite["usable_min"] <= wind <= kite["usable_max"]
        for kite in KITES.values()
    ):
        return "fuera del rango de tus cometas"

    return None


def find_sessions(rows, spot):
    """Agrupa horas navegables consecutivas del mismo día."""
    groups = []

    for row in rows:
        if discard_reason(row, spot) is not None:
            continue

        if (
            groups
            and row["dt"] - groups[-1][-1]["dt"] == timedelta(hours=1)
            and row["dt"].date() == groups[-1][-1]["dt"].date()
        ):
            groups[-1].append(row)
        else:
            groups.append([row])

    return [
        summarize(g) for g in groups
        if g[-1]["dt"] - g[0]["dt"] >= timedelta(hours=MIN_SESSION_HOURS)
    ]


def choose_kite(wind, gust):
    """Elige cometa según el viento constante y la racha máxima."""
    candidates = []

    for name, kite in KITES.items():
        if not kite["usable_min"] <= wind <= kite["usable_max"]:
            continue

        gust_ok = gust <= kite["usable_max"]
        ideal = kite["ideal_min"] <= wind <= kite["ideal_max"]
        center = (kite["ideal_min"] + kite["ideal_max"]) / 2

        if not gust_ok:
            note = f"ojo, las rachas superan su tope ({kite['usable_max']:.0f} kt)"
        elif ideal:
            note = "en su rango ideal"
        elif wind < kite["ideal_min"]:
            note = "irás justo de potencia"
        else:
            note = "irás pasado de potencia"

        # Primero que aguante las rachas, luego que el viento constante
        # caiga en su zona ideal y lo más centrado posible.
        candidates.append(((gust_ok, ideal, -abs(wind - center)), name, note))

    if not candidates:
        return None, "ninguna de tus cometas está en rango", False

    (gust_ok, ideal, _), name, note = max(candidates)
    return name, note, gust_ok and ideal


def feel(row):
    """Sensación de viento navegando: media entre el constante y la racha."""
    return (row["wind"] + row["gust"]) / 2


def summarize(group):
    winds = [r["wind"] for r in group]
    directions = [compass(r["degrees"]) for r in group]

    wind = sum(winds) / len(winds)
    gust = max(r["gust"] for r in group)
    kite, note, good = choose_kite(wind, gust)

    return {
        "start": group[0]["dt"],
        "end": group[-1]["dt"],
        "wind": wind,
        "wind_min": min(winds),
        "wind_max": max(winds),
        "gust": gust,
        "feel": sum(feel(r) for r in group) / len(group),
        "direction": max(set(directions), key=directions.count),
        "kite": kite,
        "note": note,
        "good": good,
    }


def format_spot(name, sessions):
    if not sessions:
        return f"🔴 {name}: sin sesión en {FORECAST_DAYS} días."

    lines = [name]

    for s in sessions:
        day = f"{DAYS[s['start'].weekday()]} {s['start'].day}"
        icon = "🟢" if s["good"] else "🟡"
        kite = f"{s['kite']} — {s['note']}" if s["kite"] else s["note"]

        lines.append(
            f"{icon} {day} · {s['start']:%H:%M}–{s['end']:%H:%M}\n"
            f"   💨 Constante {s['wind']:.0f} kt "
            f"({s['wind_min']:.0f}–{s['wind_max']:.0f}) · "
            f"Rachas {s['gust']:.0f} kt\n"
            f"   🌬️ Sensación {s['feel']:.0f} kt · 🧭 {s['direction']}\n"
            f"   🪁 {kite}"
        )

    return "\n".join(lines)


def daily_peaks(rows):
    """Hora de más viento de cada día, dentro del horario de navegación."""
    peaks = {}

    for row in rows:
        if not START_HOUR <= row["dt"].hour <= END_HOUR:
            continue

        day = row["dt"].date()
        if day not in peaks or row["wind"] > peaks[day]["wind"]:
            peaks[day] = row

    return [peaks[day] for day in sorted(peaks)]


PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kite Alert</title>
<style>
:root {{ color-scheme: light dark; --bg: #f3f5f7; --card: #fff;
  --text: #17212b; --muted: #627080; --line: #dde2e7;
  --good: #1a8a4a; --fair: #b57a00; --bad: #c8322b;
  --good-bg: #e6f5ec; --bad-bg: #fbe9e7; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #10161c; --card: #1a232c; --text: #e8edf2;
    --muted: #93a1b0; --line: #2b3742; --good: #4fd08a; --fair: #f0b840;
    --bad: #ff7b72; --good-bg: #16302a; --bad-bg: #33201f; }} }}
body {{ margin: 0; padding: 16px; background: var(--bg); color: var(--text);
  font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 560px; margin: 0 auto; }}
h1 {{ font-size: 1.5rem; margin: 8px 0 0; }}
.updated {{ color: var(--muted); font-size: .85rem; margin: 2px 0 16px; }}
.spot {{ background: var(--card); border-radius: 14px; padding: 14px 16px;
  margin-bottom: 12px; border-left: 6px solid var(--line); }}
.spot.on {{ background: var(--good-bg); border-color: var(--good); }}
.spot.off {{ background: var(--bad-bg); border-color: var(--bad); }}
h2 {{ font-size: 1.1rem; margin: 0 0 8px; }}
.on h2 {{ color: var(--good); }}
.off h2, .off .none {{ color: var(--bad); }}
.session {{ margin-bottom: 10px; }}
.when {{ font-weight: 600; }}
.wind {{ font-variant-numeric: tabular-nums; }}
.kite {{ font-weight: 600; color: var(--fair); }}
.good .kite {{ color: var(--good); }}
.none, .error {{ color: var(--muted); margin: 0 0 10px; }}
table {{ width: 100%; border-collapse: collapse; font-size: .85rem;
  color: var(--muted); font-variant-numeric: tabular-nums; }}
caption {{ text-align: left; padding-bottom: 2px; }}
td {{ border-top: 1px solid var(--line); padding: 4px 0; }}
td + td {{ text-align: right; }}
</style>
</head>
<body>
<main>
<h1>🪁 Kite Alert</h1>
<p class="updated">Actualizado: {updated}</p>
{spots}
</main>
</body>
</html>
"""


def spot_html(name, sessions, peaks, error):
    # Verde si hay sesión, rojo si no; sin color si falló la consulta.
    status = "error" if error else "on" if sessions else "off"
    parts = [f"<h2>{html.escape(name)}</h2>"]

    if error:
        parts.append(f'<p class="error">⚠️ {html.escape(error)}</p>')

    elif not sessions:
        parts.append(f'<p class="none">Sin sesión en {FORECAST_DAYS} días.</p>')

    for s in sessions:
        day = f"{DAYS[s['start'].weekday()]} {s['start'].day}"
        kite = f"{s['kite']} — {s['note']}" if s["kite"] else s["note"]

        parts.append(
            f'<div class="session{" good" if s["good"] else ""}">'
            f'<div class="when">{day} · '
            f'{s["start"]:%H:%M}–{s["end"]:%H:%M}</div>'
            f'<div class="wind">Constante {s["wind"]:.0f} kt '
            f'({s["wind_min"]:.0f}–{s["wind_max"]:.0f}) · '
            f'Rachas {s["gust"]:.0f} kt</div>'
            f'<div class="wind">Sensación {s["feel"]:.0f} kt · '
            f'{s["direction"]}</div>'
            f'<div class="kite">{html.escape(kite)}</div>'
            f'</div>'
        )

    if peaks:
        parts.append("<table><caption>Máximo previsto cada día</caption>")
        for p in peaks:
            parts.append(
                f"<tr><td>{DAYS[p['dt'].weekday()]} {p['dt'].day} · "
                f"{p['dt']:%H:%M}</td>"
                f"<td>{p['wind']:.0f} kt · rachas {p['gust']:.0f} kt · "
                f"sensación {feel(p):.0f} kt · "
                f"{compass(p['degrees'])}</td></tr>"
            )
        parts.append("</table>")

    return f'<section class="spot {status}">' + "".join(parts) + "</section>"


def write_page(results, updated):
    os.makedirs(os.path.dirname(SITE_FILE), exist_ok=True)

    with open(SITE_FILE, "w", encoding="utf-8") as f:
        f.write(PAGE.format(
            updated=updated,
            spots="\n".join(spot_html(*r) for r in results),
        ))


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def session_state(name, sessions):
    """Resumen por spot y día, para detectar novedades entre consultas."""
    state = {}

    for s in sessions:
        key = f"{name}|{s['start'].date().isoformat()}"
        entry = state.setdefault(key, {"kite": s["kite"], "end": ""})
        entry["end"] = s["end"].isoformat()

    return state


def describe(key):
    name, date = key.split("|")
    dt = datetime.fromisoformat(date)
    return f"{name}, {DAYS[dt.weekday()]} {dt.day}"


def find_changes(old, new):
    now = datetime.now().isoformat()
    changes = []

    for key, entry in new.items():
        if key not in old:
            changes.append(f"🆕 Sesión nueva: {describe(key)}")
        elif old[key]["kite"] != entry["kite"]:
            changes.append(f"🔁 Cambia la cometa: {describe(key)}")

    for key, entry in old.items():
        # Una sesión ya pasada no es una cancelación.
        if key not in new and entry["end"] > now:
            changes.append(f"❌ Ya no sale: {describe(key)}")

    return changes


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
        },
        timeout=20,
    )

    response.raise_for_status()


def check_once(force=False):
    old_state = load_state()
    new_state = {}
    reports = []
    results = []
    updated = "sin datos"

    for name, spot in SPOTS.items():
        try:
            data = fetch_forecast(spot["lat"], spot["lon"])
            rows = parse(data)
            sessions = find_sessions(rows, spot)
            now = local_now(data)
            updated = f"{DAYS[now.weekday()]} {now.day} · {now:%H:%M}"

            reports.append(format_spot(name, sessions))
            results.append((name, sessions, daily_peaks(rows), None))
            new_state.update(session_state(name, sessions))

        except Exception as exc:
            reports.append(f"⚠️ {name}: {exc}")
            results.append((name, [], [], str(exc)))
            # Sin datos no se puede saber si la sesión sigue en pie.
            new_state.update({
                key: entry for key, entry in old_state.items()
                if key.startswith(f"{name}|")
            })

    changes = find_changes(old_state, new_state)

    message = "🪁 KITE ALERT\n"
    if changes:
        message += "\n".join(changes) + "\n"
    message += "\n" + "\n\n".join(reports)

    print("\n" + message + "\n")

    write_page(results, updated)

    if not changes and not force:
        print("Sin novedades: no se envía aviso.")
        return

    try:
        send_telegram(message)
        save_state(new_state)
    except Exception as exc:
        print(f"⚠️ Telegram: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once",
        action="store_true",
        help="Consulta una vez y termina",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Envía el parte aunque no haya novedades",
    )
    args = parser.parse_args()

    if args.once:
        check_once(args.force)
        return

    print(
        f"Kite Alert activo. Consultando cada "
        f"{CHECK_EVERY_MINUTES} minutos. Ctrl+C para salir."
    )

    while True:
        check_once(args.force)
        time.sleep(CHECK_EVERY_MINUTES * 60)


if __name__ == "__main__":
    main()
