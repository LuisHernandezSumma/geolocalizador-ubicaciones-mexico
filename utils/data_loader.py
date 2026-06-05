import json, gzip, pickle
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

def load_cp_lookup() -> dict:
    """Load CP -> {lat, lng, zonas} lookup table."""
    path = DATA_DIR / "cp_lookup.json.gz"
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return json.load(f)

def load_kml_zones() -> dict:
    """Load KML polygon data for point-in-polygon fallback."""
    path = DATA_DIR / "kml_zones.pkl.gz"
    with gzip.open(path, 'rb') as f:
        return pickle.load(f)
