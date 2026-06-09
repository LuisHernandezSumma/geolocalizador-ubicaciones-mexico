import pandas as pd
import time
import re
import unicodedata
import requests


# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    s = str(s or '').strip().lower()
    return ''.join(c for c in unicodedata.normalize('NFD', s)
                   if unicodedata.category(c) != 'Mn')


def clean_cp(raw) -> str:
    """Normaliza un CP a string de 5 digitos.
    pandas suele leer CPs como float (76246.0) -> se limpia el .0."""
    if raw is None:
        return ''
    s = str(raw).strip()
    if not s or s.lower() in ('nan', 'none'):
        return ''
    try:
        return str(int(float(s))).zfill(5)
    except (ValueError, TypeError):
        # quita cualquier caracter no numerico y rellena
        digits = re.sub(r'\D', '', s)
        return digits.zfill(5) if digits else ''


def extract_cp_from_text(text: str) -> str:
    """Extrae un CP embebido dentro de una direccion, de forma CONSERVADORA.
    Solo lo toma cuando hay marca explicita 'C.P.'/'CP', o cuando un numero
    de 5 digitos esta al final del texto (patron tipico de domicilio).
    Asi se evita confundir numeros de lote/manzana con un CP real."""
    t = str(text or '').strip()
    # 1) Marca explicita C.P. / CP / C.P : 14308
    m = re.search(r'\b[Cc]\.?\s*[Pp]\.?\s*[:.]?\s*(\d{5})\b', t)
    if m:
        return m.group(1)
    # 2) CP de 5 digitos SOLO si esta al final del texto (ultimos ~8 chars)
    m = re.search(r'\b(\d{5})\b', t[-8:])
    if m:
        return m.group(1)
    return ''


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


# ── Google Maps Geocoding ─────────────────────────────────────────────────────

def _google_component(components: list, type_name: str, short: bool = False) -> str:
    """Extrae un componente de address_components por su tipo."""
    key = 'short_name' if short else 'long_name'
    for c in components:
        if type_name in c.get('types', []):
            return c.get(key, '')
    return ''


def parse_google(result: dict) -> dict:
    """Convierte un resultado de Google Geocoding al formato estandar."""
    comps = result.get('address_components', [])
    loc = result.get('geometry', {}).get('location', {})
    municipio = (_google_component(comps, 'locality')
                 or _google_component(comps, 'administrative_area_level_2')
                 or _google_component(comps, 'sublocality'))
    return {
        'lat': float(loc.get('lat')),
        'lng': float(loc.get('lng')),
        'estado_geo': _google_component(comps, 'administrative_area_level_1'),
        'municipio_geo': municipio,
        'cp_geo': _google_component(comps, 'postal_code'),
    }


def google_search(query: str, api_key: str) -> dict | None:
    """Forward geocode usando Google Maps Geocoding API.
    Devuelve None si no hay key, si falla, o si no hay resultados."""
    if not api_key:
        return None
    try:
        r = requests.get(
            'https://maps.googleapis.com/maps/api/geocode/json',
            params={'address': query, 'key': api_key,
                    'region': 'mx', 'language': 'es'},
            timeout=10
        )
        data = r.json()
        if data.get('status') == 'OK' and data.get('results'):
            return parse_google(data['results'][0])
    except Exception:
        pass
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
            issues.append(f"Estado \"{row_data['est']}\" != geo \"{geo['estado_geo']}\"")
    if mun_r and geo.get('municipio_geo'):
        mun_g = normalize(geo['municipio_geo'])
        if not (mun_g in mun_r or mun_r in mun_g):
            issues.append(f"Municipio \"{row_data['mun']}\" != geo \"{geo['municipio_geo']}\"")
    if cp_r and geo.get('cp_geo') and cp_r != geo['cp_geo']:
        issues.append(f"CP \"{cp_r}\" != geo \"{geo['cp_geo']}\"")
    return issues


# ── Main geocoding function ───────────────────────────────────────────────────

def geocode_row(row, mapping: dict, cp_lookup: dict, kml_zones: dict,
                delay: float = 1.1, api_key: str = '') -> dict:
    """
    Geocode a single row using the fallback strategy:
    1. Existing coords -> reverse geocode + validate
    2. CP + Estado -> CP lookup table  (CP propio o extraido de la direccion)
    3. Solo CP -> Nominatim
    4. Nombre + Ciudad + Estado -> Nominatim
    5. Ciudad + Estado -> Nominatim
    6. Google Maps -> nombre/direccion completa (si hay api_key)
    7. Coords -> KML point-in-polygon (fallback de zonas)
    """

    def gv(fid):
        col = mapping.get(fid)
        if col and col in row.index:
            val = row[col]
            if val is not None and str(val).strip() not in ('', 'nan', 'None'):
                return str(val).strip()
        return ''

    lat_s, lng_s = gv('lat'), gv('lng')
    est_s = gv('est')
    mun_s = gv('mun')
    nom_s = gv('nom')
    dir_s = gv('dir')

    # CP: primero la columna propia; si no, se intenta extraer de la direccion.
    # cp_propio = True cuando viene de una columna CP dedicada (confiable).
    cp_s = clean_cp(gv('cp'))
    cp_propio = bool(cp_s)
    if not cp_s and dir_s:
        cp_s = extract_cp_from_text(dir_s)
    if not cp_s and nom_s:
        cp_s = extract_cp_from_text(nom_s)

    row_data = {'lat': lat_s, 'lng': lng_s, 'cp': cp_s,
                'est': est_s, 'mun': mun_s, 'nom': nom_s}

    base = {'lat_geo': '', 'lng_geo': '', 'estado_geo': '', 'municipio_geo': '',
            'cp_geo': '', 'zona_sismica': '', 'zona_cresta': '', 'hidro2': '',
            'metodo': '', 'observacion': ''}

    def enrich_from_cp(cp_key: str) -> dict:
        """Get all zone data from CP lookup."""
        entry = cp_lookup.get(clean_cp(cp_key), {})
        return {
            'lat_geo': str(entry.get('lat', '')) if entry.get('lat') else '',
            'lng_geo': str(entry.get('lng', '')) if entry.get('lng') else '',
            'estado_geo': entry.get('estado', ''),
            'municipio_geo': entry.get('municipio', ''),
            'cp_geo': clean_cp(cp_key),
            'zona_sismica': entry.get('zona_sismica', ''),
            'zona_cresta': entry.get('zona_cresta', ''),
            'hidro2': entry.get('hidro2', ''),
        }

    def enrich_zones_from_coords(lat: float, lng: float) -> dict:
        """Get zone data from KML polygons given coords."""
        return get_zones_from_kml(lng, lat, kml_zones)

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
                          'metodo': '1-Coordenadas (inverso)'}
                if geo['cp_geo']:
                    z = enrich_from_cp(geo['cp_geo'])
                    result['zona_sismica'] = z['zona_sismica']
                    result['zona_cresta'] = z['zona_cresta']
                    result['hidro2'] = z['hidro2']
                if not result['zona_sismica']:
                    result.update(enrich_zones_from_coords(lat_f, lng_f))
                issues = validate_fields(row_data, result)
                result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK'
                return result
        except (ValueError, TypeError):
            pass

    # ── Step 2: CP + Estado from lookup ──────────────────────────────────────
    if cp_s:
        entry = cp_lookup.get(cp_s)
        if entry:
            conflicto_estado = False
            if est_s and entry.get('estado'):
                if normalize(est_s) not in normalize(entry['estado']) and \
                   normalize(entry['estado']) not in normalize(est_s):
                    conflicto_estado = True
            # Si el CP fue EXTRAIDO de la direccion (no columna propia) y choca con
            # el estado del archivo, probablemente no es un CP real -> se ignora y
            # se continua con geocodificacion por direccion/ciudad+estado.
            if conflicto_estado and not cp_propio:
                cp_s = ''  # descartar y caer a pasos siguientes
            else:
                result = {**base, **enrich_from_cp(cp_s), 'metodo': '2-CP en catálogo'}
                issues = []
                if conflicto_estado:
                    issues.append(f"Estado \"{est_s}\" != lookup \"{entry['estado']}\"")
                result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK (CP en catálogo)'
                if not result['zona_sismica'] and result['lat_geo']:
                    try:
                        result.update(enrich_zones_from_coords(float(result['lat_geo']), float(result['lng_geo'])))
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
                      'metodo': '3-Solo CP'}
            z = enrich_from_cp(geo['cp_geo'] or cp_s)
            result['zona_sismica'] = z['zona_sismica']
            result['zona_cresta'] = z['zona_cresta']
            result['hidro2'] = z['hidro2']
            if not result['zona_sismica']:
                result.update(enrich_zones_from_coords(geo['lat'], geo['lng']))
            issues = validate_fields(row_data, result)
            result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK (CP por Nominatim)'
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
                      'metodo': '4-Nombre + Ciudad'}
            if geo['cp_geo']:
                z = enrich_from_cp(geo['cp_geo'])
                result['zona_sismica'] = z['zona_sismica']
                result['zona_cresta'] = z['zona_cresta']
                result['hidro2'] = z['hidro2']
            if not result['zona_sismica']:
                result.update(enrich_zones_from_coords(geo['lat'], geo['lng']))
            issues = validate_fields(row_data, result)
            result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK (nombre + ciudad)'
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
                      'metodo': '5-Ciudad + Estado'}
            if geo['cp_geo']:
                z = enrich_from_cp(geo['cp_geo'])
                result['zona_sismica'] = z['zona_sismica']
                result['zona_cresta'] = z['zona_cresta']
                result['hidro2'] = z['hidro2']
            if not result['zona_sismica']:
                result.update(enrich_zones_from_coords(geo['lat'], geo['lng']))
            issues = validate_fields(row_data, result)
            result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK (ciudad + estado)'
            return result

    # ── Step 6: Google Maps (si hay API key) ─────────────────────────────────
    # Ultimo recurso para lo que Nominatim no encuentra (puentes, naves, comercios).
    if api_key:
        partes6 = [p for p in [nom_s, dir_s, mun_s, est_s] if p]
        if partes6:
            query = ', '.join(partes6 + ['Mexico'])
            geo = google_search(query, api_key)
            if geo:
                result = {**base,
                          'lat_geo': str(geo['lat']), 'lng_geo': str(geo['lng']),
                          'estado_geo': geo['estado_geo'],
                          'municipio_geo': geo['municipio_geo'],
                          'cp_geo': geo['cp_geo'],
                          'metodo': '6-Google Maps'}
                if geo['cp_geo']:
                    z = enrich_from_cp(geo['cp_geo'])
                    result['zona_sismica'] = z['zona_sismica']
                    result['zona_cresta'] = z['zona_cresta']
                    result['hidro2'] = z['hidro2']
                if not result['zona_sismica']:
                    result.update(enrich_zones_from_coords(geo['lat'], geo['lng']))
                issues = validate_fields(row_data, result)
                result['observacion'] = 'CONFLICTO: ' + ' | '.join(issues) if issues else 'OK (Google Maps)'
                return result

    # ── Step 7: Sin datos suficientes ─────────────────────────────────────────
    return {**base, 'metodo': '-', 'observacion': 'Sin datos suficientes para geocodificar'}


def enrich_zones(lat: float, lng: float, cp: str, cp_lookup: dict, kml_zones: dict) -> dict:
    """Get zone data from CP lookup first, then KML fallback."""
    entry = cp_lookup.get(clean_cp(cp), {})
    result = {
        'zona_sismica': entry.get('zona_sismica', ''),
        'zona_cresta': entry.get('zona_cresta', ''),
        'hidro2': entry.get('hidro2', ''),
    }
    if not result['zona_sismica']:
        result.update(get_zones_from_kml(lng, lat, kml_zones))
    return result


# ── Exportacion a KML para Google My Maps ─────────────────────────────────────

# Paleta de colores KML por zona sismica (formato aabbggrr de Google Earth)
_KML_COLORES = {
    'A':  'ff00b400',  # verde
    'B':  'ff00d7ff',  # amarillo
    'B1': 'ff00aaff',  # naranja claro
    'C':  'ff0078ff',  # naranja
    'D':  'ff0000ff',  # rojo
    'E':  'ff8000ff',  # rosa/magenta
    'F':  'ffff0000',  # azul
}
_KML_DEFAULT = 'ff909090'  # gris


def _kml_escape(s) -> str:
    s = str(s or '')
    return (s.replace('&', '&amp;').replace('<', '&lt;')
             .replace('>', '&gt;').replace('"', '&quot;'))


def build_kml(df_result, nom_col=None, dir_col=None) -> bytes:
    """Genera un KML con los puntos geocodificados, coloreados por Zona Sismica,
    listo para importar en Google My Maps."""
    zonas = sorted({str(z).strip() for z in df_result.get('zona_sismica', [])
                    if str(z).strip()})

    styles = []
    for z in zonas:
        color = _KML_COLORES.get(z, _KML_DEFAULT)
        styles.append(f"""  <Style id="zs_{_kml_escape(z)}">
    <IconStyle><color>{color}</color>
      <Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon>
    </IconStyle>
  </Style>""")
    styles.append(f"""  <Style id="zs_default">
    <IconStyle><color>{_KML_DEFAULT}</color>
      <Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon>
    </IconStyle>
  </Style>""")

    placemarks = []
    for _, row in df_result.iterrows():
        lat = str(row.get('lat_geo', '')).strip()
        lng = str(row.get('lng_geo', '')).strip()
        if not lat or not lng:
            continue
        try:
            latf, lngf = float(lat), float(lng)
        except (ValueError, TypeError):
            continue

        nom = str(row[nom_col]) if nom_col and nom_col in row and pd.notna(row[nom_col]) else 'Ubicacion'
        dir_ = str(row[dir_col]) if dir_col and dir_col in row and pd.notna(row[dir_col]) else ''
        zs = str(row.get('zona_sismica', '')).strip()
        style_id = f"zs_{zs}" if zs in zonas else "zs_default"

        desc = f"""<![CDATA[
          <b>Direccion:</b> {_kml_escape(dir_)}<br/>
          <b>CP:</b> {_kml_escape(row.get('cp_geo',''))}<br/>
          <b>Estado:</b> {_kml_escape(row.get('estado_geo',''))}<br/>
          <b>Municipio:</b> {_kml_escape(row.get('municipio_geo',''))}<br/>
          <b>Zona Sismica:</b> {_kml_escape(zs)}<br/>
          <b>Zona Cresta:</b> {_kml_escape(row.get('zona_cresta',''))}<br/>
          <b>Hidro2:</b> {_kml_escape(row.get('hidro2',''))}<br/>
          <b>Observacion:</b> {_kml_escape(row.get('observacion',''))}
        ]]>"""

        placemarks.append(f"""  <Placemark>
    <name>{_kml_escape(nom)}</name>
    <description>{desc}</description>
    <styleUrl>#{style_id}</styleUrl>
    <Point><coordinates>{lngf},{latf},0</coordinates></Point>
  </Placemark>""")

    kml = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
  <name>Ubicaciones SUMMA</name>
{chr(10).join(styles)}
{chr(10).join(placemarks)}
</Document>
</kml>"""
    return kml.encode('utf-8')


# ── Consulta de un solo punto (CP o direccion) ────────────────────────────────

def geocode_single(texto: str, cp_lookup: dict, kml_zones: dict,
                   delay: float = 1.1, api_key: str = '') -> dict:
    """Geocodifica UN punto a partir de texto libre (CP o direccion).
    Devuelve coordenadas, estado/municipio, CP y las tres zonas.
    No requiere Excel ni mapeo. Usa la misma estrategia de fallback."""
    base = {'lat_geo': '', 'lng_geo': '', 'estado_geo': '', 'municipio_geo': '',
            'cp_geo': '', 'zona_sismica': '', 'zona_cresta': '', 'hidro2': '',
            'metodo': '', 'observacion': ''}
    texto = str(texto or '').strip()
    if not texto:
        return {**base, 'observacion': 'Escribe un CP o una dirección'}

    def enrich_from_cp(cp_key):
        entry = cp_lookup.get(clean_cp(cp_key), {})
        return {
            'lat_geo': str(entry.get('lat', '')) if entry.get('lat') else '',
            'lng_geo': str(entry.get('lng', '')) if entry.get('lng') else '',
            'estado_geo': entry.get('estado', ''),
            'municipio_geo': entry.get('municipio', ''),
            'cp_geo': clean_cp(cp_key),
            'zona_sismica': entry.get('zona_sismica', ''),
            'zona_cresta': entry.get('zona_cresta', ''),
            'hidro2': entry.get('hidro2', ''),
        }

    def zonas_por_coords(lat, lng):
        return get_zones_from_kml(lng, lat, kml_zones)

    # Detectar si el texto es un CP puro (5 digitos) o trae un CP embebido
    solo_digitos = texto.replace(' ', '')
    cp_directo = ''
    if solo_digitos.isdigit() and len(solo_digitos) <= 5:
        cp_directo = clean_cp(solo_digitos)
    else:
        cp_directo = extract_cp_from_text(texto)

    # 1) Si hay CP y esta en catalogo -> respuesta directa (gratis, instantanea)
    if cp_directo and cp_directo in cp_lookup:
        result = {**base, **enrich_from_cp(cp_directo), 'metodo': 'CP en catálogo'}
        if not result['zona_sismica'] and result['lat_geo']:
            try:
                result.update(zonas_por_coords(float(result['lat_geo']), float(result['lng_geo'])))
            except Exception:
                pass
        result['observacion'] = 'OK'
        return result

    # 2) Buscar por texto en Nominatim (direccion/lugar)
    geo = nominatim_search(f"{texto}, Mexico", delay)
    # 3) Si Nominatim falla y hay Google, intentar Google
    if not geo and api_key:
        geo = google_search(f"{texto}, Mexico", api_key)
        fuente = 'Google Maps'
    else:
        fuente = 'Nominatim'

    if geo:
        result = {**base,
                  'lat_geo': str(geo['lat']), 'lng_geo': str(geo['lng']),
                  'estado_geo': geo['estado_geo'],
                  'municipio_geo': geo['municipio_geo'],
                  'cp_geo': geo['cp_geo'] or cp_directo,
                  'metodo': fuente}
        # Zonas: primero por CP encontrado, si no por poligono
        cp_final = geo['cp_geo'] or cp_directo
        if cp_final and clean_cp(cp_final) in cp_lookup:
            z = enrich_from_cp(cp_final)
            result['zona_sismica'] = z['zona_sismica']
            result['zona_cresta'] = z['zona_cresta']
            result['hidro2'] = z['hidro2']
        if not result['zona_sismica']:
            result.update(zonas_por_coords(geo['lat'], geo['lng']))
        result['observacion'] = 'OK'
        return result

    return {**base, 'metodo': '-', 'observacion': 'No se encontró el punto'}