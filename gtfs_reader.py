"""Baca & rebuild GTFS .zip untuk LRT Jakarta (pola gtfs_reader.py)."""

import csv
import io
import json
import os
import zipfile
from datetime import datetime


def _open_zip(zip_buffer):
    if isinstance(zip_buffer, (bytes, bytearray, memoryview)):
        return zipfile.ZipFile(io.BytesIO(zip_buffer))
    return zipfile.ZipFile(zip_buffer)


def _as_zip(zip_buffer):
    if isinstance(zip_buffer, (bytes, bytearray, memoryview)):
        buf = io.BytesIO(zip_buffer)
        buf.seek(0)
        return buf
    return zip_buffer


def read_gtfs_stops(zip_buffer):
    """Read stops.txt dari GTFS zip -> list dict (stop_id, stop_name, ...)."""
    stops = []
    with _open_zip(zip_buffer) as zf:
        if "stops.txt" not in zf.namelist():
            raise ValueError("File GTFS tidak memiliki stops.txt.")
        raw = zf.read("stops.txt").decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw))
        for row in reader:
            stop_id = (row.get("stop_id") or "").strip()
            if not stop_id:
                continue
            loc = (row.get("location_type") or "").strip()
            if loc in ("2", "3", "4"):
                continue
            stops.append({
                "stop_id": stop_id,
                "stop_name": (row.get("stop_name") or "").strip(),
                "stop_code": (row.get("stop_code") or "").strip(),
                "stop_lat": (row.get("stop_lat") or "").strip(),
                "stop_lon": (row.get("stop_lon") or "").strip(),
            })
    return stops


def read_gtfs_trip_stop_ids(zip_buffer):
    """Set stop_id yang benar-benar dipakai di stop_times.txt."""
    stop_ids = set()
    with _open_zip(zip_buffer) as zf:
        if "stop_times.txt" not in zf.namelist():
            return stop_ids
        raw = zf.read("stop_times.txt").decode("utf-8-sig")
        for row in csv.DictReader(io.StringIO(raw)):
            sid = (row.get("stop_id") or "").strip()
            if sid:
                stop_ids.add(sid)
    return stop_ids


def _read_csv_rows(zf, name):
    if name not in zf.namelist():
        return None, []
    raw = zf.read(name).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    return reader.fieldnames, [dict(r) for r in reader]


INACTIVE_STOPS_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "inactive_stops.json")


def load_inactive_stops(path=INACTIVE_STOPS_DEFAULT):
    """Load daftar stop_id nonaktif dari file JSON -> set. Aman bila file hilang/rusak."""
    if not path or not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        ids = data.get("inactive_stop_ids", []) if isinstance(data, dict) else data
        return {str(x).strip() for x in ids if str(x).strip()}
    except (OSError, ValueError, TypeError):
        return set()


def save_inactive_stops(path, stop_ids):
    """Simpan daftar stop_id nonaktif ke file JSON (menang lalu lintas bil. gagal)."""
    try:
        ids = sorted({str(x).strip() for x in stop_ids if str(x).strip()})
        payload = {
            "inactive_stop_ids": ids,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return True
    except OSError:
        return False


GTFS_MASTER_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "gtfs_master.zip")

GTFS_MASTER_PREV_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "gtfs_master_prev.zip")


def load_gtfs_master(path=GTFS_MASTER_DEFAULT):
    """Bytes GTFS master (file full asal) -> bytes atau None bila tidak ada.

    Master dipakai sebagai sumber stop_times asli saat reaktivasi:
    hasil generate = master minus stops yang sedang nonaktif (urutan & waktu
    asli dipertahankan). Aman bila file hilang/rusak.
    """
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def save_gtfs_master(path, data):
    """Simpan bytes GTFS master ke file (menggantikan master lama)."""
    if not data:
        return False
    try:
        with open(path, "wb") as f:
            f.write(data)
        return True
    except OSError:
        return False


def count_gtfs_rows(zip_buffer, name):
    """Jumlah baris data (tanpa header) sebuah file di GTFS zip. 0 bila tidak ada."""
    if not zip_buffer:
        return 0
    try:
        with _open_zip(zip_buffer) as zf:
            if name not in zf.namelist():
                return 0
            raw = zf.read(name).decode("utf-8-sig")
            return max(0, len(raw.strip().splitlines()) - 1)
    except (OSError, ValueError, KeyError):
        return 0


def classify_upload(uploaded_bytes, expected_stop_ids):
    """Klasifikasi file GTFS upload untuk melindungi master GTFS.

    Deteksi orphan biasa hanya melihat stops yang ADA di stops.txt tapi TIDAK
    di stop_times.txt. File parsial yang memotong stop TIDAK ADA dari stops.txt
    sekaligus stop_times.txt luput dari deteksi itu. Fungsi ini menutup celahnya.

    Returns dict:
      - deploy_format: bool (ada stop_id berformat deploy)
      - missing_stops: list stop_id deploy yang hilang TOTAL dari stops.txt
      - orphan_ids: set stop_id ada di stops.txt tapi tanpa referensi stop_times
    """
    missing = []
    orphan_ids = set()
    try:
        present = {s["stop_id"] for s in read_gtfs_stops(uploaded_bytes)}
    except Exception:  # noqa: BLE001
        present = set()
    deploy_format = any(str(sid).startswith("stop_agency_lrtj_") for sid in present)
    if deploy_format:
        missing = sorted(
            {str(sid) for sid in (expected_stop_ids or set())} - {str(sid) for sid in present})
    try:
        orphan_ids = find_unreferenced_stops(uploaded_bytes)
    except Exception:  # noqa: BLE001
        orphan_ids = set()
    return {
        "deploy_format": deploy_format,
        "missing_stops": missing,
        "orphan_ids": orphan_ids,
    }


def find_unreferenced_stops(zip_buffer, exclude_location_types=("2", "3", "4")):
    """Stop_id di stops.txt yang TIDAK pernah dipakai stop_times.txt.

    Dipakai mendeteksi kembali stops yang sebelumnya dinonaktifkan
    pada file hasil generate (server record hilang). location_type
    igeobi (2/3/4) tidak dihitung.
    """
    with _open_zip(zip_buffer) as zf:
        names = set(zf.namelist())
        if "stops.txt" not in names or "stop_times.txt" not in names:
            return set()
        referenced = set()
        raw = zf.read("stop_times.txt").decode("utf-8-sig")
        for row in csv.DictReader(io.StringIO(raw)):
            sid = (row.get("stop_id") or "").strip()
            if sid:
                referenced.add(sid)
        all_stops = set()
        raw = zf.read("stops.txt").decode("utf-8-sig")
        for row in csv.DictReader(io.StringIO(raw)):
            sid = (row.get("stop_id") or "").strip()
            if not sid:
                continue
            loc = (row.get("location_type") or "").strip()
            if loc in exclude_location_types:
                continue
            all_stops.add(sid)
        return all_stops - referenced


def _write_csv(rows, fieldnames):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return out.getvalue().encode("utf-8")


def rebuild_gtfs_without_inactive(zip_buffer, inactive_stop_ids):
    """Rebuild GTFS zip, menghapus stops nonaktif.

    - stops.txt: KEEP semua baris (nonaktif tetap ada, hanya stop_times yang difilter).
    - stop_times.txt: drop baris yang merujuk stop nonaktif.
    - trips.txt: drop trip yang menyisakan < 2 stop (selaras gtfs_generator).
    - routes.txt / calendar.txt: drop yang tidak lagi terreferensi.
    - File lain diteruskan apa adanya.
    """
    inactive = {str(s).strip() for s in (inactive_stop_ids or set())}

    with _open_zip(zip_buffer) as zf:
        names = sorted(zf.namelist())
        out_buf = io.BytesIO()
        with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as oz:
            stops_f, stops = _read_csv_rows(zf, "stops.txt")
            st_f, st = _read_csv_rows(zf, "stop_times.txt")
            trips_f, trips = _read_csv_rows(zf, "trips.txt")
            routes_f, routes = _read_csv_rows(zf, "routes.txt")
            cal_f, cal = _read_csv_rows(zf, "calendar.txt")

            if stops_f is not None:
                oz.writestr("stops.txt", _write_csv(stops, stops_f))
            else:
                oz.writestr("stops.txt", "")

            if st_f is not None:
                kept_st = [r for r in st if (r.get("stop_id") or "").strip() not in inactive]
                kept_trip_counts = {}
                for r in kept_st:
                    tid = r.get("trip_id")
                    if tid:
                        kept_trip_counts[tid] = kept_trip_counts.get(tid, 0) + 1
                kept_trips = {tid for tid, n in kept_trip_counts.items() if n >= 2}
                kept_st = [r for r in kept_st if r.get("trip_id") in kept_trips]
                oz.writestr("stop_times.txt", _write_csv(kept_st, st_f))
            else:
                kept_trips = set()
                oz.writestr("stop_times.txt", "")

            if trips_f is not None:
                kept_trips_rows = [r for r in trips if r.get("trip_id") in kept_trips]
                active_routes = {r.get("route_id") for r in kept_trips_rows if r.get("route_id")}
                active_services = {r.get("service_id") for r in kept_trips_rows if r.get("service_id")}
                oz.writestr("trips.txt", _write_csv(kept_trips_rows, trips_f))
            else:
                active_routes = set()
                active_services = set()
                oz.writestr("trips.txt", "")

            if routes_f is not None:
                oz.writestr("routes.txt", _write_csv(
                    [r for r in routes if r.get("route_id") in active_routes], routes_f))

            if cal_f is not None:
                oz.writestr("calendar.txt", _write_csv(
                    [r for r in cal if r.get("service_id") in active_services], cal_f))

            for n in names:
                if n in ("stops.txt", "stop_times.txt", "trips.txt", "routes.txt", "calendar.txt"):
                    continue
                oz.writestr(n, zf.read(n))

        out_buf.seek(0)
        return out_buf


def backfill_stop_times_from_master(upload_bytes, master_bytes, stop_ids):
    """Sisipkan baris stop_times untuk stop tertentu dari master cadangan ke zip upload.

    Dipakai saat reaktivasi pada file hasil nonaktif: stop yang diaktifkan ulang
    baris stop_times-nya sudah TIDAK ada di zip upload (terbuang saat nonaktif),
    sehingga harus dipulihkan dari `master_bytes` (snapshot sebelum master terakhir
    ditimpa = kondisi saat nonaktif).

    - Hanya untuk trip_id yang ADA di upload (biar konsisten dengan stop lainnya).
    - Baris (trip_id, stop_id) yang sudah ada di upload tidak diduplikat.
    - Urutan baris per trip di-sort ulang per `stop_sequence` (valid GTFS & rapi).
    - Struktur/header/kolom mengikuti zip upload; file lain diteruskan apa adanya.
    """
    stop_ids = {str(s).strip() for s in (stop_ids or set())}
    if not stop_ids or not master_bytes:
        return _as_zip(upload_bytes)
    try:
        with _open_zip(upload_bytes) as zf:
            names = sorted(zf.namelist())
            if "stop_times.txt" not in names or "trips.txt" not in names:
                return _as_zip(upload_bytes)
            st_f, st = _read_csv_rows(zf, "stop_times.txt")
            if st_f is None:
                return _as_zip(upload_bytes)
            _, trips = _read_csv_rows(zf, "trips.txt")
            upload_trips = {r.get("trip_id") for r in trips if r.get("trip_id")}
            if not upload_trips:
                return _as_zip(upload_bytes)
    except (OSError, ValueError, KeyError):
        return _as_zip(upload_bytes)

    existing = {(r.get("trip_id"), (r.get("stop_id") or "").strip()) for r in st}
    try:
        with _open_zip(master_bytes) as mf:
            mst_f, mst = _read_csv_rows(mf, "stop_times.txt")
    except (OSError, ValueError, KeyError):
        mst_f, mst = None, []
    if not mst:
        return _as_zip(upload_bytes)

    added = 0
    added_by_trip = {}
    for r in mst:
        tid = r.get("trip_id")
        sid = (r.get("stop_id") or "").strip()
        if sid in stop_ids and tid in upload_trips and (tid, sid) not in existing:
            added_by_trip.setdefault(tid, []).append({f: r.get(f, "") for f in st_f})
            existing.add((tid, sid))
            added += 1
    if not added:
        return _as_zip(upload_bytes)

    def _seq(r):
        try:
            return int(r.get("stop_sequence") or 0)
        except (TypeError, ValueError):
            return 0

    # Pertahankan urutan trip persis dari file upload (bukan lexicographic).
    # Baris backfill digabung per trip, lalu stop_sequence dirutkan ulang.
    order = []
    st_by_trip = {}
    for r in st:
        tid = r.get("trip_id") or ""
        if tid not in st_by_trip:
            order.append(tid)
            st_by_trip[tid] = []
        st_by_trip[tid].append(r)
    out = []
    for tid in order:
        rows = st_by_trip[tid] + added_by_trip.get(tid, [])
        rows.sort(key=_seq)
        out.extend(rows)
    st = out

    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as oz:
        with _open_zip(upload_bytes) as zf:
            for n in names:
                if n == "stop_times.txt":
                    oz.writestr(n, _write_csv(st, st_f))
                else:
                    oz.writestr(n, zf.read(n))
    out_buf.seek(0)
    return out_buf


def apply_stop_edits(zip_buffer, edits=None, add_rows=None):
    """Edit stops.txt & referensi di stop_times.txt dalam GTFS zip.

    - edits: {old_stop_id: {"stop_id": new, "stop_name": ...}} (hanya field yang
      diberikan diubah; rename memutakhirkan referensi stop_times.txt).
    - add_rows: list dict baris stop baru (kolom = kolom stops.txt).
    """
    edits = edits or {}
    add_rows = add_rows or []

    with _open_zip(zip_buffer) as zf:
        names = sorted(zf.namelist())
        out_buf = io.BytesIO()
        with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as oz:
            stops_f, stops = _read_csv_rows(zf, "stops.txt")
            st_f, st = _read_csv_rows(zf, "stop_times.txt")

            rename = {}
            if stops_f is not None:
                existing_ids = {(r.get("stop_id") or "").strip() for r in stops}
                for old_id, changes in edits.items():
                    new_id = str(changes.get("stop_id", "") or old_id).strip()
                    if new_id and new_id != old_id and new_id not in existing_ids and new_id not in rename.values():
                        rename[old_id] = new_id

                for r in stops:
                    old_id = (r.get("stop_id") or "").strip()
                    ch = edits.get(old_id, {})
                    for field in ("stop_id", "stop_code", "stop_name"):
                        if field in ch and ch[field] not in (None, ""):
                            r[field] = str(ch[field]).strip()
                    if old_id in rename and (r.get("stop_id") or "") == old_id:
                        r["stop_id"] = rename[old_id]

                existing_ids = {(r.get("stop_id") or "").strip() for r in stops}
                for nr in add_rows:
                    sid = str(nr.get("stop_id", "")).strip()
                    if not sid or sid in existing_ids:
                        continue
                    row = {}
                    for f in stops_f:
                        row[f] = str(nr.get(f, "") or "").strip() if nr.get(f) not in (None, "") else ""
                    row["stop_id"] = sid
                    row["location_type"] = row.get("location_type") or "1"
                    stops.append(row)
                    existing_ids.add(sid)

                oz.writestr("stops.txt", _write_csv(stops, stops_f))
            else:
                oz.writestr("stops.txt", "")

            if st_f is not None and rename:
                new_rows = []
                for r in st:
                    r = dict(r)
                    sid = (r.get("stop_id") or "").strip()
                    if sid in rename:
                        r["stop_id"] = rename[sid]
                    new_rows.append(r)
                oz.writestr("stop_times.txt", _write_csv(new_rows, st_f))
            elif st_f is not None:
                oz.writestr("stop_times.txt", _write_csv(st, st_f))
            else:
                oz.writestr("stop_times.txt", "")

            for n in names:
                if n in ("stops.txt", "stop_times.txt"):
                    continue
                oz.writestr(n, zf.read(n))

        out_buf.seek(0)
        return out_buf