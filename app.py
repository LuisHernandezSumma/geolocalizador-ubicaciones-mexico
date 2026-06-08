import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import plotly.express as px
import json, gzip, pickle, time, io, base64, html
from pathlib import Path
from utils.geocoder import geocode_row, enrich_zones, build_kml
from utils.data_loader import load_cp_lookup, load_kml_zones

# Colores institucionales SUMMA
SUMMA_AZUL = "#0036A1"
SUMMA_AZUL_CLARO = "#E8EEF8"
SUMMA_GRIS = "#6B7280"

st.set_page_config(page_title="Geolocalizador SUMMA", page_icon=":world_map:", layout="wide")

st.markdown(f"""
<style>
.main-title {{ font-size: 1.9rem; font-weight: 700; color: {SUMMA_AZUL}; margin-bottom: 0; }}
.sub-title  {{ font-size: 0.95rem; color: {SUMMA_GRIS}; margin-bottom: 1.2rem; }}
.stButton>button[kind="primary"], .stDownloadButton>button {{ background-color: {SUMMA_AZUL}; border-color: {SUMMA_AZUL}; }}
.stProgress > div > div > div > div {{ background-color: {SUMMA_AZUL}; }}
div[data-baseweb="tab-list"] button[aria-selected="true"] {{ color: {SUMMA_AZUL}; }}
div[data-baseweb="tab-highlight"] {{ background-color: {SUMMA_AZUL}; }}
.summa-header {{ display:flex; align-items:center; gap:16px; margin-bottom:0.4rem; }}
</style>
""", unsafe_allow_html=True)

def get_logo_b64():
    p = Path(__file__).parent / "assets" / "summa_logo.png"
    if p.exists():
        return base64.b64encode(p.read_bytes()).decode()
    return None

logo = get_logo_b64()
if logo:
    st.markdown(f"""
    <div class="summa-header">
      <img src="data:image/png;base64,{logo}" style="height:70px"/>
      <div>
        <div class="main-title">Geolocalizador de Ubicaciones Mexico</div>
        <div class="sub-title">Coordenadas, zonas sismicas, cresta e hidrometeorologicas - Intermediario de Reaseguro</div>
      </div>
    </div>
    """, unsafe_allow_html=True)
else:
    st.markdown('<div class="main-title">Geolocalizador de Ubicaciones Mexico</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">Coordenadas, zonas sismicas, cresta e hidrometeorologicas</div>', unsafe_allow_html=True)

@st.cache_resource(show_spinner="Cargando datos de referencia...")
def get_reference_data():
    return load_cp_lookup(), load_kml_zones()

cp_lookup, kml_zones = get_reference_data()

with st.sidebar:
    if logo:
        st.markdown(f'<img src="data:image/png;base64,{logo}" style="width:160px;margin-bottom:12px"/>', unsafe_allow_html=True)
    st.markdown("### Configuracion")
    st.markdown(f"**{len(cp_lookup):,}** CPs en base de referencia")
    st.markdown("---")
    st.markdown("**Estrategia de busqueda:**")
    st.markdown("1. Coordenadas existentes -> inverso")
    st.markdown("2. CP + Estado")
    st.markdown("3. Solo CP")
    st.markdown("4. Nombre + Ciudad + Estado")
    st.markdown("5. Ciudad + Estado")
    st.markdown("6. Punto en poligono KML")
    st.markdown("---")
    delay = st.slider("Delay entre requests (seg)", 1.0, 3.0, 1.1, 0.1,
                      help="Para respetar el rate limit de Nominatim")

uploaded = st.file_uploader("Selecciona tu archivo Excel", type=["xlsx", "xls"],
    help="El archivo puede tener cualquier formato - direccion, CP, estado, ciudad, lat/lng")

if uploaded:
    from openpyxl import load_workbook
    import unicodedata
    file_bytes = uploaded.read()
    wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    header_row = 0
    for i, row in enumerate(ws.iter_rows(max_row=10, values_only=True)):
        str_vals = [str(v).strip() for v in row
                    if v is not None and str(v).strip() not in ('', 'None', 'nan')
                    and not str(v).replace('.', '').replace('-', '').strip().isnumeric()]
        if len(str_vals) > 2:
            header_row = i
            break
    uploaded.seek(0)
    df = pd.read_excel(io.BytesIO(file_bytes), header=header_row)
    df = df.dropna(axis=1, how='all')
    df.columns = [str(c).strip() for c in df.columns]
    st.success(f"Archivo cargado: **{uploaded.name}** - {len(df):,} filas, {len(df.columns)} columnas")

    with st.expander("Mapeo de columnas", expanded=True):
        cols = ["- no usar -"] + list(df.columns)
        CAMPOS = {
            'lat': ('Latitud existente',  ['latitud', 'latitude', 'lat']),
            'lng': ('Longitud existente', ['longitud', 'longitude', 'lon', 'lng']),
            'est': ('Estado',             ['estado', 'state', 'entidad']),
            'mun': ('Ciudad/Municipio',   ['municipio', 'ciudad', 'city', 'poblacion', 'poblacion', 'ciudad juarez', 'locacion', 'locacion']),
            'cp':  ('CP',                 ['cp', 'c.p.', 'c.p', 'codigo postal', 'codigo postal', 'postal', 'zip', 'cod postal']),
            'nom': ('Nombre/Sucursal',    ['nombre del puente', 'nombre del inmueble', 'nombre', 'sucursal', 'tienda', 'unidad', 'inmueble']),
            'dir': ('Direccion',          ['direccion', 'direccion', 'domicilio', 'address', 'ubicacion', 'ubicacion']),
        }

        def _norm(c):
            nc = str(c).strip().lower()
            return ''.join(ch for ch in unicodedata.normalize('NFD', nc) if unicodedata.category(ch) != 'Mn')

        def autodetect(field_kw):
            for col in df.columns:
                if _norm(col) in field_kw:
                    return col
            for kw in sorted(field_kw, key=len, reverse=True):
                for col in df.columns:
                    if _norm(col).startswith(kw):
                        return col
            return None

        col1, col2 = st.columns(2)
        mapping = {}
        for i, (fid, (label, kws)) in enumerate(CAMPOS.items()):
            detected = autodetect(kws)
            default_idx = cols.index(detected) if detected and detected in cols else 0
            with (col1 if i % 2 == 0 else col2):
                sel = st.selectbox(label, cols, index=default_idx, key=f"map_{fid}")
                mapping[fid] = sel if sel != "- no usar -" else None

    def gv(row, fid):
        col = mapping.get(fid)
        return str(row[col]).strip() if col and col in row and pd.notna(row[col]) else ''

    if len(df) > 0:
        row0 = df.iloc[0]
        lat0, lng0 = gv(row0, 'lat'), gv(row0, 'lng')
        has_coords = lat0 and lng0 and lat0 not in ('nan', '') and lng0 not in ('nan', '')
        cp0 = gv(row0, 'cp')
        with st.expander("Vista previa fila 1"):
            for fid, (label, _) in CAMPOS.items():
                val = gv(row0, fid)
                icon = "OK" if val and val != 'nan' else "-"
                st.caption(f"{icon} {label}: **{val or '-'}**")
            if has_coords:
                st.info(f"Modo: **inverso** - reverseGeocode({float(lat0):.5f}, {float(lng0):.5f})")
            elif cp0:
                st.info(f"Modo: **CP lookup** - CP={cp0}")
            else:
                nom = gv(row0, 'nom'); mun = gv(row0, 'mun'); est = gv(row0, 'est')
                st.info(f"Modo: **Nominatim** - {', '.join(filter(None, [nom, mun, est, 'Mexico']))}")

    if st.button("Procesar", type="primary", use_container_width=True):
        results = []
        prog = st.progress(0, text="Iniciando...")
        log_area = st.empty()
        logs = []
        ok = warn = fail = 0
        for i, row in df.iterrows():
            prog.progress((i + 1) / len(df), text=f"Fila {i+1} de {len(df)}")
            result = geocode_row(row, mapping, cp_lookup, kml_zones, delay)
            results.append(result)
            status = result.get('observacion', '')
            if 'CONFLICTO' in status: warn += 1
            elif 'No encontrado' in status or 'Sin datos' in status: fail += 1
            else: ok += 1
            logs.append(f"Fila {i+1}: {result.get('metodo','?')} -> {status[:60]}")
            log_area.code('\n'.join(logs[-8:]))
        prog.progress(1.0, text="Completado")
        st.session_state['results'] = results
        st.session_state['df_orig'] = df
        st.session_state['mapping'] = mapping
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", len(df))
        c2.metric("OK", ok)
        c3.metric("Conflictos", warn)
        c4.metric("Sin resultado", fail)

if 'results' in st.session_state:
    results = st.session_state['results']
    df_orig = st.session_state['df_orig']
    mapping = st.session_state['mapping']
    extra_cols = ['lat_geo', 'lng_geo', 'estado_geo', 'municipio_geo', 'cp_geo',
                  'zona_sismica', 'zona_cresta', 'hidro2', 'metodo', 'observacion']
    df_result = df_orig.copy()
    for col in extra_cols:
        df_result[col] = [r.get(col, '') for r in results]

    nom_col = mapping.get('nom')
    dir_col = mapping.get('dir')

    def row_search_blob(row):
        parts = [
            str(row[nom_col]) if nom_col and nom_col in row else '',
            str(row[dir_col]) if dir_col and dir_col in row else '',
            str(row.get('estado_geo', '')), str(row.get('municipio_geo', '')),
            str(row.get('cp_geo', '')),
        ]
        return ' '.join(parts).lower()

    df_result['_blob'] = df_result.apply(row_search_blob, axis=1)

    tab1, tab2, tab3 = st.tabs(["Mapa", "Tabla de resultados", "Dashboard"])

    with tab1:
        st.markdown("### Puntos geocodificados")
        search = st.text_input("Buscar por estado, CP, nombre/sucursal o direccion",
                               placeholder="Escribe para filtrar...")

        def opts_with_count(col):
            d = df_result[df_result[col].astype(str).str.strip() != '']
            vc = d[col].value_counts()
            return [""] + [f"{k} ({v})" for k, v in vc.items()]

        def strip_count(label):
            return label.rsplit(" (", 1)[0] if label else ""

        fc1, fc2, fc3 = st.columns(3)
        fzs = fc1.selectbox("Zona Sismica", opts_with_count('zona_sismica'), format_func=lambda x: x if x else "-")
        fzc = fc2.selectbox("Zona Cresta", opts_with_count('zona_cresta'), format_func=lambda x: x if x else "-")
        fh2 = fc3.selectbox("Hidro2", opts_with_count('hidro2'), format_func=lambda x: x if x else "-")

        df_map = df_result[df_result['lat_geo'] != ''].copy()
        df_map['lat_f'] = pd.to_numeric(df_map['lat_geo'], errors='coerce')
        df_map['lng_f'] = pd.to_numeric(df_map['lng_geo'], errors='coerce')
        df_map = df_map.dropna(subset=['lat_f', 'lng_f'])

        if strip_count(fzs): df_map = df_map[df_map['zona_sismica'] == strip_count(fzs)]
        if strip_count(fzc): df_map = df_map[df_map['zona_cresta'] == strip_count(fzc)]
        if strip_count(fh2): df_map = df_map[df_map['hidro2'] == strip_count(fh2)]
        if search.strip():
            terms = search.lower().split()
            df_map = df_map[df_map['_blob'].apply(lambda b: all(t in b for t in terms))]

        st.caption(f"Mostrando **{len(df_map)}** puntos")

        if len(df_map) > 0:
            m = folium.Map(location=[df_map['lat_f'].mean(), df_map['lng_f'].mean()],
                           zoom_start=5, tiles='CartoDB positron')
            for _, row in df_map.iterrows():
                obs = str(row.get('observacion', ''))
                color = 'green' if obs.startswith('OK') else 'orange' if 'CONFLICTO' in obs else 'red'
                nom = str(row[nom_col]) if nom_col and nom_col in row else ''
                dir_ = str(row[dir_col]) if dir_col and dir_col in row else ''
                popup_html = f"""
                <div style="font-family:sans-serif;min-width:200px">
                  <b style="font-size:14px;color:{SUMMA_AZUL}">{html.escape(nom) or 'Sin nombre'}</b><br>
                  <span style="color:#666;font-size:12px">{html.escape(dir_)}</span><hr style="margin:6px 0">
                  <b>CP:</b> {row.get('cp_geo','')}<br>
                  <b>Estado:</b> {row.get('estado_geo','')}<br>
                  <b>Municipio:</b> {row.get('municipio_geo','')}<br>
                  <hr style="margin:6px 0">
                  <b>Zona Sismica:</b> {row.get('zona_sismica','')}<br>
                  <b>Zona Cresta:</b> {row.get('zona_cresta','')}<br>
                  <b>Hidro2:</b> {row.get('hidro2','')}<br>
                  <hr style="margin:6px 0">
                  <span style="font-size:11px;color:#888">Metodo: {row.get('metodo','')}</span><br>
                  <span style="font-size:11px;color:#888">{html.escape(obs[:80])}</span>
                </div>"""
                folium.Marker(location=[row['lat_f'], row['lng_f']],
                              popup=folium.Popup(popup_html, max_width=280),
                              tooltip=nom or f"Fila {row.name+1}",
                              icon=folium.Icon(color=color, icon='home', prefix='fa')).add_to(m)
            st_folium(m, width=None, height=550, returned_objects=[])
        else:
            st.warning("No hay puntos con coordenadas para mostrar con los filtros actuales.")

    with tab2:
        df_show = df_result.drop(columns=['_blob'])
        st.dataframe(df_show, use_container_width=True, height=400)
        cdl1, cdl2 = st.columns(2)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine='openpyxl') as writer:
            df_show.to_excel(writer, index=False, sheet_name='Geocodificado')
        buf.seek(0)
        cdl1.download_button("Descargar Excel enriquecido", buf,
            file_name="resultado_geocodificado.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True, type="primary")
        kml_bytes = build_kml(df_result, nom_col, dir_col)
        cdl2.download_button("Descargar KML (Google My Maps)", kml_bytes,
            file_name="ubicaciones_summa.kml",
            mime="application/vnd.google-earth.kml+xml", use_container_width=True)
        st.caption("El KML se colorea por Zona Sismica. Importalo en mymaps.google.com -> Crear mapa -> Importar.")

    with tab3:
        st.markdown("### Resumen por zonas y estados")
        total = len(df_result)
        con_coords = (df_result['lat_geo'] != '').sum()
        conflictos = df_result['observacion'].astype(str).str.contains('CONFLICTO').sum()
        sin_datos = df_result['observacion'].astype(str).str.contains('Sin datos').sum()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total ubicaciones", total)
        m2.metric("Con coordenadas", int(con_coords))
        m3.metric("Conflictos", int(conflictos))
        m4.metric("Sin datos", int(sin_datos))
        st.markdown("---")

        def bar(col, titulo):
            d = df_result[df_result[col].astype(str).str.strip() != '']
            if len(d) == 0:
                st.info(f"Sin datos para {titulo}")
                return
            vc = d[col].value_counts().reset_index()
            vc.columns = [titulo, 'Ubicaciones']
            fig = px.bar(vc, x=titulo, y='Ubicaciones', text='Ubicaciones',
                         color_discrete_sequence=[SUMMA_AZUL])
            fig.update_traces(textposition='outside')
            fig.update_layout(height=320, margin=dict(t=30, b=10, l=10, r=10), plot_bgcolor='white')
            st.plotly_chart(fig, use_container_width=True)

        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Por Zona Sismica**"); bar('zona_sismica', 'Zona Sismica')
        with g2:
            st.markdown("**Por Zona Cresta**"); bar('zona_cresta', 'Zona Cresta')
        g3, g4 = st.columns(2)
        with g3:
            st.markdown("**Por Hidro2**"); bar('hidro2', 'Hidro2')
        with g4:
            st.markdown("**Por Estado**"); bar('estado_geo', 'Estado')