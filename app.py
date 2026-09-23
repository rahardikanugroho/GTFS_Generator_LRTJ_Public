"""Streamlit dashboard LRT Jakarta -> GTFS Converter.

Koridor Kelapa Gading (Pegangsaan Dua) - Manggarai.
Pola/struktur mengikuti dashboard referensi:
  - Sidebar: kelola stops via GTFS .zip existing + tambah stop ke database.
  - Tab utama: Overview (editable stasiun), Rute & Trip, Jadwal, Download GTFS.
"""

import hashlib
import io
import os
import uuid
import zipfile

import pandas as pd
import streamlit as st

from lrt_parser import get_summary, parse_timetable
from lrt_station_data import STATIONS
from gtfs_reader import (read_gtfs_stops, read_gtfs_trip_stop_ids, rebuild_gtfs_without_inactive,
                         backfill_stop_times_from_master, apply_stop_edits, load_inactive_stops,
                         save_inactive_stops, classify_upload, load_gtfs_master, save_gtfs_master)
import gtfs_generator as gg
from validation import validate_stop_ids, validate_gtfs_zip, parse_active_flag

st.set_page_config(
    page_title="LRT Jakarta -> GTFS Converter",
    page_icon="\U0001F685",
    layout="wide",
)

DEFAULT_XLSX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "timetable.xlsx")
MASS_DEPLOY_ZIP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "gtfs_reference.zip")
MASS_DEPLOY_LABEL = "gtfs_reference.zip"
KOLOM_STOPS = ["stop_id", "stop_code", "stop_name", "stop_lat", "stop_lon", "zone_id", "level_id"]
INACTIVE_STOPS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inactive_stops.json")
GTFS_MASTER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gtfs_master.zip")
GTFS_MASTER_PREV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "gtfs_master_prev.zip")

# Stop Referensi yang WAJIB ada di file GTFS penuh (master candidate).
_DEPLOY_ID_TO_CODE = {v["stop_id"]: k for k, v in gg._DEPLOY_STOP_MAP.items() if v.get("stop_id")}
EXPECTED_DEPLOY_STOP_IDS = set(_DEPLOY_ID_TO_CODE)


def _sort_key(v):
    """Urutkan stop_id: numerik naik bila seluruhnya angka, else string."""
    try:
        return (0, int(str(v)))
    except (TypeError, ValueError):
        return (1, str(v))


@st.cache_data(show_spinner="Parsing timetable...")
def parse_timetable_cached(name, data):
    return parse_timetable(name, data)


def resolve_uploaded(path_or_file, ext="xlsx"):
    """File upload disimpan ke temp agar bisa dipakai openpyxl & cache."""
    if hasattr(path_or_file, "read"):
        tmp = os.path.join(os.environ.get("TEMP", "."), f"lrt_gtfs_{uuid.uuid4().hex}.{ext}")
        with open(tmp, "wb") as f:
            f.write(path_or_file.getvalue())
        return tmp
    return path_or_file


def time_to_str(t):
    if t is None:
        return "-"
    return "%02d:%02d" % (t.hour, t.minute)


def stops_to_df(stops):
    rows = []
    for code, s in stops.items():
        rows.append({
            "stop_id": s.get("stop_id", code),
            "stop_code": s.get("stop_code", code),
            "stop_name": s.get("stop_name", code),
            "stop_lat": s.get("stop_lat") or 0.0,
            "stop_lon": s.get("stop_lon") or 0.0,
            "zone_id": s.get("zone_id") or "JAKARTA",
            "level_id": s.get("level_id") or "L0",
            "is_active": 1 if s.get("is_active", 1) else 0,
        })
    return pd.DataFrame(rows)


def apply_df_to_stops(df, stops):
    for code in list(stops.keys()):
        row = df.loc[df["stop_id"] == code]
        if row.empty:
            continue
        r = row.iloc[0]
        meta = stops[code]
        try:
            lat = float(r["stop_lat"])
            lon = float(r["stop_lon"])
        except (TypeError, ValueError):
            lat, lon = meta.get("stop_lat") or 0.0, meta.get("stop_lon") or 0.0
        stops[code].update({
            "stop_code": str(r["stop_code"] or code).strip(),
            "stop_name": str(r["stop_name"] or code).strip(),
            "stop_lat": lat,
            "stop_lon": lon,
            "zone_id": str(r["zone_id"] or "JAKARTA").strip(),
            "level_id": str(r["level_id"] or "L0").strip(),
            "is_active": parse_active_flag(r["is_active"]),
        })


def stops_from_zip(gtfs_stops, inactive_ids=None):
    """Konversi stops.txt GTFS zip -> dict {stop_id: meta} dengan field lengkap.

    - inactive_ids: iterable stop_id yang ditandai nonaktif (is_active=0).
    """
    inactive = {str(x).strip() for x in (inactive_ids or [])}
    out = {}
    for s in gtfs_stops:
        sid = s["stop_id"]
        try:
            lat = float(s.get("stop_lat") or 0.0)
            lon = float(s.get("stop_lon") or 0.0)
        except (TypeError, ValueError):
            lat = lon = 0.0
        out[sid] = {
            "stop_id": sid,
            "stop_code": s.get("stop_code") or sid,
            "stop_name": s.get("stop_name") or sid,
            "stop_lat": lat,
            "stop_lon": lon,
            "zone_id": "JAKARTA",
            "level_id": "L0",
            "is_active": 0 if any(sid == x or sid.upper() == x.upper() for x in inactive) else 1,
        }
    return out


@st.cache_data(show_spinner="Memuat stop list terbaru...")
def load_mass_deploy_stops():
    """Load stops.txt dari zip Referensi sebagai database default terbaru."""
    if not os.path.exists(MASS_DEPLOY_ZIP):
        return None, None
    with open(MASS_DEPLOY_ZIP, "rb") as f:
        data = f.read()
    stops = read_gtfs_stops(data)
    if not stops:
        return None, None
    return stops_from_zip(stops), data


def stop_source_label():
    if uploaded := st.session_state.get("zip_stops"):
        return f"GTFS zip ({len(uploaded)} stops)"
    return "database LRT bawaan"


st.title("\U0001F685 LRT Jakarta -> GTFS Converter")

# =====================================================================
# Sidebar: Kelola stops via GTFS .zip existing
# =====================================================================
st.sidebar.title("\U0001F4C1 Nonaktif Stops via GTFS")
st.sidebar.caption("Upload GTFS .zip untuk menonaktifkan/aktifkan stops lalu generate ulang. "
                   "Tanpa upload, dipakai zip Referensi terbaru.")

if "inactive_stations" not in st.session_state:
    persisted = load_inactive_stops(INACTIVE_STOPS_FILE)
    st.session_state["inactive_stations"] = persisted
    if persisted:
        upper_ids = {x.upper() for x in persisted}
        for code, m in STATIONS.items():
            if m.get("stop_id") in persisted or code.upper() in upper_ids:
                m["is_active"] = 0
st.session_state.setdefault("zip_stop_adds", [])
st.session_state.setdefault("zip_stops", None)
if "gtfs_master_bytes" not in st.session_state:
    st.session_state["gtfs_master_bytes"] = load_gtfs_master(GTFS_MASTER_FILE)
if "gtfs_master_prev_bytes" not in st.session_state:
    st.session_state["gtfs_master_prev_bytes"] = load_gtfs_master(GTFS_MASTER_PREV_FILE)

uploaded_gtfs = st.sidebar.file_uploader("Upload GTFS Existing (.zip)", type=["zip"],
                                         key="gtfs_zip_upload")

if uploaded_gtfs:
    try:
        gtfs_stops = read_gtfs_stops(uploaded_gtfs.getvalue())
        if not gtfs_stops:
            st.sidebar.warning("Tidak ada stops ditemukan di stops.txt GTFS tsb.")
        else:
            st.sidebar.success(f"{len(gtfs_stops)} stops dibaca dari GTFS.")
            st.session_state["zip_gtfs_bytes"] = uploaded_gtfs.getvalue()
            uploaded_ids = {s["stop_id"] for s in gtfs_stops}

            # Deteksi stops deploy yang hilang TOTAL (stops.txt & stop_times.txt).
            # Deteksi orphan biasa tidak menangkap kasus ini (stops-nya sudah dipotong).
            upload_info = classify_upload(uploaded_gtfs.getvalue(), EXPECTED_DEPLOY_STOP_IDS)
            missing_full = upload_info["missing_stops"]

            master_meta = {}
            mb = st.session_state.get("gtfs_master_bytes")
            if mb:
                try:
                    master_meta = {s["stop_id"]: s for s in read_gtfs_stops(mb)}
                except Exception:  # noqa: BLE001
                    master_meta = {}

            def _fallback_meta(sid):
                m = master_meta.get(sid)
                if m:
                    return m
                code = _DEPLOY_ID_TO_CODE.get(sid)
                db = STATIONS.get(code) if code else None
                if db:
                    return db
                v = gg._DEPLOY_STOP_MAP.get(code or "", {})
                return v

            gtfs_name = {s["stop_id"]: s.get("stop_name", "") for s in gtfs_stops}
            gtfs_code = {s["stop_id"]: s.get("stop_code", "") for s in gtfs_stops}
            for sid in missing_full:
                fm = _fallback_meta(sid)
                if not gtfs_name.get(sid):
                    gtfs_name[sid] = fm.get("stop_name") or sid
                if not gtfs_code.get(sid):
                    gtfs_code[sid] = fm.get("stop_code") or sid

            def _code_sort_key(sid):
                c = gtfs_code.get(sid, "") or ""
                try:
                    return (0, int(str(c)))
                except (TypeError, ValueError):
                    return (1, str(c))

            gtfs_options = sorted(uploaded_ids | set(missing_full), key=_code_sort_key)

            def _fmt_stop(c):
                return f"{gtfs_code.get(c, '')} - {gtfs_name.get(c, '')}"

            # HANYA adopsi state pada file baru (fingerprint berubah), bukan tiap rerun.
            # Tanpa ini, deteksi orphan akan MENIMPA reaktivasi user di rerun berikutnya.
            upload_fp = hashlib.md5(uploaded_gtfs.getvalue()).hexdigest()
            if st.session_state.get("_gtfs_upload_fp") != upload_fp:
                st.session_state["_gtfs_upload_fp"] = upload_fp
                prev_inactive = set(st.session_state.get("inactive_stations", set()))
                prev_inactive |= {sid for sid, m in (st.session_state.get("zip_stops") or {}).items()
                                  if not m.get("is_active", 1)}
                orphan_ids = upload_info["orphan_ids"]
                new_orphans = orphan_ids - prev_inactive
                prev_inactive |= orphan_ids
                prev_inactive |= set(missing_full)
                st.session_state["inactive_stations"] = prev_inactive
                zip_stops = stops_from_zip(gtfs_stops, prev_inactive)
                # Stop yang dipotong total dari file tetap dimasukkan (nonaktif) supaya
                # tetap bisa diaktifkan kembali via sidebar (meta dari master/database).
                for sid in missing_full:
                    if sid in zip_stops:
                        zip_stops[sid]["is_active"] = 0
                        continue
                    fm = _fallback_meta(sid)
                    try:
                        lat = float(fm.get("stop_lat") or 0.0)
                        lon = float(fm.get("stop_lon") or 0.0)
                    except (TypeError, ValueError):
                        lat = lon = 0.0
                    zip_stops[sid] = {
                        "stop_id": sid,
                        "stop_code": fm.get("stop_code") or sid,
                        "stop_name": fm.get("stop_name") or sid,
                        "stop_lat": lat,
                        "stop_lon": lon,
                        "zone_id": "JAKARTA",
                        "level_id": "L0",
                        "is_active": 0,
                    }
                st.session_state["zip_stops"] = zip_stops
                if new_orphans:
                    st.sidebar.info(f"Deteksi dari file: {len(new_orphans)} stops tanpa stop_times "
                                    f"dianggap nonaktif (record server mungkin hilang). "
                                    f"Aktifkan lewat \u2705 Aktifkan Kembali: {', '.join(sorted(new_orphans))}")
                if missing_full:
                    st.sidebar.info(f"{len(missing_full)} stops TIDAK ada di file upload (terpotong "
                                    f"total dari stops.txt & stop_times.txt). Dianggap nonaktif & tetap "
                                    f"bisa diaktifkan kembali dari master: {', '.join(missing_full)}")
                # Kebijakan master: zip yang di-upload ke sidebar LANGSUNG menjadi
                # master (sumber aktivasi & nonaktivasi). Master lama otomatis
                # di-backup ke gtfs_master_prev.zip â€” dipakai sebagai sumber
                # cadangan bila stop yang diaktifkan ulang barisnya terbuang dari
                # file upload (file hasil nonaktif) â†’ di-backfill saat generate.
                prev_master = st.session_state.get("gtfs_master_bytes")
                new_bytes = uploaded_gtfs.getvalue()
                if prev_master is not None and prev_master != new_bytes:
                    st.session_state["gtfs_master_prev_bytes"] = prev_master
                    save_gtfs_master(GTFS_MASTER_PREV_FILE, prev_master)
                st.session_state["gtfs_master_bytes"] = new_bytes
                save_gtfs_master(GTFS_MASTER_FILE, new_bytes)
                st.sidebar.caption("Master GTFS = zip yang Anda upload (sumber aktivasi & "
                                   "nonaktivasi). Master lama tersimpan otomatis sebagai cadangan.")
                st.session_state["multi_nonaktif"] = sorted(prev_inactive & set(gtfs_options), key=_code_sort_key)

            if "_sidebar_msg" in st.session_state:
                st.sidebar.success(st.session_state.pop("_sidebar_msg"))

            if "_sync_multi_nonaktif" in st.session_state:
                st.session_state["multi_nonaktif"] = st.session_state.pop("_sync_multi_nonaktif")
            if "_sync_multi_aktifkan" in st.session_state:
                st.session_state["multi_aktifkan"] = st.session_state.pop("_sync_multi_aktifkan")

            st.session_state.setdefault(
                "multi_nonaktif",
                sorted(st.session_state.get("inactive_stations", set()) & set(gtfs_options),
                       key=_code_sort_key),
            )
            inactive = st.sidebar.multiselect(
                "Pilih stops yang dinonaktifkan",
                options=gtfs_options,
                format_func=_fmt_stop,
                key="multi_nonaktif",
            )
            gp = st.sidebar.button("Mulai Nonaktif Dari Zip", type="primary", width="stretch")
            if gp:
                selected = set(inactive)
                for zsid, m in st.session_state["zip_stops"].items():
                    m["is_active"] = 0 if zsid in selected else 1
                st.session_state["inactive_stations"] = selected & set(gtfs_options)
                save_inactive_stops(INACTIVE_STOPS_FILE, st.session_state["inactive_stations"])
                st.session_state["_sync_multi_nonaktif"] = sorted(selected & set(gtfs_options), key=_code_sort_key)
                st.session_state["_sidebar_msg"] = f"{len(selected)} stops ditandai nonaktif."
                st.rerun()

            now_inactive = st.session_state["inactive_stations"] & set(gtfs_options)
            st.sidebar.divider()
            st.sidebar.markdown("### \u2705 Aktifkan Kembali")
            if now_inactive:
                st.sidebar.caption(
                    "Setelah diaktifkan, klik \"Generate GTFS Baru (Dari Zip Ini)\" \u2014 stop_times "
                    "di-restore dari master GTFS yang tersimpan (urutan & waktu asli)."
                )
                reactivate = st.sidebar.multiselect(
                    "Pilih stops untuk diaktifkan kembali",
                    options=sorted(now_inactive, key=_code_sort_key),
                    format_func=_fmt_stop,
                    key="multi_aktifkan",
                )
                if st.sidebar.button("Aktifkan Pilihan", width="stretch"):
                    for zsid in reactivate:
                        m = st.session_state["zip_stops"].get(zsid)
                        if m is not None:
                            m["is_active"] = 1
                    st.session_state["inactive_stations"] -= set(reactivate)
                    remaining = st.session_state["inactive_stations"] & set(gtfs_options)
                    save_inactive_stops(INACTIVE_STOPS_FILE, remaining)
                    st.session_state["_sync_multi_nonaktif"] = sorted(remaining, key=_code_sort_key)
                    st.session_state["_sync_multi_aktifkan"] = []
                    st.session_state["_sidebar_msg"] = "Pilihan diaktifkan kembali."
                    st.rerun()
                if st.sidebar.button("Aktifkan Semua", width="stretch"):
                    for m in st.session_state["zip_stops"].values():
                        m["is_active"] = 1
                    st.session_state["inactive_stations"] = set()
                    save_inactive_stops(INACTIVE_STOPS_FILE, set())
                    st.session_state["_sync_multi_nonaktif"] = []
                    st.session_state["_sync_multi_aktifkan"] = []
                    st.session_state["_sidebar_msg"] = "Semua stops diaktifkan kembali."
                    st.rerun()
            else:
                st.sidebar.caption("Tidak ada stops yang sedang nonaktif.")

            with st.sidebar.expander("\u2795 Tambah Stop ke Zip", expanded=False):
                z_id = st.text_input("Stop ID", key="zip_add_id")
                z_code = st.text_input("Stop Code", key="zip_add_scode")
                z_name = st.text_input("Stop Name", key="zip_add_name")
                z_lat = st.text_input("Latitude", key="zip_add_lat")
                z_lon = st.text_input("Longitude", key="zip_add_lon")
                if st.button("Tambah Stop ke Zip", width="stretch"):
                    sid = (z_id or "").strip().upper()
                    if not sid:
                        st.sidebar.error("Stop ID wajib diisi.")
                    else:
                        st.session_state["zip_stop_adds"].append({
                            "stop_id": sid,
                            "stop_code": (z_code or "").strip() or sid,
                            "stop_name": (z_name or "").strip() or sid,
                            "stop_lat": (z_lat or "").strip(),
                            "stop_lon": (z_lon or "").strip(),
                        })
                        st.sidebar.success(f"Stop '{sid}' siap ditambahkan.")

            effective = {sid for sid, m in st.session_state["zip_stops"].items() if not m.get("is_active", 1)}
            effective &= set(gtfs_options)
            if effective:
                st.sidebar.warning(f"{len(effective)} stops tidak akan muncul di stop_times: "
                                   f"{', '.join(sorted(effective))}")
            if st.sidebar.button(
                "\U0001F685 Generate GTFS Baru (Dari Zip Ini)",
                type="primary", width="stretch",
            ):
                with st.spinner("Membangun GTFS dari zip yang di-upload (urutan & waktu asli)..."):
                    base = st.session_state.get("gtfs_master_bytes") or uploaded_gtfs.getvalue()
                    # Backfill: stop yang SEDANG AKTIF tapi barisnya sudah TIDAK ada di
                    # zip upload (file hasil nonaktif) â†’ dipulihkan dari master cadangan
                    # (gtfs_master_prev.zip = snapshot sebelum master terakhir ditimpa).
                    have_ids = read_gtfs_trip_stop_ids(base)
                    active_ids = {sid for sid, m in st.session_state["zip_stops"].items()
                                  if m.get("is_active", 1)}
                    restored = (active_ids & set(gtfs_options)) - have_ids
                    master_prev = st.session_state.get("gtfs_master_prev_bytes")
                    if restored and master_prev:
                        base = backfill_stop_times_from_master(base, master_prev, restored)
                        done = read_gtfs_trip_stop_ids(base) & restored
                        if done:
                            st.sidebar.caption(
                                f"Stop dipulihkan dari master cadangan: "
                                f"{', '.join(sorted(done))} \u2014 baris stop_times dikembalikan.")
                    if st.session_state.get("zip_stop_adds"):
                        base = apply_stop_edits(base, add_rows=st.session_state["zip_stop_adds"]).getvalue()
                    st.session_state["gtfs_zip_sidebar"] = rebuild_gtfs_without_inactive(base, effective)
                    errs = validate_gtfs_zip(st.session_state["gtfs_zip_sidebar"].getvalue())
                st.sidebar.success("GTFS baru berhasil dibuat (stop_times asli dipertahankan, "
                                   "hanya stops nonaktif yang dibuang).")
                if errs:
                    st.sidebar.warning("\n".join(errs))
                if st.session_state.get("gtfs_master_bytes") is None:
                    st.sidebar.info("Master GTFS belum tersimpan \u2014 file yang baru di-upload "
                                    "dipakai sebagai sumber stop_times.")
            if "gtfs_zip_sidebar" in st.session_state:
                st.sidebar.download_button(
                    "\u2b07 Download GTFS Baru (.zip)",
                    data=st.session_state["gtfs_zip_sidebar"].getvalue(),
                    file_name="lrt_gtfs_setelah_nonaktif.zip",
                    mime="application/zip",
                    width="stretch",
                )
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"Gagal membaca GTFS: {e}")
else:
    if st.session_state.get("zip_stops") is None:
        def_stops, def_bytes = load_mass_deploy_stops()
        if def_stops is not None:
            def_stops = {k: dict(v) for k, v in def_stops.items()}
            for sid in st.session_state.get("inactive_stations", set()):
                if sid in def_stops:
                    def_stops[sid]["is_active"] = 0
            st.session_state["zip_stops"] = def_stops
            st.session_state["zip_gtfs_bytes"] = def_bytes
            if st.session_state.get("gtfs_master_bytes") is None:
                st.session_state["gtfs_master_bytes"] = def_bytes
    if st.session_state.get("zip_stops"):
        inact_ids = sorted(
            (sid for sid, m in st.session_state["zip_stops"].items() if not m.get("is_active", 1)),
            key=str.lower,
        )
        if inact_ids:
            st.sidebar.divider()
            st.sidebar.markdown("### \u274c Stasiun Sedang Nonaktif")
            for sid in inact_ids:
                m = st.session_state["zip_stops"].get(sid) or {}
                label = f"{m.get('stop_name', sid)} ({sid})" if m.get("stop_name") else sid
                st.sidebar.write(f"- {label}")
            st.sidebar.markdown("### \u2705 Aktifkan Kembali")
            reactivate_default = st.sidebar.multiselect(
                "Pilih stops untuk diaktifkan kembali",
                options=inact_ids,
                format_func=lambda c: f"{st.session_state['zip_stops'].get(c, {}).get('stop_code', c)} - "
                                      f"{st.session_state['zip_stops'].get(c, {}).get('stop_name', c)}",
            )
            if st.sidebar.button("Aktifkan Pilihan", width="stretch"):
                for zsid in reactivate_default:
                    m = st.session_state["zip_stops"].get(zsid)
                    if m is not None:
                        m["is_active"] = 1
                st.session_state["inactive_stations"] -= set(reactivate_default)
                save_inactive_stops(INACTIVE_STOPS_FILE, st.session_state["inactive_stations"])
                st.rerun()
            if st.sidebar.button("Aktifkan Semua", width="stretch"):
                for m in st.session_state["zip_stops"].values():
                    m["is_active"] = 1
                st.session_state["inactive_stations"] = set()
                save_inactive_stops(INACTIVE_STOPS_FILE, set())
                st.rerun()
            st.sidebar.caption("Record nonaktif tersimpan permanen di "
                               f"`{os.path.basename(INACTIVE_STOPS_FILE)}` \u2013 tidak hilang saat restart.")
        else:
            st.sidebar.caption("Tidak ada stops yang sedang nonaktif.")
    else:
        st.sidebar.info("Upload file GTFS .zip untuk mengelola nonaktif stops.")

# =====================================================================
# Sidebar: Tambah stasiun ke database (dipakai generate FULL GTFS)
# =====================================================================
with st.sidebar.expander("\u2795 Tambah Stasiun (Database)", expanded=False):
    new_code = st.text_input("Code / Stop ID", key="add_code")
    new_scode = st.text_input("Stop Code", key="add_stopcode")
    new_name = st.text_input("Stop Name", key="add_name")
    new_lat = st.text_input("Latitude", key="add_lat")
    new_lon = st.text_input("Longitude", key="add_lon")
    if st.button("Tambah ke Database", type="primary", width="stretch"):
        code = (new_code or "").strip().upper()
        if not code:
            st.sidebar.error("Code / Stop ID wajib diisi.")
        elif code in STATIONS:
            st.sidebar.error(f"Code '{code}' sudah ada di database.")
        else:
            try:
                lat = float(new_lat) if new_lat else None
                lon = float(new_lon) if new_lon else None
            except ValueError:
                lat = lon = None
            if gg.add_station(code, stop_code=new_scode, stop_name=new_name,
                              stop_lat=lat, stop_lon=lon):
                st.sidebar.success(f"Stasiun '{code}' ditambahkan ke database.")
            else:
                st.sidebar.error("Gagal menambah stasiun.")

st.sidebar.header("Database Stasiun")
db_zip = st.session_state.get("zip_stops")
total = len(db_zip) if db_zip else len(STATIONS)
nonaktif = sum(1 for m in db_zip.values() if not m.get("is_active", 1)) if db_zip \
    else len(gg.get_inactive_stations())
st.sidebar.metric("Jumlah Stasiun", total)
st.sidebar.metric("Aktif", total - nonaktif)
st.sidebar.metric("Nonaktif", nonaktif)

# =====================================================================
# Input & Parse (Excel / CSV)
# =====================================================================
timetable_file = st.file_uploader(
    "Upload Timetable (xlsx / csv)",
    type=["xlsx", "csv"],
    key="main_timetable",
    help="Excel memakai layout blok PGD-MRI (seperti file default). CSV bisa "
         "format blok (ada baris 'TRAIN NUMBER') atau format kolom panjang "
         "(trip_id, station, arrival, departure). PDF menyusul.",
)
if timetable_file is None and os.path.exists(DEFAULT_XLSX):
    with open(DEFAULT_XLSX, "rb") as f:
        default_bytes = f.read()
        default_name = os.path.basename(DEFAULT_XLSX)
else:
    default_name, default_bytes = None, None

if timetable_file is None and default_name is None:
    st.info("Upload file timetable (Excel/CSV) untuk memulai.")
    st.stop()

file_name = timetable_file.name if timetable_file is not None else default_name
file_data = timetable_file.getvalue() if timetable_file is not None else default_bytes

try:
    parsed = parse_timetable_cached(file_name, file_data)
except Exception as e:  # noqa: BLE001
    st.error(f"Gagal membaca timetable: {e}")
    st.stop()

if not parsed:
    st.error("Tidak ditemukan blok jadwal yang dikenal dalam file.")
    st.stop()

summary = get_summary(parsed)
validation = validate_stop_ids(parsed)

st.header("Ringkasan")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Arah/Rute", summary["total_routes"])
c2.metric("Jumlah Trip", summary["total_trains"])
c3.metric("Entri Stop", summary["total_stops_entries"])
c4.metric("Stasiun di Excel", validation["excel_count"])

if validation["missing_in_db"]:
    st.error("Kode stasiun tidak dikenal di database: " + ", ".join(validation["missing_in_db"]))
else:
    st.success("Semua stasiun dari timetable terpetakan ke database LRT.")

map_warns = []
map_missing = []
if st.session_state.get("zip_stops"):
    mapping, map_warns, map_missing = gg.build_zip_stop_mapping(st.session_state["zip_stops"])
    if map_warns:
        with st.expander(f"Perbedaan nama LRT vs stop_name GTFS zip ({len(map_warns)}):"):
            for code, dbn, zid, zpn in map_warns:
                st.write(f"- **{code}**: database '{dbn}' \u2192 zip '{zid}' ({zpn})")
    if map_missing:
        st.warning(f"Tidak ditemukan di zip: {', '.join(map_missing)} (akan disisipkan "
                   f"dari database LRT saat generate).")

tabs = st.tabs(["Overview", "Rute & Trip", "Jadwal", "Download GTFS"])

with tabs[0]:
    st.subheader("\u2699\ufe0f Manajemen Stasiun (Aktif / Nonaktif + Edit)")
    zip_stops = st.session_state.get("zip_stops")
    if zip_stops:
        src = MASS_DEPLOY_LABEL if not uploaded_gtfs else "GTFS zip upload"
        st.caption(
            f"Sumber: **{src}** ({len(zip_stops)} stops \u2013 data terbaru). Edit nama/koordinat langsung "
            "di tabel; centang/lepas **Aktif** untuk menentukan stasiun yang muncul di "
            "stop_times & trips saat generate. Upload GTFS .zip lain di sidebar untuk mengganti sumber."
        )
        stns = zip_stops
        apply_target = "zip"
    else:
        st.caption(
            "Sumber: **database LRT bawaan** (upload GTFS zip di sidebar untuk memakai "
            "stop list dari zip Anda). Edit nama/koordinat langsung; centang/lepas **Aktif** "
            "untuk nonaktif di GTFS final."
        )
        stns = STATIONS
        apply_target = "db"
    df_db = stops_to_df(stns)
    edited = st.data_editor(
        df_db,
        hide_index=True,
        width="stretch",
        disabled=["stop_id"],
        num_rows="fixed",
        column_config={
            "is_active": st.column_config.CheckboxColumn("Aktif", default=True),
            "stop_lat": st.column_config.NumberColumn(format="%.6f"),
            "stop_lon": st.column_config.NumberColumn(format="%.6f"),
        },
    )
    if st.button("Terapkan Perubahan Stasiun"):
        apply_df_to_stops(edited, stns)
        if apply_target == "zip":
            inactive_now = [sid for sid, m in zip_stops.items() if not m.get("is_active", 1)]
            st.session_state["inactive_stations"] = set(inactive_now)
            save_inactive_stops(INACTIVE_STOPS_FILE, set(inactive_now))
        else:
            inactive_now = gg.get_inactive_stations()
        if inactive_now:
            st.warning(f"{len(inactive_now)} stasiun dinonaktifkan: {', '.join(inactive_now)}")
        else:
            st.info("Semua stasiun aktif saat ini.")
        st.rerun()

    st.subheader("Validasi Stasiun")
    vc1, vc2, vc3 = st.columns(3)
    vc1.metric("Kode di Excel", validation["excel_count"])
    if zip_stops:
        vc2.metric("Stops di Zip", len(zip_stops))
        vc3.metric("Stasiun LRT", len(STATIONS))
    else:
        vc2.metric("Stasiun di Database", validation["db_count"])
        vc3.metric("Tidak Ada di Database", len(validation["missing_in_db"]))
    with st.expander("Detail validasi m\u00e1pping", expanded=False):
        if map_warns or map_missing:
            st.error(f"Mapping belum 100%: {len(map_warns)} beda nama, {len(map_missing)} tidak ditemukan.")
        else:
            st.success("Semua stasiun LRT cocok dengan stop_name di zip.")
        st.json(validation)

with tabs[1]:
    for direction, meta in parsed.items():
        with st.expander(f"Rute {meta['route_id']} \u2014 {meta['direction']} "
                         f"({meta['total_trains']} trip, sheet {meta['sheet_name']})"):
            st.code(" -> ".join(code for code, _ in meta["stations_order"]))
            st.dataframe(pd.DataFrame([{
                "No. KA": t["no_ka"],
                "Arah": meta["direction"],
                "Stops": len(t["stops"]),
                "Service": t.get("service_id", "AllDay"),
            } for t in meta["trains"]]), hide_index=True, width="stretch")

with tabs[2]:
    zip_stops = st.session_state.get("zip_stops")
    name_src = zip_stops
    if name_src is None:
        name_src = STATIONS

    def stop_display(code):
        meta = name_src.get(code)
        if meta is None and zip_stops:
            for sid, m in zip_stops.items():
                if sid.upper() == code.upper():
                    return m.get("stop_name", code)
        return meta.get("stop_name") if meta else code

    direction = st.selectbox("Pilih arah", list(parsed.keys()),
                             format_func=lambda d: f"{d} ({parsed[d]['direction']})")
    meta = parsed[direction]
    selected = st.selectbox("Pilih No. KA", [t["no_ka"] for t in meta["trains"]])
    trip = next(t for t in meta["trains"] if t["no_ka"] == selected)
    st.dataframe(pd.DataFrame([{
        "Stasiun": s["station"],
        "Nama": stop_display(s["station"]),
        "Tiba": time_to_str(s.get("arrival")),
        "Berangkat": time_to_str(s.get("departure")),
    } for s in trip["stops"]]), hide_index=True, width="stretch")

    st.subheader("Filter / pencarian")
    q = st.text_input("Cari No. KA", "")
    matching = [t for t in meta["trains"] if q.strip() == "" or q.strip() in str(t["no_ka"])]
    matrix = []
    stns = [code for code, _ in meta["stations_order"]]
    for t in matching[:60]:
        row = {"No. KA": t["no_ka"]}
        for s in t["stops"]:
            row[s["station"]] = time_to_str(s["arrival"])
        matrix.append(row)
    if matrix:
        st.dataframe(pd.DataFrame(matrix)[["No. KA"] + stns], hide_index=True, width="stretch")

with tabs[3]:
    st.subheader("Generate GTFS (.zip)")
    services = st.multiselect(
        "Service calendar",
        ["AllDay", "Weekday", "Weekend"],
        default=["AllDay"],
        help="Timetable ini beroperasi setiap hari (AllDay).",
    )
    zip_stops = st.session_state.get("zip_stops")
    if zip_stops and st.session_state.get("zip_gtfs_bytes"):
        src = MASS_DEPLOY_LABEL if not uploaded_gtfs else "GTFS zip upload"
        st.caption(
            f"Mode **Zip + Timetable** \u2014 struktur & stop list mengikuti **{src}** "
            f"({len(zip_stops)} stops, data terbaru), trips & stop_times dibuat dari timetable "
            "yang Anda upload. Edit stops / is_active di tab Overview."
        )
        if st.button("Generate GTFS Dari Zip + Timetable", type="primary"):
            with st.spinner("Menyusun GTFS dari zip + timetable..."):
                buf, used, map_rows, warn_rows = gg.generate_gtfs_from_zip(
                    parsed, st.session_state["zip_gtfs_bytes"],
                    st.session_state["zip_stops"], services)
                data = buf.getvalue()
                st.session_state["gtfs_bytes"] = data
                st.session_state["gtfs_summary"] = gg.get_summary_gtfs(parsed)
                st.session_state["gtfs_source"] = "zip"
                st.success(f"GTFS terbentuk: {len(used)} stops dipakai, "
                           f"{st.session_state['gtfs_summary']['total_trips']} trips.")
    else:
        st.caption(
            "Mode **Full** (database LRT) \u2014 upload GTFS .zip di sidebar untuk "
            "generate dari struktur zip Anda."
        )
        if st.button("Generate GTFS (Full)", type="primary", key="gen_full"):
            with st.spinner("Menyusun GTFS..."):
                buf, used = gg.generate_gtfs_zip(parsed, services)
                data = buf.getvalue()
                st.session_state["gtfs_bytes"] = data
                st.session_state["gtfs_summary"] = gg.get_summary_gtfs(parsed)
                st.session_state["gtfs_source"] = "db"
                st.success(f"GTFS terbentuk: {len(used)} stops dipakai, "
                           f"{st.session_state['gtfs_summary']['total_trips']} trips.")

    if st.session_state.get("gtfs_bytes") is not None:
        errors = validate_gtfs_zip(st.session_state["gtfs_bytes"])
        if errors:
            st.warning("\n".join(errors))
        else:
            st.success("GTFS valid \u2013 tidak ada orphan stop_id.")
        fname = "lrt_from_zip_timetable_gtfs.zip" if st.session_state.get("gtfs_source") == "zip" \
            else "lrt_jakarta_pgd_mri_gtfs.zip"
        st.download_button(
            "Download GTFS (.zip)",
            data=st.session_state["gtfs_bytes"],
            file_name=fname,
            mime="application/zip",
        )
        st.subheader("Preview isi file")
        preview = st.selectbox("Pilih file", [
            "agency.txt", "calendar.txt", "routes.txt", "stops.txt",
            "trips.txt", "stop_times.txt", "extras.txt", "timetables.txt"])
        with zipfile.ZipFile(io.BytesIO(st.session_state["gtfs_bytes"])) as z:
            text = z.read(preview).decode("utf-8-sig")
        st.code("\n".join(text.splitlines()[:15]))
        st.caption(f"{len(text.splitlines())} baris total (ditampilkan 15 baris pertama)")
