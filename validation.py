"""Validasi data stasiun & integritas GTFS untuk LRT Jakarta."""

import csv
import io
import zipfile


def parse_active_flag(v):
    """Ubah nilai is_active apa pun (bool/int/str/pd.NA/NaN) menjadi 0/1 aman."""
    if v is None:
        return 0
    try:
        import pandas as pd
        if pd.isna(v):
            return 0
    except (TypeError, ValueError):
        pass
    if isinstance(v, str):
        s = v.strip().lower()
        return 1 if s in ("1", "true", "yes", "on") else 0
    try:
        return 1 if int(v) == 1 else 0
    except (TypeError, ValueError):
        return 0


def validate_stop_ids(parsed_data):
    """Cross-check kode stasiun hasil parse terhadap database stasiun.

    Return dict:
      {
        "excel_count": int,
        "db_count": int,
        "missing_in_db": [...],   # kode stasiun di Excel tapi tidak ada di database
        "not_in_excel": [...],    # stasiun database yang tidak muncul di data
      }
    """
    from lrt_station_data import STATIONS

    excel_codes = set()
    for meta in parsed_data.values():
        for t in meta["trains"]:
            for s in t["stops"]:
                excel_codes.add(s["station"])

    db_codes = set(STATIONS.keys())
    missing = sorted(excel_codes - db_codes)
    not_in_excel = sorted(db_codes - excel_codes)
    return {
        "excel_count": len(excel_codes),
        "db_count": len(db_codes),
        "missing_in_db": missing,
        "not_in_excel": not_in_excel,
    }


def validate_gtfs_zip(data):
    """Validasi GTFS zip (bytes/stream) -> daftar error referensi stop_id orphan."""
    if isinstance(data, (bytes, bytearray)):
        zf = zipfile.ZipFile(io.BytesIO(data))
    elif hasattr(data, "read"):
        zf = zipfile.ZipFile(data)
    else:
        raise TypeError("data harus bytes atau file-like")

    with zf:
        names = set(zf.namelist())
        required = {"trips.txt", "stop_times.txt", "stops.txt", "routes.txt", "agency.txt", "calendar.txt"}
        missing_files = sorted(required - names)
        errors = [f"File wajib tidak ada: {f}" for f in missing_files]

        if "stops.txt" in names:
            stops = set()
            text = zf.read("stops.txt").decode("utf-8-sig")
            for row in csv.DictReader(io.StringIO(text)):
                sid = (row.get("stop_id") or "").strip()
                if sid:
                    stops.add(sid)

        if "stop_times.txt" in names and "stops.txt" in names:
            text = zf.read("stop_times.txt").decode("utf-8-sig")
            orphans = set()
            for row in csv.DictReader(io.StringIO(text)):
                sid = (row.get("stop_id") or "").strip()
                if sid and sid not in stops:
                    orphans.add(sid)
            if orphans:
                errors.append(f"stop_id orphan di stop_times: {', '.join(sorted(orphans))}")
    return errors