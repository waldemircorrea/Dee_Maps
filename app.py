"""
Aplicativo Streamlit para mapear fotos por coordenadas EXIF.

Funcionalidades:
- Lê fotos de uma pasta raiz (ex.: OneDrive local sincronizado).
- Extrai latitude/longitude do EXIF e plota pins em mapa satélite.
- Filtra por subpastas (cada pasta pode ser uma semana).
- Popup com pré-visualização; clique na miniatura abre imagem em nova aba.
"""

from __future__ import annotations

import base64
import io
import json
import os
from urllib.parse import urlparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st
from PIL import ExifTags, Image

try:
    import folium
    from streamlit_folium import st_folium
except Exception:  # pragma: no cover
    folium = None
    st_folium = None


CONFIG_FILE = Path(__file__).parent / "config.json"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
GPS_IFD_TAG = 34853  # EXIF GPSInfo


@dataclass
class FotoGeo:
    arquivo: Path
    pasta_semana: str
    latitude: float
    longitude: float
    data_foto: Optional[str]


def carregar_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def salvar_config(config: Dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def selecionar_pasta_dialog(caminho_inicial: str = "") -> Optional[str]:
    """
    Abre um seletor de pasta nativo (Windows/Linux/Mac) via tkinter.
    Retorna o caminho selecionado ou None se cancelado.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        pasta = filedialog.askdirectory(
            title="Selecione a pasta raiz das fotos",
            initialdir=caminho_inicial if caminho_inicial else None,
        )
        root.destroy()
        return pasta if pasta else None
    except Exception:
        return None


def url_valida(url: str) -> bool:
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def dms_para_decimal(dms: Iterable[Any], ref: str) -> float:
    vals = list(dms)
    if len(vals) != 3:
        raise ValueError("Coordenada DMS inválida")

    def _to_float(v: Any) -> float:
        try:
            return float(v)
        except Exception:
            num = getattr(v, "numerator", None)
            den = getattr(v, "denominator", None)
            if num is not None and den:
                return float(num) / float(den)
            if isinstance(v, tuple) and len(v) == 2 and v[1]:
                return float(v[0]) / float(v[1])
            raise

    graus = _to_float(vals[0])
    minutos = _to_float(vals[1])
    segundos = _to_float(vals[2])
    decimal = graus + minutos / 60.0 + segundos / 3600.0
    if ref.upper() in {"S", "W"}:
        decimal *= -1
    return decimal


def extrair_gps_e_data(caminho: Path) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    try:
        with Image.open(caminho) as img:
            exif = img.getexif()
            if not exif:
                return None, None, None

            data_foto = exif.get(306) or exif.get(36867)  # DateTime / DateTimeOriginal
            gps_raw = exif.get(GPS_IFD_TAG)
            if not gps_raw:
                return None, None, data_foto

            gps_info = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_raw.items()}

            lat = gps_info.get("GPSLatitude")
            lat_ref = gps_info.get("GPSLatitudeRef")
            lon = gps_info.get("GPSLongitude")
            lon_ref = gps_info.get("GPSLongitudeRef")

            if not (lat and lat_ref and lon and lon_ref):
                return None, None, data_foto

            latitude = dms_para_decimal(lat, str(lat_ref))
            longitude = dms_para_decimal(lon, str(lon_ref))
            return latitude, longitude, str(data_foto) if data_foto else None
    except Exception:
        return None, None, None


def listar_subpastas_com_fotos(raiz: Path) -> List[Path]:
    if not raiz.exists() or not raiz.is_dir():
        return []

    subpastas: List[Path] = []
    for p in sorted([x for x in raiz.iterdir() if x.is_dir()]):
        if any(arq.suffix.lower() in IMAGE_EXTENSIONS for arq in p.iterdir() if arq.is_file()):
            subpastas.append(p)
    return subpastas


def coletar_fotos_georreferenciadas(raiz: Path, pasta_filtro: str) -> List[FotoGeo]:
    resultados: List[FotoGeo] = []
    subpastas = listar_subpastas_com_fotos(raiz)

    for pasta in subpastas:
        if pasta_filtro != "Todas as fotos" and pasta.name != pasta_filtro:
            continue

        for arq in sorted(pasta.iterdir()):
            if not arq.is_file() or arq.suffix.lower() not in IMAGE_EXTENSIONS:
                continue

            lat, lon, data_foto = extrair_gps_e_data(arq)
            if lat is None or lon is None:
                continue

            resultados.append(
                FotoGeo(
                    arquivo=arq,
                    pasta_semana=pasta.name,
                    latitude=lat,
                    longitude=lon,
                    data_foto=data_foto,
                )
            )

    return resultados


def imagem_para_base64(caminho: Path, largura_max: int = 1280, qualidade: int = 88) -> str:
    with Image.open(caminho) as img:
        img = img.convert("RGB")
        img.thumbnail((largura_max, largura_max))
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=qualidade, optimize=True)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")


def popup_html(foto: FotoGeo) -> str:
    b64_preview = imagem_para_base64(foto.arquivo, largura_max=360, qualidade=80)
    b64_full = imagem_para_base64(foto.arquivo, largura_max=1800, qualidade=90)
    nome = foto.arquivo.name
    data_txt = foto.data_foto or "Sem data EXIF"
    pasta = foto.pasta_semana

    return f"""
    <div style="width: 220px; font-family: Arial, sans-serif;">
      <div style="font-weight: 700; margin-bottom: 6px;">{nome}</div>
      <div style="font-size: 12px; color: #555; margin-bottom: 6px;">Pasta: {pasta}</div>
      <div style="font-size: 12px; color: #555; margin-bottom: 8px;">Data: {data_txt}</div>
      <a href="data:image/jpeg;base64,{b64_full}" target="_blank" title="Abrir em janela inteira">
        <img src="data:image/jpeg;base64,{b64_preview}" style="width: 200px; border-radius: 6px;" />
      </a>
      <div style="font-size: 11px; color: #666; margin-top: 6px;">
        Clique na imagem para abrir em janela inteira.
      </div>
    </div>
    """


def main() -> None:
    st.set_page_config(page_title="Mapa de Fotos por EXIF", page_icon="🗺️", layout="wide")
    st.title("🗺️ Mapa de Fotos por Coordenadas EXIF")
    st.caption(
        "Mostra fotos de pastas (semanas), lê GPS EXIF e plota em mapa satélite com pré-visualização."
    )

    if folium is None or st_folium is None:
        st.error(
            "Dependências ausentes. Instale com: pip install folium streamlit-folium Pillow"
        )
        st.stop()

    cfg = carregar_config()
    default_onedrive = os.environ.get("OneDrive", "")
    caminho_padrao = cfg.get("pasta_raiz_fotos") or default_onedrive
    onedrive_web_padrao = cfg.get("onedrive_web_url", "")

    if "pasta_raiz_input" not in st.session_state:
        st.session_state["pasta_raiz_input"] = caminho_padrao

    st.sidebar.header("⚙️ Fonte das Fotos")
    pasta_raiz_txt = st.sidebar.text_input(
        "Pasta raiz (OneDrive local ou outra pasta)",
        key="pasta_raiz_input",
        help="Ex.: C:/Users/SEU_USUARIO/OneDrive/Inspecoes",
    )

    col_sel, col_save = st.sidebar.columns(2)
    with col_sel:
        if st.button("📂 Selecionar pasta"):
            selecionada = selecionar_pasta_dialog(pasta_raiz_txt)
            if selecionada:
                st.session_state["pasta_raiz_input"] = selecionada
                st.rerun()
    with col_save:
        if st.button("💾 Salvar pasta"):
            salvar_config(
                {
                    "pasta_raiz_fotos": st.session_state.get("pasta_raiz_input", ""),
                    "onedrive_web_url": onedrive_web_padrao,
                }
            )
            st.sidebar.success("Caminho salvo no config.json.")

    if pasta_raiz_txt.strip():
        pasta_uri = Path(pasta_raiz_txt.strip()).as_uri()
        st.sidebar.markdown(f"[📁 Abrir pasta atual]({pasta_uri})")

    st.sidebar.markdown("---")
    st.sidebar.subheader("🌐 OneDrive Web")
    onedrive_web_url = st.sidebar.text_input(
        "Link da pasta no OneDrive Web (opcional)",
        value=onedrive_web_padrao,
        help="Ex.: https://onedrive.live.com/... ou link de compartilhamento da pasta",
    )

    col_web1, col_web2 = st.sidebar.columns(2)
    with col_web1:
        if onedrive_web_url.strip() and url_valida(onedrive_web_url):
            st.link_button("🔗 Abrir link", onedrive_web_url)
    with col_web2:
        if st.button("💾 Salvar link"):
            salvar_config(
                {
                    "pasta_raiz_fotos": st.session_state.get("pasta_raiz_input", ""),
                    "onedrive_web_url": onedrive_web_url.strip(),
                }
            )
            st.sidebar.success("Link salvo no config.json.")

    if st.sidebar.button("💾 Salvar caminho padrão"):
        salvar_config(
            {
                "pasta_raiz_fotos": st.session_state.get("pasta_raiz_input", ""),
                "onedrive_web_url": onedrive_web_url.strip(),
            }
        )
        st.sidebar.success("Caminho salvo no config.json.")

    pasta_raiz = Path(pasta_raiz_txt.strip()) if pasta_raiz_txt.strip() else None
    if not pasta_raiz or not pasta_raiz.exists() or not pasta_raiz.is_dir():
        st.warning("Informe uma pasta raiz válida para carregar as fotos.")
        st.info(
            "Sugestão OneDrive: use a pasta local sincronizada pelo aplicativo OneDrive no Windows."
        )
        if onedrive_web_url.strip():
            st.caption(
                "Link OneDrive Web configurado. "
                "Observação: para leitura automática de EXIF via web, seria necessário integrar Microsoft Graph API."
            )
        st.stop()

    subpastas = listar_subpastas_com_fotos(pasta_raiz)
    opcoes_filtro = ["Todas as fotos"] + [p.name for p in subpastas]

    filtro = st.sidebar.selectbox(
        "📁 Filtrar por semana/pasta",
        options=opcoes_filtro,
        index=0,
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Dica: organize cada semana em uma subpasta. "
        "A opção 'Todas as fotos' consolida tudo."
    )

    with st.spinner("Lendo fotos e extraindo coordenadas EXIF..."):
        fotos = coletar_fotos_georreferenciadas(pasta_raiz, filtro)

    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("Pins georreferenciados", len(fotos))
    with col_b:
        st.metric("Filtro atual", filtro)

    if not fotos:
        st.warning(
            "Nenhuma foto com coordenada GPS EXIF encontrada para o filtro atual."
        )
        st.stop()

    lat_media = sum(f.latitude for f in fotos) / len(fotos)
    lon_media = sum(f.longitude for f in fotos) / len(fotos)

    mapa = folium.Map(
        location=[lat_media, lon_media],
        zoom_start=14,
        control_scale=True,
        tiles=None,
    )

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satélite",
        overlay=False,
        control=True,
    ).add_to(mapa)

    folium.TileLayer(
        tiles="OpenStreetMap",
        name="Mapa",
        overlay=False,
        control=True,
    ).add_to(mapa)

    for foto in fotos:
        popup = folium.Popup(popup_html(foto), max_width=280)
        tooltip = f"{foto.pasta_semana} | {foto.arquivo.name}"
        folium.Marker(
            location=[foto.latitude, foto.longitude],
            popup=popup,
            tooltip=tooltip,
            icon=folium.Icon(color="blue", icon="camera", prefix="fa"),
        ).add_to(mapa)

    folium.LayerControl(collapsed=False).add_to(mapa)

    st_folium(
        mapa,
        width=None,
        height=680,
        returned_objects=[],
    )

    with st.expander("📋 Tabela das fotos georreferenciadas"):
        df = pd.DataFrame(
            [
                {
                    "pasta_semana": f.pasta_semana,
                    "arquivo": f.arquivo.name,
                    "latitude": f.latitude,
                    "longitude": f.longitude,
                    "data_exif": f.data_foto or "",
                    "caminho": str(f.arquivo),
                }
                for f in fotos
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
