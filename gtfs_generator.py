"""Engine generator GTFS LRT Jakarta.

Output GTFS: agency.txt, calendar.txt, routes.txt, stops.txt,
trips.txt, stop_times.txt, extras.txt, timetables.txt (dikemas .zip),
mengikuti format "Kelapa Gading - Manggarai Referensi":
  - stop_id:  stop_agency_lrtj_1 .. stop_agency_lrtj_11, stop_code numerik 40001..
  - route_id: route_agency_lrtj_0 (PGD->MRI) / route_agency_lrtj_1 (MRI->PGD)
  - service:  service_agency_lrtj_0 / service_agency_lrtj_1
  - trip_id:  trip_{route_id}_{n}
  - waktu stop_times H:MM:SS (jam tanpa leading zero), boleh >= 24:00
Menangani trip yang melewati tengah malam (waktu > 24:00) dan
manajemen stasiun aktif/nonaktif (is_active).
"""

import csv
import io
import zipfile
from collections import Counter

from lrt_route import AGENCY, ROUTES
import lrt_station_data as _sd

STATION_SET = _sd.STATIONS

# Nama resmi stasiun (tanpa awalan "STASIUN LRT") pada zip deployment terbaru
# . Dipakai memetakan kode LRT -> stop_id zip
# bila stop_name zip memakai format terbaru (mis. "STASIUN LRT KELAPA GADING").
# ============================================================================
# Konstanta format Referensi LRTJ ()
# Kelapa Gading - Manggarai Referensi.zip
# ============================================================================

_DEPLOY_AGENCY = {
    "agency_id": "agency_lrtj",
    "agency_name": "LRT Jakarta",
    "agency_url": "https://lrtjakarta.co.id/",
    "agency_timezone": "Asia/Jakarta",
    "agency_lang": "id",
    "agency_phone": "",
}

# stop_id / stop_code / stop_name mengikuti urutan fisik zip Referensi.
_DEPLOY_STOP_MAP = {
    "KPG": {"stop_id": "stop_agency_lrtj_1", "stop_code": "40006", "stop_name": "STASIUN LRT KELAPA GADING"},
    "BVU": {"stop_id": "stop_agency_lrtj_2", "stop_code": "40005", "stop_name": "STASIUN LRT BOULEVARD UTARA"},
    "BVS": {"stop_id": "stop_agency_lrtj_3", "stop_code": "40004", "stop_name": "STASIUN LRT BOULEVARD SELATAN"},
    "PUM": {"stop_id": "stop_agency_lrtj_4", "stop_code": "40003", "stop_name": "STASIUN LRT PULOMAS"},
    "EQS": {"stop_id": "stop_agency_lrtj_5", "stop_code": "40002", "stop_name": "STASIUN LRT EQUESTRIAN"},
    "VEL": {"stop_id": "stop_agency_lrtj_6", "stop_code": "40001", "stop_name": "STASIUN LRT VELODROME"},
    "RWM": {"stop_id": "stop_agency_lrtj_7", "stop_code": "40007", "stop_name": "STASIUN LRT RAWAMANGUN"},
    "PRM": {"stop_id": "stop_agency_lrtj_8", "stop_code": "40008", "stop_name": "STASIUN LRT PRAMUKA"},
    "MRT": {"stop_id": "stop_agency_lrtj_9", "stop_code": "40009", "stop_name": "STASIUN LRT MATRAMAN"},
    "PSM": {"stop_id": "stop_agency_lrtj_10", "stop_code": "40010", "stop_name": "STASIUN LRT PROKLAMASI"},
    "MRI": {"stop_id": "stop_agency_lrtj_11", "stop_code": "40011", "stop_name": "STASIUN LRT MANGGARAI"},
}

_DEPLOY_ROUTE_BY_DIRECTION = {
    "PGD-MRI": ("route_agency_lrtj_0", "service_agency_lrtj_0"),
    "MRI-PGD": ("route_agency_lrtj_1", "service_agency_lrtj_1"),
}

# Service reguler yang dibangkitkan dari timetable. Service lain di zip template
# (mis. tahun baru malam/pagi) dianggap "extra" dan ikut di-merge apa adanya.
_MANAGED_SERVICES = {"service_agency_lrtj_0", "service_agency_lrtj_1"}

_CALENDAR_START = "20210927"
_CALENDAR_END = "20261231"


def _deploy_stop(code):
    """stop_id Referensi untuk kode LRT (fallback: kode asli)."""
    d = _DEPLOY_STOP_MAP.get(code)
    return d["stop_id"] if d else code


# ============================================================================
# Konstanta legacy mapping nama (dipakai build_zip_stop_mapping)
# ============================================================================

_DEPLOY_NAME_MAP = {
    "KPG": "KELAPA GADING",
    "BVU": "BOULEVARD UTARA",
    "BVS": "BOULEVARD SELATAN",
    "PUM": "PULOMAS",
    "EQS": "EQUESTRIAN",
    "VEL": "VELODROME",
    "RWM": "RAWAMANGUN",
    "PRM": "PRAMUKA",
    "PSM": "PROKLAMASI",
    "MRT": "MATRAMAN",
    "MRI": "MANGGARAI",
}


def configure(stations=None, agency=None, routes=None):
    """Swap config (pola referensi). LRT sebagai default."""
    global STATION_SET
    if stations is not None:
        STATION_SET = stations
    global AGENCY, ROUTES
    if agency is not None:
        AGENCY = agency
    if routes is not None:
        ROUTES = routes


def is_station_active(station_code):
    s = STATION_SET.get(station_code)
    if s is None:
        return True
    return s.get("is_active", 1) != 0


def get_inactive_stations():
    return sorted([c for c, s in STATION_SET.items() if s.get("is_active", 1) == 0])


def get_active_stations():
    return sorted([c for c, s in STATION_SET.items() if s.get("is_active", 1) != 0])


def set_station_active(station_code, active):
    if station_code in STATION_SET:
        STATION_SET[station_code]["is_active"] = 1 if active else 0
        return True
    return False


def reset_station_activity():
    for s in STATION_SET.values():
        s["is_active"] = 1


def add_station(code, stop_code=None, stop_name=None, stop_lat=None, stop_lon=None, zone_id=None):
    code = (code or "").strip().upper()
    if not code or code in STATION_SET:
        return False
    STATION_SET[code] = {
        "stop_id": code,
        "stop_code": (stop_code or "").strip() or code,
        "stop_name": (stop_name or "").strip() or code,
        "stop_lat": float(stop_lat) if stop_lat else 0.0,
        "stop_lon": float(stop_lon) if stop_lon else 0.0,
        "zone_id": (zone_id or "").strip() or "JAKARTA",
        "level_id": "L0",
        "is_active": 1,
    }
    return True


def set_station_info(code, **updates):
    if code not in STATION_SET:
        return False
    for k, v in updates.items():
        if k in STATION_SET[code] and v not in (None, ""):
            STATION_SET[code][k] = v
    return True


def _time_to_minutes(tval):
    if tval is None:
        return None
    return tval.hour * 60 + tval.minute + tval.second // 60


def _minutes_to_hhmmss(minutes):
    minutes = int(round(minutes))
    hh = minutes // 60
    mm = minutes % 60
    return "%02d:%02d:%02d" % (hh, mm, 0)


def _minutes_to_deploy_time(minutes):
    """Format waktu Referensi: H:MM:SS (jam tanpa leading zero)."""
    minutes = int(round(minutes))
    hh = minutes // 60
    mm = minutes % 60
    return "%d:%02d:%02d" % (hh, mm, 0)


def normalize_trip_times(stops):
    """Koreksi waktu tiap stop agar monoton naik, dengan crossing midnight (+1440)."""
    recs = []
    prev = None
    offset = 0
    for st in stops:
        base = st["arrival"] if st["arrival"] is not None else st["departure"]
        arr_min = _time_to_minutes(st["arrival"])
        dep_min = _time_to_minutes(st["departure"])
        if base is None:
            recs.append({"station": st["station"], "arrival": arr_min, "departure": dep_min, "express": st.get("express", False)})
            continue
        base_min = _time_to_minutes(base)
        if prev is not None and base_min < prev:
            offset += 1440
        prev = base_min
        a = arr_min + offset if arr_min is not None else None
        d = dep_min + offset if dep_min is not None else None
        recs.append({"station": st["station"], "arrival": a, "departure": d, "express": st.get("express", False)})

    first_dep = next((r["departure"] for r in recs if r["departure"] is not None), None)
    for r in recs:
        if r["arrival"] is not None and first_dep is not None and r["arrival"] < first_dep:
            r["arrival"] += 1440
        if r["departure"] is not None and first_dep is not None and r["departure"] < first_dep:
            r["departure"] += 1440
        if r["arrival"] is not None and r["departure"] is not None and r["departure"] < r["arrival"]:
            r["departure"] = r["arrival"]
    return recs


def sanitize_trip_id(no_ka):
    return str(no_ka).replace(" ", "_").replace("/", "_").replace("\\", "_")


def generate_agency():
    return "agency_id,agency_name,agency_url,agency_timezone,agency_lang,agency_phone\n" \
           "{agency_id},{agency_name},{agency_url},{agency_timezone},{agency_lang},{agency_phone}\n".format(**_DEPLOY_AGENCY)


def generate_calendar(service_ids):
    lines = ["end_date,friday,monday,saturday,service_id,start_date,sunday,thursday,tuesday,wednesday"]
    for sid in service_ids:
        lines.append(f"{_CALENDAR_END},1,1,1,{sid},{_CALENDAR_START},1,1,1,1")
    return "\n".join(lines) + "\n"


def generate_routes(used_routes=None):
    lines = ["route_id,agency_id,route_short_name,route_long_name,route_type,route_color,route_text_color"]
    order = ["route_agency_lrtj_0", "route_agency_lrtj_1"]
    if used_routes:
        order = [r for r in order if r in used_routes]
    for rid in order:
        lines.append(f"{rid},{_DEPLOY_AGENCY['agency_id']},LRTJ,LRT Jakarta,1,,")
    return "\n".join(lines) + "\n"


def generate_stops(used_stations=None):
    lines = ["location_type,stop_code,stop_id,stop_lat,stop_lon,stop_name,stop_timezone"]
    for code, meta in STATION_SET.items():
        if not meta.get("is_active", 1):
            continue
        if used_stations is not None and _deploy_stop(code) not in used_stations:
            continue
        d = _DEPLOY_STOP_MAP.get(code, {})
        stop_id = d.get("stop_id") or meta.get("stop_id") or code
        stop_code = d.get("stop_code") or meta.get("stop_code") or code
        stop_name = d.get("stop_name") or meta.get("stop_name") or code
        lat = meta.get("stop_lat") or 0.0
        lon = meta.get("stop_lon") or 0.0
        lines.append(f"0,{stop_code},{stop_id},{lat:.6f},{lon:.6f},{stop_name},Asia/Jakarta")
    return "\n".join(lines) + "\n"


def _route_for_direction(direction):
    route, _service = _DEPLOY_ROUTE_BY_DIRECTION.get(direction, ("route_agency_lrtj_0", "service_agency_lrtj_0"))
    return route


def _service_for_direction(direction):
    _route, service = _DEPLOY_ROUTE_BY_DIRECTION.get(direction, ("route_agency_lrtj_0", "service_agency_lrtj_0"))
    return service


def generate_trips_and_stop_times(parsed_data, filter_services=None, station_map=None, active_func=None):
    """Generate trips.txt & stop_times.txt.

    - station_map: {kode_LRT: stop_id_zip} bila timetable harus dipetakan ke
      stop_id GTFS zip upload (opsional). Bila diberikan, used_stations berisi
      stop_id zip; bila tidak, berisi kode LRT.
    - active_func: callable(stop_id_zip) -> bool untuk filter stasiun aktif
      (default: is_station_active per kode LRT).
    """
    trips = ["route_id,service_id,trip_id"]
    stop_times = ["trip_id,pickup_type,departure_time,stop_id,arrival_time,stop_sequence,drop_off_type"]
    used_stations = set()
    used_trip_ids = set()

    for direction, meta in parsed_data.items():
        deploy_route = _route_for_direction(meta["direction"])
        deploy_service = _service_for_direction(meta["direction"])
        per_route_index = 0
        for t in meta["trains"]:
            sid = t.get("service_id", "AllDay")
            if filter_services and sid not in filter_services:
                continue
            normalized = normalize_trip_times(t["stops"])
            real = [r for r in normalized
                    if (r["arrival"] is not None or r["departure"] is not None) and not r.get("express")]
            mapped = []
            for r in real:
                stop = station_map.get(r["station"], r["station"]) if station_map else r["station"]
                mapped.append(dict(r, station=stop))
            if active_func:
                mapped = [r for r in mapped if active_func(r["station"])]
            else:
                mapped = [r for r in mapped if is_station_active(r["station"])]
            if len(mapped) < 2:
                continue
            per_route_index += 1
            trip_id = f"trip_{deploy_route}_{per_route_index}"
            while trip_id in used_trip_ids:
                per_route_index += 1
                trip_id = f"trip_{deploy_route}_{per_route_index}"
            used_trip_ids.add(trip_id)

            trips.append(f"{deploy_route},{deploy_service},{trip_id}")
            seq = 1
            for r in mapped:
                if station_map:
                    out_stop = r["station"]
                else:
                    out_stop = _deploy_stop(r["station"])
                used_stations.add(out_stop)
                t_txt = _minutes_to_deploy_time(r["departure"] if r["departure"] is not None else r["arrival"])
                stop_times.append(f"{trip_id},0,{t_txt},{out_stop},{t_txt},{seq},0")
                seq += 1
    return trips, stop_times, used_stations


def generate_extras(stop_ids):
    """extras.txt Referensi: baris stop,<stop_id>,hub,0 per stop."""
    lines = ["object,id,key,value"]
    for sid in sorted(stop_ids):
        lines.append(f"stop,{sid},hub,0")
    return "\n".join(lines) + "\n"


def generate_timetables(routes_used, services_used):
    """timetables.txt Referensi: 1 baris per rute yang dipakai."""
    lines = ["timetable_id,route_id,start_date,end_date,monday,tuesday,wednesday,thursday,friday,saturday,sunday"]
    for i, rid in enumerate(sorted(routes_used)):
        suffix = rid.rsplit("_", 1)[-1]
        sid = f"service_agency_lrtj_{suffix}" if f"service_agency_lrtj_{suffix}" in services_used else next(
            iter(services_used))
        lines.append(
            f"timetable_agency_lrtj_{rid}_{sid}_{i},{rid},{_CALENDAR_START},{_CALENDAR_END},1,1,1,1,1,1,1")
    return "\n".join(lines) + "\n"


def generate_gtfs_zip(parsed_data, filter_services=None):
    trips_lines, stop_times_lines, used_stations = generate_trips_and_stop_times(parsed_data, filter_services)
    services_used = {line.split(",")[1] for line in trips_lines[1:]}
    services_used = services_used or {"service_agency_lrtj_0"}
    routes_used = {line.split(",")[0] for line in trips_lines[1:]}
    files = {
        "agency.txt": generate_agency(),
        "calendar.txt": generate_calendar(sorted(services_used)),
        "routes.txt": generate_routes(routes_used),
        "stops.txt": generate_stops(used_stations),
        "trips.txt": "\n".join(trips_lines) + "\n",
        "stop_times.txt": "\n".join(stop_times_lines) + "\n",
        "extras.txt": generate_extras(used_stations),
        "timetables.txt": generate_timetables(routes_used, services_used),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf, used_stations


def get_summary_gtfs(parsed_data):
    """Ringkasan GTFS ringan untuk ditampilkan."""
    total_trips = 0
    total_stop_times = 0
    for direction, meta in parsed_data.items():
        for t in meta["trains"]:
            total_trips += 1
            total_stop_times += len([s for s in t["stops"] if s["arrival"] is not None or s["departure"] is not None])
    return {"total_trips": total_trips, "total_stop_times": total_stop_times}


# ===========================================================================
# Mode "zip + timetable": struktur & stop list mengikuti GTFS zip upload,
# trip & stop_times dibangkitkan dari tabel waktu yang di-upload.
# ===========================================================================
def build_zip_stop_mapping(zip_stops, db_stations=None):
    """Petakan kode stasiun LRT (dari timetable) -> stop_id GTFS zip.

    Pencocokan bertingkat:
      1. stop_code persis (kode LRT ATAU stop_code database), lalu
      2. stop_name persis (hasil normalize), lalu
      3. nama resmi deployment via _DEPLOY_NAME_MAP (zip terbaru LRTJ),
      4. token subset stop_name database vs stop_name zip.
    Mengembalikan (mapping, warnings) dengan memakai `_sd.STATIONS` bila
    `db_stations` tidak diberikan.

    - mapping: {kode_LRT: zip_stop_id}
    - warnings: [(kode_LRT, nama_LRT, zip_stop_id, nama_zip)] untuk nama yang
      berbeda antara database LRT dan stop_name zip.
    """
    db = db_stations or _sd.STATIONS

    def norm(s):
        return " ".join(str(s).upper().replace(".", " ").replace("-", " ").replace("_", " ").split())

    _STOP = _DEPLOY_NAME_MAP  # noqa: F841 (tidak langsung dipakai, matcher pakai _DEPLOY_NAME_MAP)

    by_code = {}
    by_name = {}
    token_groups = {}  # {frozenset(tokens): sid} dari stop_name zip
    for sid, s in zip_stops.items():
        code = norm(s.get("stop_code") or "")
        if code and code not in by_code:
            by_code[code] = sid
        nm = norm(s.get("stop_name") or "")
        if nm and nm not in by_name:
            by_name[nm] = sid
        toks = frozenset(nm.split()) if nm else frozenset()
        if toks and toks not in token_groups:
            token_groups[toks] = sid

    def match_tokens(requested):
        """Cari zip stop yang token namanya SUPERSET dari requested tokens.

        Mengembalikan sid pertama (urutan dict stabil) yang belum terpakai.
        """
        req = frozenset(requested)
        if req:
            for toks, sid_ in token_groups.items():
                if req <= toks and sid_ not in mapping.values():
                    return sid_
        return None

    def best_sid(code, meta):
        sid = by_code.get(norm(code)) or by_code.get(norm(meta.get("stop_code")))
        if sid:
            return sid
        sid = by_name.get(norm(meta.get("stop_name")))
        if sid:
            return sid
        deploy_name = _DEPLOY_NAME_MAP.get(code)
        if deploy_name:
            sid = match_tokens(norm(deploy_name).split())
            if sid:
                return sid
        return match_tokens(norm(meta.get("stop_name") or "").split())

    mapping = {}
    warning_rows = []
    missing = []
    for code, meta in _sd.STATIONS.items():
        sid = best_sid(code, meta)
        if sid is None:
            missing.append(code)
            continue
        mapping[code] = sid
        db_name = norm(meta.get("stop_name") or "")
        zip_name = norm(zip_stops[sid].get("stop_name") or "")
        if db_name and zip_name and db_name != zip_name:
            warning_rows.append((code, meta.get("stop_name", ""), sid, zip_stops[sid].get("stop_name", "")))
    return mapping, warning_rows, missing


def _template_extra_trips(zip_template, active_func=None):
    """Ambil trips/stop_times/calendar "extra" dari zip template.

    Trip dengan service di luar `_MANAGED_SERVICES` (mis. tahun baru malam/pagi)
    TIDAK dibangkitkan dari timetable, jadi ikut disalin agar hasil generate
    tetap sinkron dengan zip asal. Stop nonaktif tetap difilter; trip yang
    tersisa < 2 stop dibuang (selaras aturan generator).

    Mengembalikan (extra_trips, extra_stop_times, extra_calendar) sebagai
    list baris (dict per kolom file).
    """
    if not zip_template:
        return [], [], []
    with zipfile.ZipFile(io.BytesIO(zip_template)) as zf:
        names = set(zf.namelist())
        def _rows(name):
            if name not in names:
                return []
            return list(csv.DictReader(io.StringIO(zf.read(name).decode("utf-8-sig"))))
        trips = _rows("trips.txt")
        st = _rows("stop_times.txt")
        cal = _rows("calendar.txt")

    extra = [t for t in trips if (t.get("service_id") or "").strip() not in _MANAGED_SERVICES]
    if not extra:
        return [], [], []
    extra_ids = {(t.get("trip_id") or "").strip() for t in extra}
    extra_st = [r for r in st if (r.get("trip_id") or "").strip() in extra_ids]
    if active_func:
        extra_st = [r for r in extra_st if active_func((r.get("stop_id") or "").strip())]
    counts = Counter((r.get("trip_id") or "").strip() for r in extra_st)
    kept = {tid for tid, n in counts.items() if n >= 2}
    extra = [t for t in extra if (t.get("trip_id") or "").strip() in kept]
    extra_st = [r for r in extra_st if (r.get("trip_id") or "").strip() in kept]
    extra_services = {(t.get("service_id") or "").strip() for t in extra}
    extra_cal = [r for r in cal if (r.get("service_id") or "").strip() in extra_services]
    return extra, extra_st, extra_cal


def generate_gtfs_from_zip(parsed_data, zip_template, zip_stops, filter_services=None):
    """Generate GTFS baru dari timetable + struktur GTFS zip upload.

    - parsed_data: hasil parse_timetable (kode stasiun LRT).
    - zip_template: bytes GTFS .zip existing (sumber struktur & stops.txt).
    - zip_stops: dict {stop_id: {stop_code, stop_name, stop_lat, stop_lon,
        zone_id, level_id, is_active, ...}} hasil pembacaan/edit stops.txt zip.

    Mengembalikan (buf, used_stations, mapping, warnings):
    - mapping: {kode_LRT: zip_stop_id}
    - warnings: [(kode_LRT, nama_LRT, zip_stop_id, nama_zip)]
    """
    mapping, warnings, missing = build_zip_stop_mapping(zip_stops)

    zip_stops = {sid: dict(s) for sid, s in zip_stops.items()}
    for code in missing:
        meta = _sd.STATIONS[code]
        zip_stops[_deploy_stop(code)] = dict(meta, is_active=1)

    def active(sid):
        s = zip_stops.get(sid)
        return s is None or s.get("is_active", 1) != 0

    trips_lines, stop_times_lines, used_stations = generate_trips_and_stop_times(
        parsed_data, filter_services, station_map=mapping, active_func=active)

    managed_services = {line.split(",")[1] for line in trips_lines[1:]}
    managed_services = managed_services or {"service_agency_lrtj_0"}
    routes_used = {line.split(",")[0] for line in trips_lines[1:]}

    # Merge trip/service extra dari zip template (mis. tahun baru malam/pagi).
    extra_trips, extra_st, extra_cal = _template_extra_trips(zip_template, active)
    for t in extra_trips:
        trips_lines.append(f"{t.get('route_id','')},{t.get('service_id','')},{t.get('trip_id','')}")
        routes_used.add((t.get("route_id") or "").strip())
    for r in extra_st:
        stop_times_lines.append(
            f"{r.get('trip_id','')},{r.get('pickup_type','0')},{r.get('departure_time','')},"
            f"{r.get('stop_id','')},{r.get('arrival_time','')},{r.get('stop_sequence','')},"
            f"{r.get('drop_off_type','0')}")
        used_stations.add((r.get("stop_id") or "").strip())

    calendar_lines = generate_calendar(sorted(managed_services)).rstrip("\n").split("\n")
    for r in extra_cal:
        calendar_lines.append(
            f"{r.get('end_date','')},{r.get('friday','')},{r.get('monday','')},{r.get('saturday','')},"
            f"{r.get('service_id','')},{r.get('start_date','')},{r.get('sunday','')},{r.get('thursday','')},"
            f"{r.get('tuesday','')},{r.get('wednesday','')}")
    calendar_text = "\n".join(calendar_lines) + "\n"

    # stops.txt memuat SEMUA stop (termasuk yang nonaktif) agar stasiun yang
    # dinonaktifkan tidak hilang dan masih bisa diaktifkan kembali (reaktivasi)
    # dari zip hasil generate -- selaras rebuild_gtfs_without_inactive & Mass
    # Deployment. Yang difilter dari stop_times/trips hanyalah stop nonaktif.
    stop_rows = []
    for sid, s in zip_stops.items():
        stop_rows.append({
            "stop_id": s.get("stop_id") or sid,
            "stop_name": s.get("stop_name") or sid,
            "stop_lat": "%.6f" % float(s.get("stop_lat") or 0.0),
            "stop_lon": "%.6f" % float(s.get("stop_lon") or 0.0),
            "stop_code": s.get("stop_code") or sid,
            "stop_timezone": "Asia/Jakarta",
            "location_type": "0",
        })
    stops_text = "location_type,stop_code,stop_id,stop_lat,stop_lon,stop_name,stop_timezone\n"
    stops_text += "\n".join(
        "{location_type},{stop_code},{stop_id},{stop_lat},{stop_lon},{stop_name},{stop_timezone}".format(**r)
        for r in stop_rows) + "\n"

    files = {
        "agency.txt": generate_agency(),
        "calendar.txt": calendar_text,
        "routes.txt": generate_routes(routes_used),
        "stops.txt": stops_text,
        "trips.txt": "\n".join(trips_lines) + "\n",
        "stop_times.txt": "\n".join(stop_times_lines) + "\n",
        "extras.txt": generate_extras(used_stations),
        "timetables.txt": generate_timetables(routes_used, managed_services),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf, used_stations, mapping, warnings
