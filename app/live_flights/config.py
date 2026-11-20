"""Configuration constants and dictionaries for Real-Time Flight Data Service."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "live_flights.db"

# Airports and Reference Projections
HKG_ORIGIN = (22.3080, 113.9185)  # (lat, lon)
HKG_RUNWAY_HEADING_DEG = 74.0     # Runway 07L/07R aligned to -Z

WUH_ORIGIN = (30.7838, 114.2081)  # (lat, lon)
WUH_RUNWAY_HEADING_DEG = 38.0     # Runway 04L/04R aligned to -Z

METERS_PER_UNIT = 2.0
RUNWAY_WEST_X = -365.0
RUNWAY_EAST_X = 375.0
RUNWAY_Z_MIN = -540.0
RUNWAY_Z_MAX = 500.0

DEFAULT_RADIUS_NM = 35
ADSB_TIMEOUT = 8.0
ADSB_POLL_INTERVAL = 3.0
SCHEDULE_POLL_INTERVAL = 180.0

ADSB_ENDPOINTS = [
    "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius}",
    "https://api.airplanes.live/v2/point/{lat}/{lon}/{radius}",
    "https://api.adsb.one/v2/point/{lat}/{lon}/{radius}",
]

HKG_SCHEDULE_URL = (
    "https://www.hongkongairport.com/flightinfo-rest/rest/flights/past"
    "?date={date}&lang=zh_CN&cargo={cargo}&arrival={arrival}"
)

# 120+ Global and Domestic ICAO 3-letter to IATA 2-letter airline mappings
ICAO_TO_IATA = {
    # HK / Greater Bay / Regional
    "CPA": "CX", "HKE": "UO", "CRK": "HX", "HGB": "HB", "AHK": "LD",
    "AMU": "NX", "SJX": "JX", "CAL": "CI", "EVA": "BR", "TTW": "IT",
    # China Mainland
    "CSN": "CZ", "CES": "MU", "CCA": "CA", "CHH": "HU", "CSZ": "ZH",
    "CXA": "MF", "DKH": "HO", "CQH": "9C", "CSC": "3U", "CDG": "SC",
    "CBJ": "JD", "GCR": "GS", "OKA": "BK", "CNH": "G5", "SHU": "8L",
    "EPA": "DZ", "SFY": "O3", "CSS": "YG", "CYZ": "Y8", "YZR": "YZ",
    "LKE": "GJ", "KNA": "KN", "HXA": "HX", "CJX": "CF", "CCD": "CD",
    # Asia Pacific
    "SIA": "SQ", "TGW": "TR", "MAS": "MH", "AXM": "AK", "MXD": "OD",
    "THA": "TG", "AIQ": "FD", "BKP": "PG", "HVN": "VN", "VJC": "VJ",
    "GIA": "GA", "CTV": "QG", "LNI": "JT", "JAL": "JL", "ANA": "NH",
    "APJ": "MM", "KAL": "KE", "AAR": "OZ", "JNA": "LJ", "TWB": "TW",
    "ESR": "ZE", "ABL": "BX", "CEB": "5J", "PAL": "PR", "APG": "5J",
    "AIC": "AI", "IGO": "6E", "SEJ": "SG", "VTI": "UK",
    # Middle East & Africa
    "UAE": "EK", "QTR": "QR", "ETD": "EY", "SVA": "SV", "FDB": "FZ",
    "GFA": "GF", "OMA": "WY", "ETH": "ET", "MSR": "MS", "RAM": "AT",
    # Europe
    "BAW": "BA", "DLH": "LH", "AFR": "AF", "KLM": "KL", "FIN": "AY",
    "SAS": "SK", "SWR": "LX", "AUA": "OS", "BEL": "SN", "IBE": "IB",
    "TAP": "TP", "THY": "TK", "AFL": "SU", "VIR": "VS", "EZY": "U2",
    # Americas & Oceania
    "QFA": "QF", "ANZ": "NZ", "UAL": "UA", "AAL": "AA", "DAL": "DL",
    "ACA": "AC", "WJA": "WS", "LAN": "LA", "TAM": "JJ", "AMX": "AM",
    # Cargo
    "UPS": "5X", "FDX": "FX", "GTI": "5Y", "CLX": "CV", "BOX": "3S",
    "PAC": "PO", "KZR": "KC", "ABW": "RU", "BCS": "QY", "SOO": "7L",
}

# Airline Tail Livery Colors (Three.js Hex)
AIRLINE_COLORS = {
    "CX": 0x006564,  # Cathay Jade Green
    "UO": 0x7c3aed,  # HK Express Purple
    "HX": 0xdc2626,  # HK Airlines Red
    "HB": 0x2563eb,  # Greater Bay Blue
    "CZ": 0x0284c7,  # China Southern Sky Blue
    "MU": 0x2563eb,  # China Eastern Blue
    "CA": 0xdc2626,  # Air China Phoenix Red
    "HU": 0xef4444,  # Hainan Airlines Red
    "ZH": 0xdc2626,  # Shenzhen Airlines Red
    "MF": 0x0284c7,  # Xiamen Air Blue
    "3U": 0xdc2626,  # Sichuan Airlines Red
    "9C": 0x16a34a,  # Spring Airlines Green
    "HO": 0x831843,  # Juneyao Crimson
    "SQ": 0x1e3a8a,  # Singapore Navy
    "TG": 0x6b21a8,  # Thai Purple
    "JL": 0xdc2626,  # JAL Red
    "NH": 0x0284c7,  # ANA Triton Blue
    "KE": 0x38bdf8,  # Korean Sky Blue
    "OZ": 0x475569,  # Asiana Slate
    "EK": 0xdc2626,  # Emirates Red
    "QR": 0x831843,  # Qatar Burgundy
    "LH": 0x1e3a8a,  # Lufthansa Deep Blue
    "BA": 0x1e40af,  # British Airways Blue
    "AF": 0x1e3a8a,  # Air France Blue
    "KL": 0x0284c7,  # KLM Cyan
    "AY": 0x1e3a8a,  # Finnair Navy
    "QF": 0xdc2626,  # Qantas Red
    "UA": 0x0284c7,  # United Blue
    "AA": 0x94a3b8,  # American Silver
    "DL": 0x1e3a8a,  # Delta Navy
    "FX": 0x475569,  # FedEx Grey
    "5X": 0x78350f,  # UPS Brown
}

WIDEBODY_TYPES = {
    "B77W", "B772", "B773", "B77L", "B77F", "B788", "B789", "B78X",
    "A359", "A35K", "A332", "A333", "A338", "A339", "A343", "A346",
    "B744", "B748", "A388", "MD11", "IL76", "IL96"
}

BIZJET_TYPES = {
    "GLF5", "GLF6", "GLEX", "CL60", "CL30", "CL35", "F900", "FA7X",
    "FA8X", "C56X", "C680", "C750", "E55P", "LJ60", "HDJT"
}
