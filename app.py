import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import json, gzip, pickle, time, requests
from utils.geocoder import geocode_row, enrich_zones
from utils.data_loader import load_cp_lookup, load_kml_zones

st.set_page_config(
    page_title="Geolocalizador de Ubicaciones México",
    page_icon="🗺️",
    layout="wide"
)

# ── Estilos ──────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.main-title { font-size: 2rem; font-weight: 700; margin-bottom: 0; }
.sub-title  { font-size: 1rem; color: #666; margin-bottom: 1.5rem; }
.metric-box { background: #f8f9fa; border-radius: 8px; padding: 12px 16px;
              border-left: 4px solid #1f77b4; margin-bottom: 8px; }
.badge-ok     { background: #d4edda; color: #155724; padding: 2px 8px;
                border-radius: 4px; font-size: 0.8rem; font-weight: 500; }
.badge-warn   { background: #fff3cd; color: #856404; padding: 2px 8px;
                border-radius: 4px; font-size: 0.8rem; font-weight: 500; }
.badge-error  { background: #f8d7da; color: #721c24; padding: 2px 8px;
                border-radius: 4px; font-size: 0.8rem; font-weight: 500; }
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown('<div class="main-title">🗺️ Geolocalizador de Ubicaciones México</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Sube tu Excel con ubicaciones y obtén coordenadas, zonas sísmicas, cresta e hidrometeorológicas</div>', unsafe_allow_html=True)

# ── Cargar datos de referencia ────────────────────────────────────────────────
@st.cache_resource(show_spinner="Cargando datos de referencia...")
def get_reference_data():
    cp_lookup = load_cp_lookup()
    kml_zones = load_kml_zones()
    return cp_lookup, kml_zones

cp_lookup, kml_zones = get_reference_data()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Configuración")
    st.markdown(f"📦 **{len(cp_lookup):,}** CPs en base de referencia")
    st.markdown("---")
    st.markdown("**Estrategia de búsqueda:**")
    st.markdown("1️⃣ Coordenadas existentes → inverso")
    st.markdown("2️⃣ CP + Estado")
    st.markdown("3️⃣ Solo CP")
    st.markdown("4️⃣ Nombre + Ciudad + Estado")
    st.markdown("5️⃣ Ciudad + Estado")
    st.markdown("6️⃣ Punto en polígono KML")
    st.markdown("---")
    delay = st.slider("Delay entre requests (seg)", 1.0, 3.0, 1.1, 0.1,
                      help="Para respetar el rate limit de Nominatim")
    st.markdown("---")
    st.markdown("**Columnas de salida agregadas:**")
    st.caption("Lat · Lng · Estado_geo · Municipio_geo · CP_geo · Zona_Sismica · Zona_Cresta · Hidro2 · Método · Observación")

# ── Upload ────────────────────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "📂 Selecciona tu archivo Excel",
    type=["xlsx", "xls"],
    help="El archivo puede tener cualquier formato — dirección, CP, estado, ciudad, lat/lng"
)

if uploaded:
    df = pd.read_excel(uploaded)
    st.success(f"✅ Archivo cargado: **{uploaded.name}** — {len(df):,} filas, {len(df.columns)} columnas")

    # ── Mapeo de columnas ─────────────────────────────────────────────────────
    with st.expander("🔧 Mapeo de columnas", expanded=True):
        cols = ["— no usar —"] + list(df.columns)
        CAMPOS = {
            'lat':    ('📍 Latitud existente',    ['latitud','latitude','lat']),
            'lng':    ('📍 Longitud existente',   ['longitud','longitude','lon','lng']),
            'est':    ('🗺️ Estado',               ['estado','state','entidad']),
            'mun':    ('🏙️ Ciudad/Municipio',     ['municipio','ciudad','city']),
            'cp':     ('📮 CP',                   ['cp','c.p.','codigo postal','postal','zip']),
            'nom':    ('🏢 Nombre/Sucursal',      ['nombre','sucursal','unidad','tienda','locacion']),
            'dir':    ('📌 Dirección',             ['direccion','dirección','domicilio','address']),
        }

        def autodetect(field_kw):
            for col in df.columns:
                nc = str(col).strip().lower()
                import unicodedata
                nc = ''.join(c for c in unicodedata.normalize('NFD', nc) if unicodedata.category(c) != 'Mn')
                for kw in field_kw:
                    if nc == kw or nc.startswith(kw):
                        return col
            return None

        col1, col2 = st.columns(2)
        mapping = {}
        for i, (fid, (label, kws)) in enumerate(CAMPOS.items()):
            detected = autodetect(kws)
            default_idx = cols.index(detected) if detected and detected in cols else 0
            with (col1 if i % 2 == 0 else col2):
                sel = st.selectbox(label, cols, index=default_idx, key=f"map_{fid}")
                mapping[fid] = sel if sel != "— no usar —" else None

    def gv(row, fid):
        col = mapping.get(fid)
        return str(row[col]).strip() if col and col in row and pd.notna(row[col]) else ''

    # Preview primera fila
    if len(df) > 0:
        row0 = df.iloc[0]
        lat0, lng0 = gv(row0,'lat'), gv(row0,'lng')
        has_coords = lat0 and lng0 and lat0 not in ('nan','') and lng0 not in ('nan','')
        cp0 = gv(row0,'cp')
        with st.expander("👁️ Vista previa fila 1"):
            for fid, (label, _) in CAMPOS.items():
                val = gv(row0, fid)
                icon = "✅" if val and val != 'nan' else "⬜"
                st.caption(f"{icon} {label}: **{val or '—'}**")
            if has_coords:
                st.info(f"🔄 Modo: **inverso** — reverseGeocode({float(lat0):.5f}, {float(lng0):.5f})")
            elif cp0:
                st.info(f"🔍 Modo: **CP lookup** — CP={cp0}")
            else:
                nom = gv(row0,'nom'); mun = gv(row0,'mun'); est = gv(row0,'est')
                st.info(f"🔍 Modo: **Nominatim** — {', '.join(filter(None,[nom,mun,est,'Mexico']))}")

    # ── Procesar ──────────────────────────────────────────────────────────────
    if st.button("▶️ Procesar", type="primary", use_container_width=True):
        results = []
        prog = st.progress(0, text="Iniciando...")
        log_area = st.empty()
        logs = []
        ok = warn = fail = 0

        for i, row in df.iterrows():
            pct = (i + 1) / len(df)
            prog.progress(pct, text=f"Fila {i+1} de {len(df)}")

            result = geocode_row(row, mapping, cp_lookup, kml_zones, delay)
            results.append(result)

            status = result.get('observacion','')
            if 'CONFLICTO' in status: warn += 1
            elif 'No encontrado' in status or 'Sin datos' in status: fail += 1
            else: ok += 1

            emoji = '✅' if ok > warn+fail else '⚠️' if warn > fail else '❌'
            logs.append(f"Fila {i+1}: {result.get('metodo','?')} → {status[:60]}")
            log_area.code('\n'.join(logs[-8:]))

        prog.progress(1.0, text="✅ Completado")
        st.session_state['results'] = results
        st.session_state['df_orig'] = df
        st.session_state['mapping'] = mapping

        # Métricas
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", len(df))
        c2.metric("✅ OK", ok)
        c3.metric("⚠️ Conflictos", warn)
        c4.metric("❌ Sin resultado", fail)

# ── Resultados ────────────────────────────────────────────────────────────────
if 'results' in st.session_state:
    results = st.session_state['results']
    df_orig = st.session_state['df_orig']

    extra_cols = ['lat_geo','lng_geo','estado_geo','municipio_geo','cp_geo',
                  'zona_sismica','zona_cresta','hidro2','metodo','observacion']
    df_result = df_orig.copy()
    for col in extra_cols:
        df_result[col] = [r.get(col,'') for r in results]

    tab1, tab2 = st.tabs(["🗺️ Mapa", "📊 Tabla de resultados"])

    with tab1:
        st.markdown("### Puntos geocodificados")

        # Filtros
        fc1, fc2, fc3 = st.columns(3)
        zs_opts = ['Todas'] + sorted(df_result['zona_sismica'].dropna().unique().tolist())
        zc_opts = ['Todas'] + sorted(df_result['zona_cresta'].dropna().unique().tolist())
        h2_opts = ['Todas'] + sorted(df_result['hidro2'].dropna().unique().tolist())
        fzs = fc1.selectbox("Zona Sísmica", zs_opts)
        fzc = fc2.selectbox("Zona Cresta", zc_opts)
        fh2 = fc3.selectbox("Hidro2", h2_opts)

        df_map = df_result[df_result['lat_geo'] != ''].copy()
        df_map['lat_f'] = pd.to_numeric(df_map['lat_geo'], errors='coerce')
        df_map['lng_f'] = pd.to_numeric(df_map['lng_geo'], errors='coerce')
        df_map = df_map.dropna(subset=['lat_f','lng_f'])

        if fzs != 'Todas': df_map = df_map[df_map['zona_sismica'] == fzs]
        if fzc != 'Todas': df_map = df_map[df_map['zona_cresta'] == fzc]
        if fh2 != 'Todas': df_map = df_map[df_map['hidro2'] == fh2]

        st.caption(f"Mostrando **{len(df_map)}** puntos")

        # Mapa Folium
        if len(df_map) > 0:
            clat = df_map['lat_f'].mean()
            clng = df_map['lng_f'].mean()
            m = folium.Map(location=[clat, clng], zoom_start=5,
                          tiles='CartoDB positron')

            nom_col = st.session_state['mapping'].get('nom')
            dir_col = st.session_state['mapping'].get('dir')

            for _, row in df_map.iterrows():
                obs = str(row.get('observacion',''))
                color = 'green' if obs.startswith('OK') else 'orange' if 'CONFLICTO' in obs else 'red'
                nom = str(row[nom_col]) if nom_col and nom_col in row else ''
                dir_ = str(row[dir_col]) if dir_col and dir_col in row else ''
                popup_html = f"""
                <div style="font-family:sans-serif;min-width:200px">
                  <b style="font-size:14px">{nom or 'Sin nombre'}</b><br>
                  <span style="color:#666;font-size:12px">{dir_}</span><hr style="margin:6px 0">
                  <b>CP:</b> {row.get('cp_geo','')}<br>
                  <b>Estado:</b> {row.get('estado_geo','')}<br>
                  <b>Municipio:</b> {row.get('municipio_geo','')}<br>
                  <hr style="margin:6px 0">
                  <b>Zona Sísmica:</b> {row.get('zona_sismica','')}<br>
                  <b>Zona Cresta:</b> {row.get('zona_cresta','')}<br>
                  <b>Hidro2:</b> {row.get('hidro2','')}<br>
                  <hr style="margin:6px 0">
                  <span style="font-size:11px;color:#888">Método: {row.get('metodo','')}</span><br>
                  <span style="font-size:11px;color:#888">{obs[:80]}</span>
                </div>"""
                folium.Marker(
                    location=[row['lat_f'], row['lng_f']],
                    popup=folium.Popup(popup_html, max_width=280),
                    tooltip=nom or f"Fila {row.name+1}",
                    icon=folium.Icon(color=color, icon='home', prefix='fa')
                ).add_to(m)

            st_folium(m, width=None, height=550, returned_objects=[])
        else:
            st.warning("No hay puntos con coordenadas para mostrar con los filtros actuales.")

    with tab2:
        st.dataframe(df_result, use_container_width=True, height=400)

        # Descarga
        import io
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            df_result.to_excel(writer, index=False, sheet_name='Geocodificado')
        buf.seek(0)
        st.download_button(
            "⬇️ Descargar Excel enriquecido",
            buf,
            file_name="resultado_geocodificado.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary"
        )
