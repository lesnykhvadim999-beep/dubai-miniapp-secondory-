"""Convert DLD official area names back to user-friendly names.
DLD canonical имена не понятны пользователям — нужно показывать привычные.
"""
import sys, io, psycopg2
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

DB = "REDACTED_DSN_USE_DATABASE_URL_ENV"

# DLD official name → User-friendly name
DLD_TO_FRIENDLY = {
    "Al Barsha South Fourth":            "Jumeirah Village Circle",
    "Al Barsha South Third":             "Jumeirah Village Triangle",
    "Al Barsha South Second":            "Jumeirah Village Triangle",
    "Marsa Dubai":                       "Dubai Marina",
    "Hadaeq Sheikh Mohammed Bin Rashid": "MBR City",
    "Al Khairan First":                  "Dubai Creek Harbour",
    "Al Khairan Second":                 "Dubai Creek Harbour",
    "Wadi Al Safa 2":                    "Dubai Hills Estate",
    "Wadi Al Safa 3":                    "Dubai Hills Estate",
    "Wadi Al Safa 5":                    "Dubai Hills Estate",
    "Wadi Al Safa 7":                    "Dubai Hills Estate",
    "Burj Khalifa":                      "Downtown Dubai",  # тоже DLD official для Downtown
    "Jabal Ali First":                   "Jebel Ali",
    "Saih Shuaib 2":                     "Dubai Investments Park",
    "Madinat Al Mataar":                 "Dubai South",
    "Marsa Al Arab":                     "Sufouh",
    "Trade Center Second":               "DIFC",
    "Trade Center First":                "DIFC",
    "Mirdif":                            "Mirdiff Hills",
    "Al Yufrah 1":                       "Town Square",
    "Al Yufrah 2":                       "Town Square",
    "Al Thanyah Fifth":                  "Jumeirah Lake Towers",
    "Al Thanyah Fourth":                 "Jumeirah Lake Towers",
    "Al Thanyah Third":                  "Jumeirah Lake Towers",
}

conn = psycopg2.connect(DB); cur = conn.cursor()
total_updated = 0
for dld_name, friendly in DLD_TO_FRIENDLY.items():
    cur.execute("UPDATE listings SET area=%s WHERE area=%s", (friendly, dld_name))
    n = cur.rowcount
    if n:
        print(f'{dld_name!r:45} → {friendly!r:30} [{n}]', flush=True)
        total_updated += n
print(f'\nTotal area names updated: {total_updated}', flush=True)
conn.commit(); conn.close()
