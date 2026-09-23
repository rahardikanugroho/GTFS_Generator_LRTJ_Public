"""Unit test LRT Jakarta -> GTFS.

Menjalankan mode pemeliharaan:
  - engine & reader
  - parse Excel + generate GTFS end-to-end
Tetap bisa dijalankan sekalipun file Excel tidak ditemukan (test yang
bergantung file akan di-skip otomatis).
"""

import io
import os
import unittest
import zipfile
from datetime import time

import pandas as pd

import gtfs_generator as gg
import gtfs_reader as gr
from lrt_parser import parse_excel, get_summary, resolve_station_code
from lrt_station_data import STATIONS
from validation import validate_stop_ids, validate_gtfs_zip, parse_active_flag

XLSX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "timetable.xlsx")
MASS_ZIP = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "gtfs_reference.zip")


def _parse():
    if not os.path.exists(XLSX):
        return None
    return parse_excel(XLSX)


class TestStationDatabase(unittest.TestCase):
    def test_11_stations(self):
        self.assertEqual(len(STATIONS), 11)

    def test_station_activity(self):
        gg.reset_station_activity()
        gg.set_station_active("MRI", False)
        self.assertIn("MRI", gg.get_inactive_stations())
        self.assertFalse(gg.is_station_active("MRI"))
        gg.reset_station_activity()
        self.assertEqual(gg.get_inactive_stations(), [])

    def test_add_station(self):
        gg.reset_station_activity()
        ok = gg.add_station("XYZ", stop_name="Stasiun Uji", stop_lat=-6.2, stop_lon=106.8)
        self.assertTrue(ok)
        self.assertIn("XYZ", STATIONS)
        gg.set_station_info("XYZ", stop_name="Stasiun Uji Baru")
        self.assertEqual(STATIONS["XYZ"]["stop_name"], "Stasiun Uji Baru")
        del STATIONS["XYZ"]
        self.assertFalse(gg.add_station("MRI", stop_name="dup"))


class TestResolver(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(resolve_station_code("PEGANGSAAAN DUA"), "KPG")
        self.assertEqual(resolve_station_code("PEGANGSAAN DUA"), "KPG")
        self.assertEqual(resolve_station_code("PEMUDA"), "RWM")
        self.assertEqual(resolve_station_code("PRAMUKA BPKP"), "PRM")
        self.assertEqual(resolve_station_code("PASAR PRAMUKA"), "PSM")
        self.assertEqual(resolve_station_code("PROKLAMASI"), "PSM")
        self.assertEqual(resolve_station_code("MANGGARAI"), "MRI")
        self.assertEqual(resolve_station_code("KELAPA GADING LRT"), "KPG")
        self.assertIsNone(resolve_station_code("STASIUN TAK DIKENAL"))
        self.assertIsNone(resolve_station_code("PSM"))
        self.assertIsNone(resolve_station_code("MRI"))

    def test_name_variants(self):
        self.assertEqual(resolve_station_code("STASIUN LRT PROKLAMASI"), "PSM")
        self.assertEqual(resolve_station_code("STASIUN PROKLAMASI"), "PSM")
        self.assertEqual(resolve_station_code("PASAR PRAMUKA LRT"), "PSM")
        self.assertEqual(resolve_station_code("STASIUN LRT KELAPA GADING"), "KPG")
        self.assertEqual(resolve_station_code("KELAPA GADING LRT"), "KPG")
        self.assertEqual(resolve_station_code("MANGGARAI LRT"), "MRI")
        self.assertEqual(resolve_station_code("STASIUN LRT MANGGARAI"), "MRI")
        self.assertIsNone(resolve_station_code("STASIUN LRT XYZ"))


class TestNormalize(unittest.TestCase):
    def test_midnight_crossing(self):
        gg.reset_station_activity()
        stops = [
            {"station": "MRI", "arrival": None, "departure": time(23, 48), "express": False},
            {"station": "MRT", "arrival": time(23, 51), "departure": None, "express": False},
            {"station": "KPG", "arrival": time(0, 18), "departure": None, "express": False},
        ]
        recs = gg.normalize_trip_times(stops)
        self.assertEqual(recs[0]["departure"], 23 * 60 + 48)
        self.assertEqual(recs[1]["arrival"], 23 * 60 + 51)
        self.assertEqual(recs[2]["arrival"], 24 * 60 + 18)


class TestGenerator(unittest.TestCase):
    def setUp(self):
        gg.reset_station_activity()

    def _gen(self, parsed):
        buf, used = gg.generate_gtfs_zip(parsed, None)
        return buf.getvalue(), used

    def test_gtfs_structure(self):
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        data, used = self._gen(parsed)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for f in ("agency.txt", "calendar.txt", "routes.txt", "stops.txt",
                      "trips.txt", "stop_times.txt", "extras.txt", "timetables.txt"):
                self.assertIn(f, z.namelist())
        self.assertEqual(validate_gtfs_zip(data), [])

    def test_generate_with_inactive_stop(self):
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        gg.set_station_active("VEL", False)
        data, used = self._gen(parsed)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            st = z.read("stop_times.txt").decode("utf-8-sig")
            self.assertNotIn("stop_agency_lrtj_6", st)
            stops = z.read("stops.txt").decode("utf-8-sig")
            self.assertNotIn("stop_agency_lrtj_6", stops)


class TestParseAndValidate(unittest.TestCase):
    def test_parse_counts(self):
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        self.assertEqual(set(parsed.keys()), {"OUTBOUND", "INBOUND"})
        self.assertEqual(parsed["OUTBOUND"]["total_trains"], 91)
        self.assertEqual(parsed["INBOUND"]["total_trains"], 93)
        codes_out = [c for c, _ in parsed["OUTBOUND"]["stations_order"]]
        self.assertEqual(codes_out[0], "KPG")
        self.assertEqual(codes_out[-1], "MRI")
        codes_in = [c for c, _ in parsed["INBOUND"]["stations_order"]]
        self.assertEqual(codes_in[0], "MRI")
        self.assertEqual(codes_in[-1], "KPG")
        self.assertEqual(resolve_station_code("PEMUDA"), "RWM")
        # trip lewat tengah malam: stasiun terakhir KA 1185 di 00:18
        t1185 = next(t for t in parsed["INBOUND"]["trains"] if t["no_ka"] == "1185")
        self.assertEqual(t1185["stops"][-1]["arrival"], time(0, 18))

    def test_validation_ok(self):
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        v = validate_stop_ids(parsed)
        self.assertEqual(v["missing_in_db"], [])
        self.assertEqual(len(v["not_in_excel"]), 0)
        self.assertEqual(v["excel_count"], 11)

    def test_summary(self):
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        s = get_summary(parsed)
        self.assertEqual(s["total_routes"], 2)
        self.assertEqual(s["total_trains"], 184)


class TestReader(unittest.TestCase):
    def test_roundtrip_zip(self):
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")

        buf, _used = gg.generate_gtfs_zip(parsed, None)
        data = buf.getvalue()
        stops = gr.read_gtfs_stops(data)
        self.assertEqual(len(stops), 11)

        rebuilt = gr.rebuild_gtfs_without_inactive(data, {"stop_agency_lrtj_6"}).getvalue()
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as z:
            st = z.read("stop_times.txt").decode("utf-8-sig")
            self.assertNotIn("stop_agency_lrtj_6", st)
            trips = z.read("trips.txt").decode("utf-8-sig")
            self.assertGreater(len(trips.splitlines()), 1)

    def test_apply_stop_edits(self):
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        buf, _used = gg.generate_gtfs_zip(parsed, None)
        edited = gr.apply_stop_edits(
            buf.getvalue(),
            edits={"stop_agency_lrtj_6": {"stop_id": "VLD", "stop_name": "VELODROME BARU"}},
            add_rows=[{"stop_id": "TEST", "stop_name": "Test Stop", "stop_lat": "-6.1", "stop_lon": "106.8"}],
        ).getvalue()
        with zipfile.ZipFile(io.BytesIO(edited)) as z:
            stops = z.read("stops.txt").decode("utf-8-sig")
            self.assertIn("VLD", stops)
            self.assertIn("TEST", stops)
            st = z.read("stop_times.txt").decode("utf-8-sig")
            self.assertIn("VLD", st)
            self.assertNotIn("stop_agency_lrtj_6", st)


class TestActiveFlag(unittest.TestCase):
    def test_values(self):
        self.assertEqual(parse_active_flag(None), 0)
        self.assertEqual(parse_active_flag(1), 1)
        self.assertEqual(parse_active_flag(0), 0)
        self.assertEqual(parse_active_flag(True), 1)
        self.assertEqual(parse_active_flag(False), 0)
        self.assertEqual(parse_active_flag("1"), 1)
        self.assertEqual(parse_active_flag("0"), 0)
        self.assertEqual(parse_active_flag(1.0), 1)

    def test_na_nan_tidak_crash(self):
        self.assertEqual(parse_active_flag(pd.NA), 0)
        self.assertEqual(parse_active_flag(float("nan")), 0)


def _mini_zip():
    """GTFS zip minimal: trip T1 (3 stop), T2 (3 stop), T3 (2 stop)."""
    files = {
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
                     "S1,A,0.0,0.0\nS2,B,0.0,0.0\nS3,C,0.0,0.0\nS4,D,0.0,0.0\nS5,E,0.0,0.0\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
                          "T1,06:00:00,06:00:00,S1,1\nT1,06:10:00,06:10:00,S2,2\nT1,06:20:00,06:20:00,S3,3\n"
                          "T2,07:00:00,07:00:00,S1,1\nT2,07:10:00,07:10:00,S4,2\nT2,07:20:00,07:20:00,S5,3\n"
                          "T3,08:00:00,08:00:00,S2,1\nT3,08:10:00,08:10:00,S3,2\n",
        "trips.txt": "route_id,service_id,trip_id\nR1,AllDay,T1\nR1,AllDay,T2\nR1,AllDay,T3\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_type\n"
                      "R1,A,1,Test,3\n",
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\nA,Test,http://x,Asia/Jakarta\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
                        "start_date,end_date\nAllDay,1,1,1,1,1,1,1,20260101,20261231\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, c in files.items():
            z.writestr(n, c)
    return buf


class TestRebuildTripDrop(unittest.TestCase):
    def test_trip_drop_below_2_stops(self):
        """Rule <2 selaras generator: trip yang sisa 1 stop ikut dihapus."""
        zip_bytes = _mini_zip().getvalue()
        rebuilt = gr.rebuild_gtfs_without_inactive(zip_bytes, {"S1", "S2"}).getvalue()
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as z:
            st = z.read("stop_times.txt").decode("utf-8-sig")
            trips = z.read("trips.txt").decode("utf-8-sig")
            self.assertIn("T2", st)
            self.assertIn("T2", trips)
            self.assertNotIn("T1", st)
            self.assertNotIn("T1", trips)
            self.assertNotIn("T3", st)
            self.assertNotIn("T3", trips)
            self.assertEqual(validate_gtfs_zip(rebuilt), [])

    def test_reactivation_roundtrip(self):
        """Rebuild tanpa nonaktif = stop_times tidak berubah."""
        orig = _mini_zip().getvalue()
        with zipfile.ZipFile(io.BytesIO(orig)) as z:
            orig_st = z.read("stop_times.txt")
        rebuilt = gr.rebuild_gtfs_without_inactive(orig, set()).getvalue()
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as z:
            self.assertEqual(z.read("stop_times.txt"), orig_st)


class TestGeneratorTripDrop(unittest.TestCase):
    def setUp(self):
        gg.reset_station_activity()

    def _parsed(self):
        return {
            "OUTBOUND": {
                "route_id": "KGM",
                "direction": "PGD-MRI",
                "trains": [
                    {
                        "no_ka": "1000",
                        "service_id": "AllDay",
                        "stops": [
                            {"station": "KPG", "arrival": time(6, 0), "departure": time(6, 0), "express": False},
                            {"station": "VEL", "arrival": time(6, 5), "departure": time(6, 6), "express": False},
                            {"station": "MRI", "arrival": time(6, 12), "departure": time(6, 13), "express": False},
                        ],
                    },
                ],
            },
        }

    def test_trip_kept_at_2_stops(self):
        trips, _st, _used = gg.generate_trips_and_stop_times(self._parsed(), None)
        self.assertIn("trip_route_agency_lrtj_0_1", "\n".join(trips))

    def test_trip_dropped_below_2_stops(self):
        gg.set_station_active("KPG", False)
        gg.set_station_active("VEL", False)
        trips, _st, _used = gg.generate_trips_and_stop_times(self._parsed(), None)
        self.assertNotIn("trip_route_agency_lrtj_0_1", "\n".join(trips))


class TestInactivePersistence(unittest.TestCase):
    def test_save_load_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "inactive_stops.json")
            self.assertTrue(gr.save_inactive_stops(p, {"VEL", "MRT"}))
            self.assertEqual(gr.load_inactive_stops(p), {"VEL", "MRT"})
            self.assertEqual(gr.load_inactive_stops(os.path.join(td, "nope.json")), set())
            self.assertEqual(gr.load_inactive_stops(""), set())
            with open(p, "w", encoding="utf-8") as f:
                f.write("{broken")
            self.assertEqual(gr.load_inactive_stops(p), set())

    def test_find_unreferenced_after_rebuild(self):
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        buf, _used = gg.generate_gtfs_zip(parsed, None)
        self.assertEqual(gr.find_unreferenced_stops(buf.getvalue()), set())
        rebuilt = gr.rebuild_gtfs_without_inactive(buf.getvalue(), {"stop_agency_lrtj_6"}).getvalue()
        self.assertEqual(gr.find_unreferenced_stops(rebuilt), {"stop_agency_lrtj_6"})

    def test_master_save_load(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "master.zip")
            data = b"\x50\x4b\x05\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            self.assertTrue(gr.save_gtfs_master(p, data))
            self.assertEqual(gr.load_gtfs_master(p), data)
            self.assertIsNone(gr.load_gtfs_master(os.path.join(td, "nope.zip")))
            self.assertFalse(gr.save_gtfs_master(p, None))

    def test_reactivation_restores_stop_times_from_master(self):
        """Reaktivasi harus mengembalikan stop_times persis dari master (urutan & seq)."""
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        master = gg.generate_gtfs_zip(parsed, None)[0].getvalue()
        trip_id = "trip_route_agency_lrtj_0_1"

        def seqs_of(zip_bytes):
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            rows = zf.read("stop_times.txt").decode("utf-8-sig").strip().split("\n")[1:]
            return [(r.split(",")[3], int(r.split(",")[5])) for r in rows if r.startswith(trip_id + ",")]

        orig_seq = seqs_of(master)
        stripped = gr.rebuild_gtfs_without_inactive(master, {"stop_agency_lrtj_6"}).getvalue()
        self.assertNotIn("stop_agency_lrtj_6", {s for s, _ in seqs_of(stripped)})
        restored = gr.rebuild_gtfs_without_inactive(master, set()).getvalue()
        self.assertEqual(seqs_of(restored), orig_seq)


class TestMasterFollowsUpload(unittest.TestCase):
    """Kebijakan master baru: zip yang di-upload ke sidebar LANGSUNG menjadi
    master (sumber aktivasi & nonaktivasi). Master lama otomatis di-backup ke
    `gtfs_master_prev.zip` dan dipakai untuk backfill stop_times saat
    reaktivasi stop yang barisnya sudah terbuang dari file upload (file hasil
    nonaktif)."""

    EXPECTED = {v["stop_id"] for v in gg._DEPLOY_STOP_MAP.values() if v.get("stop_id")}

    def _full_zip(self):
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        return gg.generate_gtfs_zip(parsed, None)[0].getvalue()

    def _strip_stop_totally(self, zip_bytes, target):
        """Hapus stop dari stops.txt sekaligus stop_times.txt (file terpotong)."""
        out = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as oz:
                for name in z.namelist():
                    raw = z.read(name)
                    if name in ("stops.txt", "stop_times.txt"):
                        lines = raw.decode("utf-8-sig").splitlines()
                        if not lines:
                            oz.writestr(name, raw)
                            continue
                        header = lines[0].split(",")
                        idx = header.index("stop_id") if "stop_id" in header else None
                        kept = [lines[0]]
                        for ln in lines[1:]:
                            if idx is None:
                                kept.append(ln)
                                continue
                            cells = ln.split(",")
                            if len(cells) > idx and cells[idx].strip() == target:
                                continue
                            kept.append(ln)
                        oz.writestr(name, ("\n".join(kept) + "\n").encode("utf-8"))
                    else:
                        oz.writestr(name, raw)
        out.seek(0)
        return out.getvalue()

    def test_count_gtfs_rows(self):
        data = self._full_zip()
        self.assertGreater(gr.count_gtfs_rows(data, "stop_times.txt"), 0)
        self.assertEqual(gr.count_gtfs_rows(data, "nope.txt"), 0)
        self.assertEqual(gr.count_gtfs_rows(None, "stop_times.txt"), 0)

    def test_full_zip_is_complete_master_candidate(self):
        data = self._full_zip()
        info = gr.classify_upload(data, self.EXPECTED)
        self.assertTrue(info["deploy_format"])
        self.assertEqual(info["missing_stops"], [])
        self.assertEqual(info["orphan_ids"], set())
        # stop_times tidak lebih kecil dari "master" (dirinya sendiri).
        self.assertLess(
            gr.count_gtfs_rows(self._strip_stop_totally(data, "stop_agency_lrtj_6"), "stop_times.txt"),
            gr.count_gtfs_rows(data, "stop_times.txt"),
        )

    def test_partial_zip_missing_stop_totally_detected(self):
        """Zip hasil FULL-mode (stop dipotong dari stops.txt & stop_times) harus
        terdeteksi `missing_stops` (celah deteksi orphan biasa ditutup)."""
        data = self._full_zip()
        partial = self._strip_stop_totally(data, "stop_agency_lrtj_6")
        info = gr.classify_upload(partial, self.EXPECTED)
        # Deteksi orphan biasa GAGAL (stop hilang dari kedua file) ...
        self.assertEqual(info["orphan_ids"], set())
        # ... tapi classify_upload menangkap bahwa file TIDAK lengkap.
        self.assertEqual(info["missing_stops"], ["stop_agency_lrtj_6"])

    def test_upload_always_becomes_master_backfill_restores(self):
        """Alur user: master full â†’ nonaktif _6 â†’ hasilnya di-upload lagi jadi
        master baru (prev=full) â†’ stop _6 barisnya terbuang, tapi reaktivasi
        via backfill dari prev mengembalikan stop_times persis."""
        full = self._full_zip()
        deact = gr.rebuild_gtfs_without_inactive(full, {"stop_agency_lrtj_6"}).getvalue()
        # kebijakan baru: master := file upload terakhir (hasil nonaktif).
        master, prev = deact, full
        self.assertNotIn("stop_agency_lrtj_6", gr.read_gtfs_trip_stop_ids(master))

        restored = gr.backfill_stop_times_from_master(master, prev, {"stop_agency_lrtj_6"}).getvalue()
        self.assertEqual(gr.count_gtfs_rows(restored, "stop_times.txt"),
                         gr.count_gtfs_rows(full, "stop_times.txt"))
        self.assertIn("stop_agency_lrtj_6", gr.read_gtfs_trip_stop_ids(restored))
        self.assertEqual(validate_gtfs_zip(restored), [])

    def test_backfill_idempotent_no_duplicate(self):
        full = self._full_zip()
        deact = gr.rebuild_gtfs_without_inactive(full, {"stop_agency_lrtj_6"}).getvalue()
        once = gr.backfill_stop_times_from_master(deact, full, {"stop_agency_lrtj_6"}).getvalue()
        twice = gr.backfill_stop_times_from_master(once, full, {"stop_agency_lrtj_6"}).getvalue()
        self.assertEqual(gr.count_gtfs_rows(twice, "stop_times.txt"),
                         gr.count_gtfs_rows(once, "stop_times.txt"))
        with zipfile.ZipFile(io.BytesIO(twice)) as z:
            rows = z.read("stop_times.txt").decode("utf-8-sig").strip().split("\n")[1:]
        pairs = [tuple(r.split(",")[0:1] + [r.split(",")[3]]) for r in rows]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_backfill_keeps_other_files_untouched(self):
        full = self._full_zip()
        deact = gr.rebuild_gtfs_without_inactive(full, {"stop_agency_lrtj_6"}).getvalue()
        restored = gr.backfill_stop_times_from_master(deact, full, {"stop_agency_lrtj_6"}).getvalue()
        with zipfile.ZipFile(io.BytesIO(deact)) as a, zipfile.ZipFile(io.BytesIO(restored)) as b:
            for name in ("trips.txt", "stops.txt", "routes.txt", "calendar.txt", "agency.txt"):
                self.assertEqual(a.read(name), b.read(name), f"{name} harus tetap sama")

    def test_backfill_preserves_upload_trip_order(self):
        """Backfill harus mempertahankan urutan trip file upload (mis. `_0_2`
        setelah `_0_1`), bukan lexicographic (`_0_10` sebelum `_0_2`)."""
        full = self._full_zip()
        deact = gr.rebuild_gtfs_without_inactive(full, {"stop_agency_lrtj_6"}).getvalue()
        restored = gr.backfill_stop_times_from_master(deact, full, {"stop_agency_lrtj_6"}).getvalue()

        def trip_order(data):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                rows = z.read("stop_times.txt").decode("utf-8-sig").replace("\r\n", "\n") \
                    .strip().split("\n")[1:]
            seen = []
            for r in rows:
                t = r.split(",")[0]
                if not seen or seen[-1] != t:
                    seen.append(t)
            return seen

        self.assertEqual(trip_order(restored), trip_order(full))
        with zipfile.ZipFile(io.BytesIO(restored)) as a, zipfile.ZipFile(io.BytesIO(full)) as b:
            norm = lambda d: d.replace(b"\r\n", b"\n")  # noqa: E731
            self.assertEqual(norm(a.read("stop_times.txt")), norm(b.read("stop_times.txt")))
        self.assertEqual(validate_gtfs_zip(restored), [])

    def test_reactivation_restores_stop_times_from_master(self):
        """Reaktivasi harus mengembalikan stop_times persis dari master (urutan & seq)."""
        parsed = _parse()
        if parsed is None:
            self.skipTest("File Excel tidak ditemukan")
        master = gg.generate_gtfs_zip(parsed, None)[0].getvalue()
        trip_id = "trip_route_agency_lrtj_0_1"

        def seqs_of(zip_bytes):
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            rows = zf.read("stop_times.txt").decode("utf-8-sig").strip().split("\n")[1:]
            return [(r.split(",")[3], int(r.split(",")[5])) for r in rows if r.startswith(trip_id + ",")]

        orig_seq = seqs_of(master)
        stripped = gr.rebuild_gtfs_without_inactive(master, {"stop_agency_lrtj_6"}).getvalue()
        self.assertNotIn("stop_agency_lrtj_6", {s for s, _ in seqs_of(stripped)})
        restored = gr.rebuild_gtfs_without_inactive(master, set()).getvalue()
        self.assertEqual(seqs_of(restored), orig_seq)


class TestZipMergeExtras(unittest.TestCase):
    """Trip/service non-reguler dari zip template (tahun baru) harus ikut ter-merge."""

    def setUp(self):
        gg.reset_station_activity()

    def _gen(self, inactive=None):
        parsed = _parse()
        if parsed is None or not os.path.exists(MASS_ZIP):
            self.skipTest("Excel / Referensi zip tidak ditemukan")
        with open(MASS_ZIP, "rb") as f:
            zbytes = f.read()
        zip_stops = {s["stop_id"]: {**s, "is_active": 1} for s in gr.read_gtfs_stops(zbytes)}
        for sid in (inactive or set()):
            if sid in zip_stops:
                zip_stops[sid]["is_active"] = 0
        return gg.generate_gtfs_from_zip(parsed, zbytes, zip_stops, None)

    def test_tahun_baru_dipertahankan(self):
        buf, _used, _map, _warn = self._gen()
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as z:
            trips = z.read("trips.txt").decode("utf-8-sig")
            cal = z.read("calendar.txt").decode("utf-8-sig")
            self.assertIn("trip_route_agency_lrtj_tbm_", trips)
            self.assertIn("trip_route_agency_lrtj_tbp_", trips)
            self.assertIn("service_agency_lrtj_tahun_baru_malam", cal)
            self.assertIn("service_agency_lrtj_tahun_baru_pagi", cal)
            self.assertIn("service_agency_lrtj_0", cal)
            self.assertIn("service_agency_lrtj_1", cal)
        self.assertEqual(validate_gtfs_zip(buf.getvalue()), [])

    def test_tahun_baru_ikut_filter_stop_nonaktif(self):
        buf, _used, _map, _warn = self._gen(inactive={"stop_agency_lrtj_6"})
        with zipfile.ZipFile(io.BytesIO(buf.getvalue())) as z:
            st = z.read("stop_times.txt").decode("utf-8-sig")
        self.assertNotIn("stop_agency_lrtj_6", st)

    def test_stop_nonaktif_tetap_ada_di_stops_txt(self):
        """Stasiun nonaktif harus tetap ada di stops.txt supaya bisa diaktifkan
        kembali (reaktivasi) saat zip di-upload lagi; hanya stop_times yang difilter."""
        buf, _used, _map, _warn = self._gen(inactive={"stop_agency_lrtj_6"})
        data = buf.getvalue()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            stops = z.read("stops.txt").decode("utf-8-sig")
            st = z.read("stop_times.txt").decode("utf-8-sig")
        self.assertIn("stop_agency_lrtj_6", stops)
        self.assertNotIn("stop_agency_lrtj_6", st)
        self.assertEqual(gr.find_unreferenced_stops(data), {"stop_agency_lrtj_6"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
