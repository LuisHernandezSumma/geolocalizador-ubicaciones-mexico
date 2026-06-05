import time
import unicodedata
import requests


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    s = str(s or '').strip().lower()
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


def point_in_polygon(lng: float, lat: float, polygon: list) -> bool:
    """Ray casting algorithm."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > lat) != (yj > lat)) and (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def get_zones_from_kml(lng: float, lat: float, kml_zones: dict) -> dict:
    """Find zona_sismica, zona_cresta, hidro2 from KML polygons."""
    result = {'zona_sismica': '', 'zona_cresta': '', 'hidro2': ''}
    for poly in kml_zones.get('sismicas', []):
        if point_in_polygon(lng, lat, poly['polygon']):
            result['zona_sismica'] = poly['name']
            break
    for poly in kml_zones.get('cresta', []):
        if point_in_polygon(lng, lat, poly['polygon']):
            result['zona_cresta'] = poly['name']
            break
    for poly in kml_zones.get('huracanes', []):
        if point_in_polygon(lng, lat, poly['polygon']):
            result['hidro2'] = poly['name']
            break
    return result


def parse_nominatim(data: dict) -> dict:
    """Parse Nominatim response into standardized dict."""
    a = data.get('address', {})
    return {
        'lat': float(data['lat']),
        'lng': float(data['lon']),
        'estado_geo': a.get('state', ''),
        'municipio_geo': a.get('city') or a.get('county') or a.get('municipality') or a.get('town') or a.get('village', ''),
        'cp_geo': a.get('postcode', ''),
    }


def nominatim_search(query: str, delay: float = 1.1) -> dict | None:
    """Forward geocode using Nominatim."""
    try:
        r = requests.get(
            'https://nominatim.openstreetmap.org/search',
            params={'q': query, 'format': 'json', 'limit': 1,
                    'countrycodes': 'mx', 'addressdetails': 1},
            headers={'User-Agent': 'geocodificador-mx/1.0'},
            timeout=10
        )
        data = r.json()
        if data:
            time.sleep(delay)
            return parse_nominatim(data[0])
    except Exception:
        pass
    time.sleep(delay)
    return None


def nominatim_reverse(lat: float, lng: float, delay: float = 1.1) -> dict | None:
    """Reverse geocode using Nominatim."""
    try:
        r = requests.get(
            'https://nominatim.openstreetmap.org/reverse',
            params={'lat': lat, 'lon': lng, 'format': 'json',
                    'addressdetails': 1, 'accept-language': 'es'},
            headers={'User-Agent': 'geocodificador-mx/1.0'},
            timeout=10
        )
        data = r.json()
        if 'address' in data:
            time.sleep(delay)
            return parse_nominatim(data)
    except Exception:
        pass
    time.sleep(delay)
    return None


def validate_fields(row_data: dict, geo: dict) -> list:
    """Cross-validate estado/municipio/cp between row and geocoded data."""
    issues = []
    est_r = normalize(row_data.get('est', ''))
    mun_r = normalize(row_data.get('mun', ''))
    cp_r = str(row_data.get('cp', '')).strip()

    if est_r and geo.get('estado_geo'):
        est_g = normalize(geo['estado_geo'])
        if not (est_g in est_r or est_r in est_g):
            issues.append(f"Estado \"{row_data['est']}\" ≠ geo \"{geo['estado_geo']}\"")
    if mun_r and geo.get('municipio_geo'):
        mun_g = normalize(geo['municipio_geo'])
        if not (mun_g in mun_r or mun_r in mun_g):
            issues.append(f"Municipio \"{row_data['mun']}\" ≠ geo \"{geo['municipio_geo']}\"")
    if cp_r and geo.get('cp_geo') and cp_r != geo['cp_geo']:
        issues.append(f"CP \"{cp_r}\" ≠ geo \"{geo['cp_geo']}\"")
    return issues


# ── Main geocoding function ───────────────────────────────────────────────────

def geocode_row(row, mapping: dict, cp_lookup: dict, kml_zones: dict, delay: float = 1.1) -> dict:
    """
    Geocode a single row using the 6-step fallback strategy:
    1. Existing coords → reverse geocode + validate
    2. CP + Estado → CP lookup table
    3. Solo CP → CP lookup table
    4. Nombre + Ciudad + Estado → Nominatim
    5. Ciudad + Estado → Nominatim
    6. Coords from CP → KML point-in-polygon
    """

    def gv(fid):
        col = mapping.get(fid)
        if col and col in row.index:
            val = row[col]
            if val is not None and str(val).strip() not in ('', 'nan', 'None'):
                return str(val).strip()
        return ''

    lat_s, lng_s = gv('lat'), gv('lng')
    cp_s = gv('cp').zfill(5) if gv('cp') else ''
    est_s = gv('est')
    mun_s = gv('mun')
    nom_s = gv('nom')
    dir_s = gv('dir')

    row_data = {'lat': lat_s, 'lng': lng_s, 'cp': cp_s,
                'est': est_s, 'mun': mun_s, 'nom': nom_s}

    base = {'lat_geo': '', 'lng_geo': '', 'estado_geo': '', 'municipio_geo': '',
            'cp_geo': '', 'zona_sismica': '', 'zona_cresta': '', 'hidro2': '',
            'metodo': '', 'observacion': ''}

    def enrich_from_cp(cp_key: str) -> dict:
        """Get all zone data from CP lookup."""
        entry = cp_lookup.get(cp_key.zfill(5), {})
        return {
            'lat_geo': str(entry.get('lat', '')) if entry.get('lat') else '',
            'lng_geo': str(entry.get('lng', '')) if entry.get('lng') else '',
            'estado_geo': entry.get('estado', ''),
            'municipio_geo': entry.get('municipio', ''),
            'cp_geo': cp_key,
            'zona_sismica': entry.get('zona_sismica', ''),
            'zona_cresta': entry.get('zona_cresta', ''),
            'hidro2': entry.get('hidro2', ''),
        }

    def enrich_zones_from_coords(lat: float, lng: float) -> dict:
        """Get zone data from KML polygons given coords."""
        zones = get_zones_from_kml(lng, lat, kml_zones)
        return zones

    # ── Step 1: Existing coords ───────────────────────────────────────────────
    if lat_s and lng_s:
        try:
            lat_f, lng_f = float(lat_s), float(lng_s)
            geo = nominatim_reverse(lat_f, lng_f, delay)
            if geo:
                result = {**base,
                          'lat_geo': lat_s, 'lng_geo': lng_s,
                          'estado_geo': geo['estado_geo'],
                          'municipio_geo': geo['municipio_geo'],
                          'cp_geo': geo['cp_geo'],
                          'metodo': '1-coords-inverso'}
                # Enrich zones from CP if available
                if geo['cp_geo']:
                    z = enrich_from_cp(geo['cp_geo'])
                    result['zona_sismica'] = z['zona_sismica']
                    result['zona_cresta'] = z['zona_cresta']
                    result['hidro2'] = z['hidro2']
                # Fallback zones from KML
                if not result['zona_sismica']:
                    kz = enrich_zones_from_coords(lat_f, lng_f)
                    result.update(kz)
                issues = validate_fields(row_data, result)
                result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK'
                return result
        except (ValueError, TypeError):
            pass

    # ── Step 2: CP + Estado from lookup ──────────────────────────────────────
    if cp_s:
        entry = cp_lookup.get(cp_s)
        if entry:
            result = {**base, **enrich_from_cp(cp_s), 'metodo': '2-CP+lookup'}
            # Validate estado
            issues = []
            if est_s and entry.get('estado'):
                if normalize(est_s) not in normalize(entry['estado']) and \
                   normalize(entry['estado']) not in normalize(est_s):
                    issues.append(f"Estado \"{est_s}\" ≠ lookup \"{entry['estado']}\"")
            result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK (CP lookup)'
            # If no zones in CP table, try KML
            if not result['zona_sismica'] and result['lat_geo']:
                try:
                    kz = enrich_zones_from_coords(float(result['lat_geo']), float(result['lng_geo']))
                    result.update(kz)
                except Exception:
                    pass
            return result

    # ── Step 3: Solo CP → Nominatim ──────────────────────────────────────────
    if cp_s:
        geo = nominatim_search(f"{cp_s}, Mexico", delay)
        if geo:
            result = {**base,
                      'lat_geo': str(geo['lat']), 'lng_geo': str(geo['lng']),
                      'estado_geo': geo['estado_geo'],
                      'municipio_geo': geo['municipio_geo'],
                      'cp_geo': geo['cp_geo'] or cp_s,
                      'metodo': '3-solo-CP'}
            z = enrich_from_cp(geo['cp_geo'] or cp_s)
            result['zona_sismica'] = z['zona_sismica']
            result['zona_cresta'] = z['zona_cresta']
            result['hidro2'] = z['hidro2']
            if not result['zona_sismica']:
                kz = enrich_zones_from_coords(geo['lat'], geo['lng'])
                result.update(kz)
            issues = validate_fields(row_data, result)
            result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK (CP Nominatim)'
            return result

    # ── Step 4: Nombre + Ciudad + Estado → Nominatim ─────────────────────────
    parts4 = [p for p in [nom_s, mun_s, est_s] if p]
    if len(parts4) >= 2:
        query = ', '.join(parts4 + ['Mexico'])
        geo = nominatim_search(query, delay)
        if geo:
            result = {**base,
                      'lat_geo': str(geo['lat']), 'lng_geo': str(geo['lng']),
                      'estado_geo': geo['estado_geo'],
                      'municipio_geo': geo['municipio_geo'],
                      'cp_geo': geo['cp_geo'],
                      'metodo': '4-nombre+ciudad'}
            if geo['cp_geo']:
                z = enrich_from_cp(geo['cp_geo'])
                result['zona_sismica'] = z['zona_sismica']
                result['zona_cresta'] = z['zona_cresta']
                result['hidro2'] = z['hidro2']
            if not result['zona_sismica']:
                kz = enrich_zones_from_coords(geo['lat'], geo['lng'])
                result.update(kz)
            issues = validate_fields(row_data, result)
            result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK (nombre+ciudad)'
            return result

    # ── Step 5: Ciudad + Estado → Nominatim ──────────────────────────────────
    parts5 = [p for p in [mun_s, est_s] if p]
    if parts5:
        query = ', '.join(parts5 + ['Mexico'])
        geo = nominatim_search(query, delay)
        if geo:
            result = {**base,
                      'lat_geo': str(geo['lat']), 'lng_geo': str(geo['lng']),
                      'estado_geo': geo['estado_geo'],
                      'municipio_geo': geo['municipio_geo'],
                      'cp_geo': geo['cp_geo'],
                      'metodo': '5-ciudad+estado'}
            if geo['cp_geo']:
                z = enrich_from_cp(geo['cp_geo'])
                result['zona_sismica'] = z['zona_sismica']
                result['zona_cresta'] = z['zona_cresta']
                result['hidro2'] = z['hidro2']
            if not result['zona_sismica']:
                kz = enrich_zones_from_coords(geo['lat'], geo['lng'])
                result.update(kz)
            issues = validate_fields(row_data, result)
            result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK (ciudad+estado)'
            return result

    # ── Step 6: Sin coordenadas ───────────────────────────────────────────────
    result = {**base, 'metodo': '—', 'observacion': 'Sin datos suficientes para geocodificar'}
    return result


def enrich_zones(lat: float, lng: float, cp: str, cp_lookup: dict, kml_zones: dict) -> dict:
    """Get zone data from CP lookup first, then KML fallback."""
    cp_key = str(cp).zfill(5)
    entry = cp_lookup.get(cp_key, {})
    result = {
        'zona_sismica': entry.get('zona_sismica', ''),
        'zona_cresta': entry.get('zona_cresta', ''),
        'hidro2': entry.get('hidro2', ''),
    }
    if not result['zona_sismica']:
        kz = get_zones_from_kml(lng, lat, kml_zones)
        result.update(kz)
    return result
