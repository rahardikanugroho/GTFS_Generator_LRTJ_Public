# GTFS Generator LRT Jakarta (Dashboard Streamlit)

Generator GTFS untuk **LRT Jakarta** dalam bentuk dashboard web (Streamlit).
Membaca jadwal perjalanan dari file Excel (timetable), membangun file GTFS
(.zip), serta mengelola stops — termasuk menonaktifkan/aktifkan stasiun tanpa
kehilangan data.

## Fitur

- Parse jadwal dari Excel/CSV (format 2 arah: genap & ganjil per rute).
- Generate GTFS .zip lengkap (agency, calendar, routes, stops, trips,
  stop_times, extras, timetables) mengikuti format GTFS standar.
- Dashboard sidebar: upload GTFS .zip existing, nonaktifkan/aktifkan stops,
  generate ulang, dan download hasil.
- State nonaktif stops disimpan permanen (tidak hilang saat server restart).
- Validasi stop_id & integritas GTFS.
- Backfill stop_times dari master GTFS untuk reaktivasi stasiun.

## Menjalankan

```bash
pip install -r requirements.txt
streamlit run app.py
```

Dashboard terbuka di browser lokal (port default Streamlit). Port dapat diubah
lewat argumen `--server.port`.

Dashboard: manage stops via GTFS .zip existing + tambah stop ke database.
Tab utama: Overview (edit stasiun), Rute & Trip, Jadwal, Download GTFS.

## Struktur

| File | Fungsi |
|------|--------|
| `app.py` | Dashboard Streamlit utama |
| `lrt_parser.py` | Parse timetable Excel/CSV |
| `gtfs_generator.py` | Generate & tulis GTFS .zip |
| `gtfs_reader.py` | Baca/rebuild GTFS .zip dari upload |
| `lrt_route.py` | Konfigurasi rute & agen |
| `lrt_station_data.py` | Data stasiun LRT |
| `validation.py` | Validasi stop_id & GTFS |
| `test_gtfs.py` | Unit test |

## Test

```bash
python -m pytest test_gtfs.py -q
```

> Catatan: file data operasional (timetable Excel, GTFS master .zip) **tidak
> disertakan** dalam repositori publik ini. Siapkan file di folder yang sama
> dengan dashboard, atau gunakan fitur upload di sidebar.

## Lisensi

Proyek pribadi untuk keperluan internal. Tidak ada jaminan apapun.
