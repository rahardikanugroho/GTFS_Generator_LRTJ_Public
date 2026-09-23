"""Konfigurasi rute & agen LRT Jakarta koridor Kelapa Gading (Pegangsaan Dua) - Manggarai."""

AGENCY = {
    "agency_id": "LRTJ",
    "agency_name": "PT. LRT Jakarta",
    "agency_url": "https://lrtjakarta.co.id",
    "agency_timezone": "Asia/Jakarta",
    "agency_lang": "id",
}

ROUTES = [
    {
        "route_id": "KGM",
        "route_short_name": "LRT JAKARTA",
        "route_long_name": "Kelapa Gading - Manggarai",
        "route_desc": "LRT Jakarta Lin Kelapa Gading (PGD-MRI)",
        "route_type": "0",
    },
    {
        "route_id": "MGK",
        "route_short_name": "LRT JAKARTA",
        "route_long_name": "Manggarai - Kelapa Gading",
        "route_desc": "LRT Jakarta Lin Kelapa Gading (MRI-PGD)",
        "route_type": "0",
    },
]

BLOCK_DIRECTORY = {
    "OUTBOUND": {
        "route_id": "KGM",
        "direction": "PGD-MRI",
    },
    "INBOUND": {
        "route_id": "MGK",
        "direction": "MRI-PGD",
    },
}