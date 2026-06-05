# 🗺️ Geolocalizador de Ubicaciones México

App web para geocodificar ubicaciones en México, obtener zonas sísmicas, cresta e hidrometeorológicas.

## 🚀 Despliegue en Streamlit Cloud (5 minutos)

### 1. Sube el repositorio a GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/TU_USUARIO/geolocalizador-ubicaciones-mexico.git
git push -u origin main
```

### 2. Despliega en Streamlit Cloud
1. Ve a [share.streamlit.io](https://share.streamlit.io)
2. Inicia sesión con tu cuenta de GitHub
3. Clic en **"New app"**
4. Selecciona tu repositorio y rama `main`
5. App file: `app.py`
6. Clic en **"Deploy"**

En ~2 minutos tendrás tu URL pública: `https://tu-usuario-geolocalizador-ubicaciones-mexico.streamlit.app`

---

## 📁 Estructura del proyecto

```
geocodificador/
├── app.py                    # App principal Streamlit
├── requirements.txt          # Dependencias Python
├── .streamlit/
│   └── config.toml          # Tema y configuración
├── data/
│   ├── cp_lookup.json.gz    # 36,182 CPs con coords y zonas
│   └── kml_zones.pkl.gz     # Polígonos KML para fallback
└── utils/
    ├── __init__.py
    ├── data_loader.py        # Carga de datos de referencia
    └── geocoder.py           # Lógica de geocodificación
```

---

## 🔄 Estrategia de geocodificación

Para cada fila del Excel, el sistema intenta en orden:

| Paso | Método | Fuente |
|------|--------|--------|
| 1️⃣ | Coordenadas existentes → geocodificación inversa | Nominatim |
| 2️⃣ | CP → tabla de referencia | CP-MEX-2025 |
| 3️⃣ | Solo CP → búsqueda | Nominatim |
| 4️⃣ | Nombre + Ciudad + Estado | Nominatim |
| 5️⃣ | Ciudad + Estado | Nominatim |
| 6️⃣ | Coords → punto en polígono | KML INEGI |

---

## 📊 Columnas de salida

El Excel descargado incluye las columnas originales más:

| Columna | Descripción |
|---------|-------------|
| `lat_geo` | Latitud geocodificada |
| `lng_geo` | Longitud geocodificada |
| `estado_geo` | Estado según geocodificación |
| `municipio_geo` | Municipio según geocodificación |
| `cp_geo` | CP encontrado |
| `zona_sismica` | Zona sísmica (A-J) |
| `zona_cresta` | Zona cresta (1-47) |
| `hidro2` | Zona hidrometeorológica |
| `metodo` | Qué paso encontró el resultado |
| `observacion` | OK / CONFLICTO / Sin datos |

---

## 🗺️ Funcionalidades del mapa

- **Verde:** registros OK
- **Naranja:** registros con conflicto (datos inconsistentes)
- **Rojo:** registros sin geocodificación
- **Popup:** click en cada pin para ver todos los datos
- **Filtros:** por Zona Sísmica, Zona Cresta e Hidro2
