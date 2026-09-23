"""Parser timetable LRT Jakarta koridor Kelapa Gading (Pegangsaan Dua) - Manggarai.

Dukungan format input:
  - Excel (.xlsx): 'timetable.xlsx' (1 sheet, 2 blok
    arah dalam satu halaman: GENAP PGD->MRI di atas, GANJIL MRI->PGD di bawah).
  - CSV: auto-detect 2 bentuk:
      1. Format blok (mirip Excel): baris penanda 'TRAIN NUMBER', kolom = No KA,
         baris = stasiun + ARRIVAL/DEPARTURE.
      2. Format kolom panjang (long): tiap baris = (trip_id/No KA, station, waktu).

Kolom = nomor KA, baris = stasiun (Arrival/Departure).
Waktu > 24:00 (lewat tengah malam) direpresentasikan openpyxl sebagai
datetime(1900-01-01, hh:mm) -> tetap diambil sebagai jam; normalisasi +1
hari dilakukan saat pembuatan GTFS (gtfs_generator.normalize_trip_times).
"""

import io
import os

from datetime import time as _time

from lrt_station_data import STATIONS
from lrt_route import BLOCK_DIRECTORY

STATION_ALIASES = {
    # Blok 1 ada typo 'PEGANGSAAAN DUA'; blok 2 'PEGANGSAAN DUA'
    "PEGANGSAAAN DUA": "KPG",
    "PEGANGSAAN DUA": "KPG",
    "PEGANGSAN DUA": "KPG",
    "KELAPA GADING": "KPG",
    "BOULEVARD UTARA": "BVU",
    "BOULEVARD UTARA SUMARECON MALL": "BVU",
    "BOULEVARD SELATAN": "BVS",
    "PULOMAS": "PUM",
    "EQUESTRIAN": "EQS",
    "VELODROME": "VEL",
    "PEMUDA": "RWM",
    "RAWAMANGUN": "RWM",
    "PRAMUKA BPKP": "PRM",
    "PRAMUKA": "PRM",
    "PASAR PRAMUKA": "PSM",
    "PROKLAMASI": "PSM",
    "MATRAMAN": "MRT",
    "MANGGARAI": "MRI",
}


def _normalize_key(value):
    if value is None:
        return None
    return " ".join(str(value).upper().replace(".", " ").replace("-", " ").replace("_", " ").split())


def _name_variants(key):
    """Varian nama utk toleransi prefiks/suffix umum tanpa mengubah makna.

    Contoh: 'STASIUN LRT PROKLAMASI' -> {'STASIUN LRT PROKLAMASI',
    'PROKLAMASI'} dengan token 'STASIUN' & 'LRT' dibuang. 'MANGGARAI LRT'
    -> {'MANGGARAI LRT', 'MANGGARAI'} dst.
    """
    nouns = [t for t in key.split() if t not in ("STASIUN", "LRT")]
    cleaned = " ".join(nouns)
    if cleaned != key:
        return (key, cleaned)
    return (key,)


def resolve_station_code(label):
    """Resolusi nama stasiun dari Excel -> kode stasiun resmi.

    Hanya mencocokkan NAMA (full name dari STATIONS atau alias). Kode
    singkatan (mis. "PSM") tidak diresolusi: label singkatan di-skip.
    Toleran terhadap prefiks 'STASIUN'/'STASIUN LRT' dan suffix 'LRT'.
    """
    key = _normalize_key(label)
    if key is None:
        return None
    direct = {_normalize_key(v["stop_name"]): k for k, v in STATIONS.items()}
    alias_norm = {_normalize_key(k): v for k, v in STATION_ALIASES.items()}
    for k in _name_variants(key):
        if k in direct:
            return direct[k]
        if k in alias_norm:
            return alias_norm[k]
        if k in STATION_ALIASES:
            return STATION_ALIASES[k]
    return None


def _cell_to_time(val):
    """Konversi nilai sel (time / datetime / str) -> datetime.time atau None."""
    if val is None:
        return None
    if isinstance(val, _time):
        return val
    if hasattr(val, "hour") and hasattr(val, "minute"):
        return _time(val.hour, val.minute, val.second)
    if isinstance(val, str):
        val = val.strip()
        if val in ("", "-"):
            return None
        parts = val.split(":")
        try:
            h, m = int(parts[0]), int(parts[1])
            s = int(parts[2]) if len(parts) > 2 and parts[2] else 0
            return _time(h % 24, m, s)
        except (ValueError, IndexError):
            return None
    return None


def _merge_arrdep(rows):
    """Gabung pasangan baris ARR+DEP berurutan untuk stasiun yang sama.

    rows = [(row_idx, label, kind), ...] (kind ARR/DEP).
    STATION dengan DEP (stasiun asal) menghasilkan 2 baris (ARR lalu DEP) yang
    digabung menjadi satu stop berisi keduanya -> (row_idx, label, "ARR_DEP").
    """
    merged = []
    for r, (row, label, kind) in enumerate(rows):
        if r > 0 and kind == "DEP" and rows[r - 1][2] == "ARR" and rows[r - 1][1] == label:
            continue
        nxt = rows[r + 1] if r + 1 < len(rows) else None
        if kind == "ARR" and nxt is not None and nxt[2] == "DEP" and nxt[1] == label:
            merged.append((row, label, "ARR_DEP"))
        elif kind == "DEP":
            merged.append((row, label, "DEP"))
        else:
            merged.append((row, label, kind))
    return merged


def _build_trains(merged, header_row, get_cell, max_col):
    """Bangun daftar trains dari blok jadwal generik.

    - merged: output _merge_arrdep -> [(row_idx, label, kind)].
    - header_row: baris header (kolom disini = nomor KA).
    - get_cell(row_idx, col): aksesor nilai sel.
    - max_col: kolom terakhir yang perlu dicek.
    """
    trains = []
    for col in range(3, max_col + 1):
        raw_no = get_cell(header_row, col)
        if raw_no is None:
            continue
        no_ka = str(raw_no).strip()
        if not no_ka:
            continue
        stops = []
        for (row, label, kind) in merged:
            scode = resolve_station_code(label)
            if scode is None:
                continue
            if kind == "ARR_DEP":
                arr = _cell_to_time(get_cell(row, col))
                dep = _cell_to_time(get_cell(row + 1, col))
            elif kind == "DEP":
                arr, dep = None, _cell_to_time(get_cell(row, col))
            else:
                arr, dep = _cell_to_time(get_cell(row, col)), None
            stops.append({"station": scode, "arrival": arr, "departure": dep, "express": False})
        if len(stops) < 2:
            continue
        trains.append({"no_ka": no_ka, "service_id": "AllDay", "stops": stops})
    return trains


def _parse_block(ws, header_row, rows):
    """Parse satu blok. rows = [(row, label, kind), ...] (kind ARR/ARR_DEP/DEP)."""
    merged = _merge_arrdep(rows)
    return _build_trains(merged, header_row, lambda r, c: ws.cell(r, c).value, ws.max_column)


def _resolve_block_direction(codes):
    if not codes:
        return None
    if codes[0] == "KPG" and codes[-1] == "MRI":
        return "OUTBOUND"
    if codes[0] == "MRI" and codes[-1] == "KPG":
        return "INBOUND"
    return None


def parse_workbook(wb):
    """Parse workbook openpyxl -> dict bercabang per arah (OUTBOUND/INBOUND)."""
    results = {}
    for ws in wb.worksheets:
        header_rows = [r for r in range(1, ws.max_row + 1)
                       if ws.cell(r, 1).value is not None
                       and "TRAIN NUMBER" in str(ws.cell(r, 1).value).upper()]
        if not header_rows:
            continue

        for hr in header_rows:
            rows = []
            last_label = None
            for r in range(hr + 1, ws.max_row + 1):
                label = ws.cell(r, 1).value
                kind = ws.cell(r, 2).value
                if label is None and kind is None:
                    break
                if label is not None and "TRAIN NUMBER" in str(label).upper():
                    break
                if label is not None:
                    last_label = label
                normalized = _normalize_key(kind)
                if normalized not in ("ARRIVAL", "DEPARTURE"):
                    continue
                is_dep = normalized == "DEPARTURE"
                if last_label is None:
                    continue
                rows.append((r, last_label, "DEP" if is_dep else "ARR"))

            codes = [resolve_station_code(lb) for (_r, lb, _k) in rows]
            codes = [c for c in codes if c]
            direction = _resolve_block_direction(codes)
            if direction is None:
                continue
            trains = _parse_block(ws, hr, rows)
            if not trains:
                continue

            stations_order = []
            for (rw, lb, _k) in rows:
                c = resolve_station_code(lb)
                if c and (not stations_order or stations_order[-1][0] != c):
                    stations_order.append((c, rw))
            results[direction] = {
                "sheet_name": ws.title,
                "route_id": BLOCK_DIRECTORY[direction]["route_id"],
                "direction": BLOCK_DIRECTORY[direction]["direction"],
                "stations_order": stations_order,
                "trains": trains,
                "total_trains": len(trains),
            }
    return results


def parse_excel(filepath):
    """Parse file Excel timetable LRT -> dict bercabang per arah (OUTBOUND/INBOUND)."""
    import openpyxl

    if isinstance(filepath, (bytes, bytearray, memoryview)):
        wb = openpyxl.load_workbook(io.BytesIO(bytes(filepath)), data_only=True)
    else:
        wb = openpyxl.load_workbook(filepath, data_only=True)
    try:
        return parse_workbook(wb)
    finally:
        wb.close()


# ---------------------------------------------------------------------------
# Parser CSV: auto-detect format blok (mirip Excel) ATAU format kolom panjang.
# ---------------------------------------------------------------------------
def _csv_rows(text):
    import csv

    return list(csv.reader(io.StringIO(text)))


def _strip_cell(v):
    return None if v is None else str(v).strip()


def _parse_csv_block(rows, header_idx):
    """Parse blok format CSV mirip Excel. rows = csv.reader output.

    Baris header berisi 'TRAIN NUMBER' dengan nomor KA di kolom 3+.
    Kolom 1 = nama stasiun (atau kosong = ulangi stasiun sebelumnya),
    kolom 2 = ARRIVAL/DEPARTURE, kolom 3+ = waktu per nomor KA.
    """
    header = rows[header_idx]
    max_col = len(header)
    last_label = None
    rows_data = []
    for r in range(header_idx + 1, len(rows)):
        line = rows[r]
        if not line or not _strip_cell(line[0] if len(line) > 0 else None):
            continue
        label_raw = _strip_cell(line[0])
        if "TRAIN NUMBER" in (label_raw or "").upper():
            break
        kind_raw = _strip_cell(line[1]) if len(line) > 1 else None
        if _normalize_key(kind_raw) not in ("ARRIVAL", "DEPARTURE"):
            continue
        if label_raw:
            last_label = label_raw
        label = last_label or label_raw
        rows_data.append((r, label, "DEP" if _normalize_key(kind_raw) == "DEPARTURE" else "ARR"))

    codeseq = [resolve_station_code(lb) for (_rw, lb, _k) in rows_data]
    codeseq = [c for c in codeseq if c]
    direction = _resolve_block_direction(codeseq)
    if direction is None:
        return None
    merged = _merge_arrdep(rows_data)
    trains = _build_trains(merged, header_idx, lambda r, c: _strip_cell(rows[r][c]) if c < len(rows[r]) else None,
                           len(header))

    stations_order = []
    for (_rw, lb, _k) in rows_data:
        c = resolve_station_code(lb)
        if c and (not stations_order or stations_order[-1][0] != c):
            stations_order.append((c, _rw))
    return {
        "sheet_name": "CSV",
        "route_id": BLOCK_DIRECTORY[direction]["route_id"],
        "direction": BLOCK_DIRECTORY[direction]["direction"],
        "stations_order": stations_order,
        "trains": trains,
        "total_trains": len(trains),
    }


_LONG_ALIAS = {
    "trip": ("trip", "trip_id", "no_ka", "no ka", "train", "train_no", "nomor"),
    "station": ("station", "stasiun", "stop", "stop_name", "station_name", "name"),
    "arrival": ("arrival", "arrival_time", "tiba", "arr_time", "arr"),
    "departure": ("departure", "departure_time", "berangkat", "dep_time", "dep"),
}


def _find_col(header, kind):
    for idx, h in enumerate(header):
        norm = _normalize_key(h)
        if norm and any(a in norm for a in _LONG_ALIAS[kind]):
            return idx
    return None


def _parse_csv_long(rows):
    """Parse format CSV kolom panjang (long): satu baris per stop per trip.

    Contoh: trip_id, station, arrival, departure
            K1000, KELAPA GADING, 05:00, 05:02
            K1000, MANGGARAI, 05:45,
    """
    header = rows[0]
    col_trip = _find_col(header, "trip")
    col_sta = _find_col(header, "station")
    col_arr = _find_col(header, "arrival")
    col_dep = _find_col(header, "departure")
    if col_trip is None or col_sta is None:
        return None

    groups = {}
    order = []
    for line in rows[1:]:
        if not line or len(line) <= max(col_trip, col_sta):
            continue
        trip_raw = _strip_cell(line[col_trip])
        if not trip_raw:
            continue
        if trip_raw not in groups:
            groups[trip_raw] = []
            order.append(trip_raw)
        label = _strip_cell(line[col_sta])
        if not label:
            continue
        scode = resolve_station_code(label)
        if scode is None:
            continue
        arr = _cell_to_time(_strip_cell(line[col_arr])) if col_arr is not None and col_arr < len(line) else None
        dep = _cell_to_time(_strip_cell(line[col_dep])) if col_dep is not None and col_dep < len(line) else None
        groups[trip_raw].append({"station": scode, "arrival": arr, "departure": dep, "express": False})

    results = {}
    for trip_id in order:
        stops = [s for s in groups[trip_id] if s["arrival"] is not None or s["departure"] is not None]
        if len(stops) < 2:
            continue
        codes = [s["station"] for s in stops]
        direction = _resolve_block_direction(codes)
        if direction is None:
            continue
        meta = results.setdefault(direction, {
            "sheet_name": "CSV",
            "route_id": BLOCK_DIRECTORY[direction]["route_id"],
            "direction": BLOCK_DIRECTORY[direction]["direction"],
            "stations_order": [],
            "trains": [],
            "total_trains": 0,
        })
        meta["trains"].append({"no_ka": trip_id, "service_id": "AllDay", "stops": stops})
        for s in stops:
            if not meta["stations_order"] or meta["stations_order"][-1][0] != s["station"]:
                meta["stations_order"].append((s["station"], None))
        meta["total_trains"] = len(meta["trains"])
    return results or None


def parse_csv(source):
    """Parse timetable CSV -> dict per arah. source = teks atau bytes.

    Auto-detect: format blok (ada baris 'TRAIN NUMBER') atau format panjang.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        source = bytes(source).decode("utf-8-sig", errors="replace")
    text = source.lstrip("\ufeff")
    rows = _csv_rows(text)

    header_idxs = [i for i, r in enumerate(rows)
                   if r and _strip_cell(r[0]) and "TRAIN NUMBER" in _strip_cell(r[0]).upper()]
    if header_idxs:
        results = {}
        for hi in header_idxs:
            block = _parse_csv_block(rows, hi)
            if block and block["direction"] not in results:
                results[block["direction"]] = block
        return results
    return _parse_csv_long(rows) or {}


def parse_timetable(name_or_ext, data=None):
    """Dispatcher: parse Excel (.xlsx) atau CSV sesuai ekstensi file.

    name_or_ext = nama file / ekstensi; data = bytes isi file (jika bukan path).
    """
    name = str(name_or_ext).lower()
    ext = os.path.splitext(name)[1].lstrip(".") if "." in name else str(name_or_ext).lstrip(".").lower()
    if ext in ("xlsx", "xls"):
        return parse_excel(data if data is not None else name_or_ext)
    if ext == "csv":
        return parse_csv(data if data is not None else name_or_ext)
    raise ValueError(f"Format file tidak didukung: .{ext} (dukung: xlsx, csv)")


def get_summary(parsed_data):
    """Ringkasan hasil parsing untuk dashboard."""
    total_trains = 0
    total_stops = 0
    route_stats = []
    stations_used = set()
    for direction, meta in parsed_data.items():
        trains = meta["trains"]
        count = len(trains)
        stops = 0
        for t in trains:
            stops += len(t["stops"])
            for s in t["stops"]:
                stations_used.add(s["station"])
        total_trains += count
        total_stops += stops
        route_stats.append({
            "route_id": meta["route_id"],
            "route_long_name": meta["direction"],
            "sheet": meta["sheet_name"],
            "train_count": count,
            "stop_entries": stops,
            "stations": [x[0] for x in meta["stations_order"]],
        })
    return {
        "total_routes": len(parsed_data),
        "total_trains": total_trains,
        "total_stops_entries": total_stops,
        "routes": route_stats,
        "stations_used": sorted(stations_used),
    }