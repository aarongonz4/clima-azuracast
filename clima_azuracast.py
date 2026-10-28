#!/usr/bin/env python3
"""
Spot automático de clima y noticias para AzuraCast (Nogaradio) - VERSION V9 DUO IMAGING (Estilo Urbana Play / La 100).

Arquitectura de 4 bloques vocales independientes (Cero cortes fallidos):
  1) TAG INICIAL (Voz A con FX FM: Reverse Swell + Reverb Low / Slap Delay).
  2) CUERPO CLIMA (Voz A 100% SECA, sin eco ni reverb durante la lectura).
  3) GRAN TRANSICIÓN FM NOTORIA -> CUERPO NOTICIAS (Voz B 100% SECA, clara y al frente).
  4) TRANSICIÓN PASE HOMBRE/MUJER -> REMATE FINAL MÍTICO (Vuelve Voz A con Reverb Low + Eco Ping-Pong / Slow FM)
     mientras la canción de fondo baja despacito en un fade-out limpio de 3 segundos.
"""

import glob
import io
import os
import random
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import numpy as np
import paramiko
import requests
from google.cloud import texttospeech
from pydub import AudioSegment

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

CIUDAD = "Resistencia"
LAT = -27.4514
LON = -58.9867
ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")

LOCAL_MP3 = "clima.mp3"
REMOTE_FILENAME = "clima.mp3"

FEEDS_MUNDO = [
    "https://feeds.bbci.co.uk/mundo/rss.xml",
    "https://www.infobae.com/arc/outboundfeeds/rss/",
]
FEEDS_MUSICA = [
    "https://www.planeta.pe/noticias/musica/feed.xml",
    "https://indiehoy.com/musica/feed/",
]

ASSETS_DIR = "assets"
EXTENSIONES = (".mp3", ".wav", ".ogg", ".m4a", ".flac")
SR = 44100

# Niveles de mezcla en dB
REFERENCIA_RMS_DB = -18.5
EFECTOS_DB = -0.5          # Intros y Transiciones bien al frente y notorias
AMBIENTE_DB = -8.5         # Cortina de clima presente debajo de la voz
TICKER_NOTICIAS_DB = -8.0  # Cortina de noticias bien audible
CAMA_DB_LIBRE = -4.5
CAMA_DB_VOZ = -10.5

# ---------------------------------------------------------------------------
# Diccionarios y Funciones de Tiempo
# ---------------------------------------------------------------------------

CODIGOS_CLIMA = {
    0: "cielo despejado", 1: "mayormente despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "neblina", 48: "neblina con escarcha", 51: "llovizna débil", 53: "llovizna moderada",
    55: "llovizna intensa", 61: "lluvia débil", 63: "lluvia moderada", 65: "lluvia intensa",
    71: "nevada débil", 73: "nevada moderada", 75: "nevada intensa", 80: "chubascos débiles",
    81: "chubascos moderados", 82: "chubascos fuertes", 95: "tormenta eléctrica",
    96: "tormenta con granizo", 99: "tormenta fuerte con granizo",
}

NUMEROS = {
    0: "cero", 1: "una", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco",
    6: "seis", 7: "siete", 8: "ocho", 9: "nueve", 10: "diez",
    11: "once", 12: "doce", 13: "trece", 14: "catorce", 15: "quince",
    16: "dieciséis", 17: "diecisiete", 18: "dieciocho", 19: "diecinueve",
    20: "veinte", 21: "veintiuno", 22: "veintidós", 23: "veintitrés",
    24: "veinticuatro", 25: "veinticinco", 26: "veintiséis", 27: "veintisiete",
    28: "veintiocho", 29: "veintinueve", 30: "treinta", 31: "treinta y uno",
    32: "treinta y dos", 33: "treinta y tres", 34: "treinta y cuatro",
    35: "treinta y cinco", 36: "treinta y seis", 37: "treinta y siete",
    38: "treinta y ocho", 39: "treinta y nueve", 40: "cuarenta",
    41: "cuarenta y uno", 42: "cuarenta y dos", 43: "cuarenta y tres",
    44: "cuarenta y cuatro", 45: "cuarenta y cinco", 46: "cuarenta y seis",
    47: "cuarenta y siete", 48: "cuarenta y ocho", 49: "cuarenta y nueve",
    50: "cincuenta", 51: "cincuenta y uno", 52: "cincuenta y dos",
    53: "cincuenta y tres", 54: "cincuenta y cuatro", 55: "cincuenta y cinco",
    56: "cincuenta y seis", 57: "cincuenta y siete", 58: "cincuenta y ocho",
    59: "cincuenta y nueve",
}


def hora_en_espanol(hora: int, minuto: int) -> str:
    hora_12 = hora % 12
    if hora_12 == 0:
        hora_12 = 12
    texto_hora = NUMEROS[hora_12]
    if minuto == 0:
        return f"{texto_hora} en punto"
    return f"{texto_hora} con {NUMEROS[minuto]} minutos"


def saludo_segun_hora(hora_actual: int) -> str:
    if 6 <= hora_actual < 12:
        return random.choice([
            "Hola, muy buenos días",
            "Buenos días, qué bueno que estés ahí",
            "Arriba esa mañana, muy buenos días",
            "Excelente mañana para vos",
        ])
    elif 12 <= hora_actual < 20:
        return random.choice([
            "Hola, muy buenas tardes",
            "Buenas tardes, seguimos haciéndote el aguante",
            "Muy buenas tardes, qué lindo que estés del otro lado",
            "Excelente tarde para vos",
        ])
    return random.choice([
        "Hola, muy buenas noches",
        "Buenas noches, gracias por estar del otro lado",
        "Muy buenas noches, seguimos acompañándote",
        "Excelente noche para vos",
    ])


# ---------------------------------------------------------------------------
# Guiones Míticos FM (Separados en Apertura, Clima, Noticias y Remate Dúo)
# ---------------------------------------------------------------------------

TAGS_APERTURA_FM = [
    "Escuchás, Nogaradio.",
    "Conectados, en Nogaradio.",
    "Suena, Nogaradio.",
    "Estás en Nogaradio, tu radio.",
    "La radio que te mueve. Nogaradio.",
    "Siempre en tu sintonía. Nogaradio.",
]

FRASES_PASE_NOTICIAS = [
    "No te muevas, ya llegan los titulares.",
    "Quedate ahí, vamos directo al flash informativo.",
    "Seguí con nosotros, actualizamos las noticias.",
    "Subí el volumen, ya llega toda la información.",
    "Ahora sí, pasamos a las noticias de esta hora.",
]

PLANTILLAS_CLIMA = [
    "{saludo}. En Argentina son las {hora}. Así está el tiempo en {ciudad}: tenemos {descripcion}, con {temp} grados y una sensación térmica de {sensacion}. La humedad es del {humedad} por ciento. {pase}",
    "{saludo}. Pasaron las {hora} en todo el país. Actualizamos el clima en {ciudad}: {descripcion}, {temp} grados de temperatura y {sensacion} de térmica. Humedad del {humedad} por ciento y viento de {viento} kilómetros por hora. {pase}",
    "{saludo}. Son las {hora} en Argentina. Repasamos el estado del tiempo para {ciudad}: cielo con {descripcion}, temperatura actual de {temp} grados y térmica de {sensacion}. La humedad, del {humedad} por ciento. {pase}",
    "{saludo}. Cuando pasan las {hora}, miramos cómo está el tiempo en {ciudad}: tenemos {descripcion}, {temp} grados de temperatura, térmica de {sensacion} y vientos de {viento} kilómetros por hora. {pase}",
]

# Pie que deja picando el Locutor 2 al terminar las noticias, antes de que entre el Locutor 1 al remate
PIES_PRE_CIERRE_NOTICIAS = [
    "Ya estás al día con toda la información. Subí el volumen,",
    "Hasta acá el flash informativo de esta hora. No te muevas del dial,",
    "Así actualizamos los titulares del momento. Quedate del otro lado,",
    "Toda la actualidad al instante en tu radio. Seguí con nosotros,",
    "Ya estás bien informado. Ahora sigue sonando la mejor música,",
]

# Frases míticas finales que dice el otro locutor con efecto Reverb Low / Eco Ping-Pong / Slow
REMATES_FINALES_MITICOS = [
    "porque en Nogaradio, seguimos con vos.",
    "escuchás Nogaradio, veinticuatro siete sonando para vos.",
    "en Nogaradio, estamos siempre con vos.",
    "esto es Nogaradio, conectados con vos.",
    "viví la música en Nogaradio, siempre junto a vos.",
    "Nogaradio, la radio que suena para vos.",
]


def obtener_clima(reintentos: int = 3, espera: int = 5) -> dict | None:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT, "longitude": LON,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
        "timezone": "America/Argentina/Buenos_Aires",
    }
    for intento in range(1, reintentos + 1):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            print(f"Intento {intento}/{reintentos} falló al consultar Open-Meteo: {e}", file=sys.stderr)
            if intento < reintentos:
                time.sleep(espera)
    return None


def armar_textos_clima(data: dict | None) -> tuple[str, str]:
    """Devuelve (tag_apertura, cuerpo_clima_seco)."""
    ahora = datetime.now(ZONA_HORARIA)
    hora_str = hora_en_espanol(ahora.hour, ahora.minute)
    saludo = saludo_segun_hora(ahora.hour)
    pase = random.choice(FRASES_PASE_NOTICIAS)
    tag_ini = random.choice(TAGS_APERTURA_FM)

    if not data or "current" not in data:
        cuerpo = f"{saludo}. En Argentina son las {hora_str}. Te acompañamos en cada momento con la mejor música. {pase}"
        return tag_ini, cuerpo

    actual = data["current"]
    temp = round(actual["temperature_2m"])
    sensacion = round(actual["apparent_temperature"])
    humedad = round(actual["relative_humidity_2m"])
    viento = round(actual["wind_speed_10m"])
    descripcion = CODIGOS_CLIMA.get(actual["weather_code"], "condiciones variables")

    plantilla = random.choice(PLANTILLAS_CLIMA)
    cuerpo = plantilla.format(
        saludo=saludo, hora=hora_str, ciudad=CIUDAD, descripcion=descripcion,
        temp=temp, sensacion=sensacion, humedad=humedad, viento=viento, pase=pase
    )
    return tag_ini, cuerpo


def elegir_ambiente(data: dict | None) -> str:
    hora = datetime.now(ZONA_HORARIA).hour
    es_dia = 6 <= hora < 20

    if not data or "current" not in data:
        return "dia" if es_dia else "noche"

    actual = data["current"]
    codigo = actual.get("weather_code", 0)
    temp = actual.get("temperature_2m", 24)
    viento = actual.get("wind_speed_10m", 0)

    if codigo in (95, 96, 99):
        return "tormenta"
    if codigo in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "lluvia"
    if codigo in (3, 45, 48, 71, 73, 75) or (viento and viento >= 25):
        return "viento"
    if not es_dia:
        return "noche"
    if temp is not None and temp >= 30:
        return "calor"
    return "dia"


# ---------------------------------------------------------------------------
# Verificador Inteligente de Noticias RSS
# ---------------------------------------------------------------------------

def limpiar_texto_rss(texto: str) -> str:
    if not texto:
        return ""
    texto_limpio = re.sub(r'<[^>]+>', ' ', texto)
    reemplazos = {
        "&nbsp;": " ", "&amp;": " y ", "&quot;": "", "&#8220;": "",
        "&#8221;": "", "&#8216;": "", "&#8217;": "", "&#039;": "",
        "Leer más": "", "Sigue leyendo": "", "[…]": "", "...": ".",
    }
    for k, v in reemplazos.items():
        texto_limpio = texto_limpio.replace(k, v)
    return " ".join(texto_limpio.strip().split())


def normalizar_comparacion(texto: str) -> str:
    return re.sub(r'[^a-záéíóúñ0-9]', '', texto.lower())


def construir_noticia_limpia(titulo: str, descripcion: str) -> str:
    titulo = titulo.strip().rstrip(".")
    if not descripcion:
        return f"{titulo}."

    oraciones = re.split(r'(?<=[.!?])\s+', descripcion)
    primera_oracion = oraciones[0].strip().rstrip(".")

    norm_tit = normalizar_comparacion(titulo)
    norm_desc = normalizar_comparacion(primera_oracion)

    if not norm_desc or norm_tit in norm_desc or norm_desc in norm_tit:
        return f"{titulo}."
    if len(norm_tit) > 15 and norm_desc[:18] == norm_tit[:18]:
        return f"{titulo}."
    if len(primera_oracion) > 190:
        return f"{titulo}."

    return f"{titulo}. {primera_oracion}."


def obtener_titular_verificado(lista_feeds: list[str], cantidad: int = 20) -> str | None:
    entradas_unicas = []
    vistos = set()

    for url_base in lista_feeds:
        try:
            sep = "&" if "?" in url_base else "?"
            url_fresca = f"{url_base}{sep}_t={int(time.time() // 120)}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Accept-Language": "es-AR,es;q=0.9",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            }
            resp = requests.get(url_fresca, headers=headers, timeout=12)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries[:cantidad]:
                titulo = limpiar_texto_rss(getattr(entry, "title", ""))
                descripcion = limpiar_texto_rss(getattr(entry, "description", "") or getattr(entry, "summary", ""))

                if len(titulo) < 18:
                    continue
                clave = normalizar_comparacion(titulo)[:35]
                if clave in vistos:
                    continue
                vistos.add(clave)

                noticia = construir_noticia_limpia(titulo, descripcion)
                entradas_unicas.append(noticia)

            if len(entradas_unicas) >= 6:
                break
        except Exception as e:
            print(f"Aviso: No se pudo leer {url_base}: {e}", file=sys.stderr)

    if not entradas_unicas:
        return None

    candidatos_top = entradas_unicas[: min(12, len(entradas_unicas))]
    elegida = random.choice(candidatos_top)
    print(f"Noticia seleccionada ({len(entradas_unicas)} disponibles): {elegida[:80]}...")
    return elegida


def armar_textos_noticias_y_remate(titular_mundo: str | None, titular_musica: str | None) -> tuple[str, str]:
    """Devuelve (cuerpo_noticias_seco, frase_remate_final_otro_locutor)."""
    pie = random.choice(PIES_PRE_CIERRE_NOTICIAS)
    remate = random.choice(REMATES_FINALES_MITICOS)
    intro_mundo = random.choice(["Flash internacional:", "Actualidad internacional:", "En el plano internacional:"])
    intro_musica = random.choice(["Y en la música:", "Pasando a la música:", "En novedades musicales:"])

    if titular_mundo and titular_musica:
        cuerpo = f"{intro_mundo} {titular_mundo} {intro_musica} {titular_musica} {pie}"
    elif titular_mundo:
        cuerpo = f"{intro_mundo} {titular_mundo} {pie}"
    elif titular_musica:
        cuerpo = f"{intro_musica} {titular_musica} {pie}"
    else:
        cuerpo = pie
    return cuerpo, remate


# ---------------------------------------------------------------------------
# Motor DSP + Efectos Vocales Estilo Urbana Play / La 100 (Reverb Low, Eco, Slow)
# ---------------------------------------------------------------------------

def seg_a_array(seg: AudioSegment) -> np.ndarray:
    seg = seg.set_frame_rate(SR).set_channels(2).set_sample_width(2)
    a = np.array(seg.get_array_of_samples(), dtype=np.float64)
    return a.reshape(-1, 2) / 32768.0


def array_a_seg(x: np.ndarray) -> AudioSegment:
    x = np.clip(x, -1.0, 1.0)
    datos = (x * 32767).astype(np.int16).tobytes()
    return AudioSegment(data=datos, sample_width=2, frame_rate=SR, channels=2)


def _est(mono: np.ndarray) -> np.ndarray:
    return np.column_stack((mono, mono))


def _t(dur_ms: float) -> np.ndarray:
    return np.arange(int(SR * dur_ms / 1000)) / SR


def _dur_ms(x: np.ndarray) -> float:
    return len(x) * 1000.0 / SR


def _g(db: float) -> float:
    return 10 ** (db / 20.0)


def _g_array(db: np.ndarray) -> np.ndarray:
    return 10 ** (db / 20.0)


def _pico(x: np.ndarray, objetivo: float = 0.9) -> np.ndarray:
    p = np.max(np.abs(x))
    return x * (objetivo / p) if p > 0 else x


def _rms(x: np.ndarray, db: float) -> np.ndarray:
    r = np.sqrt(np.mean(x ** 2))
    return x * (_g(db) / r) if r > 0 else x


def _fade(x: np.ndarray, entrada_ms: float, salida_ms: float) -> np.ndarray:
    x = x.copy()
    n = len(x)
    a = min(int(SR * entrada_ms / 1000), n)
    b = min(int(SR * salida_ms / 1000), n)
    if a > 0:
        x[:a] *= np.linspace(0, 1, a)[:, None]
    if b > 0:
        x[-b:] *= np.linspace(1, 0, b)[:, None]
    return x


def _pista(dur_ms: float) -> np.ndarray:
    return np.zeros((int(SR * dur_ms / 1000), 2))


def _sumar(destino: np.ndarray, x: np.ndarray, inicio_ms: float, ganancia: float = 1.0) -> None:
    i = max(0, int(SR * inicio_ms / 1000))
    fin = min(len(destino), i + len(x))
    if fin > i:
        destino[i:fin] += x[: fin - i] * ganancia


def _filtrar(x: np.ndarray, pasa_altos: float | None = None, pasa_bajos: float | None = None, orden: int = 2) -> np.ndarray:
    n = x.shape[0]
    if n == 0:
        return x
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    mascara = np.ones_like(f)
    if pasa_bajos:
        mascara *= 1.0 / (1.0 + (f / pasa_bajos) ** (2 * orden))
    if pasa_altos:
        h = (f / np.maximum(pasa_altos, 1e-3)) ** (2 * orden)
        mascara *= h / (1.0 + h)
    return np.fft.irfft(X * mascara[:, None], n=n, axis=0)


def _hueco_vocal_cortina(x: np.ndarray) -> np.ndarray:
    medios_voz = _filtrar(x, pasa_altos=1400, pasa_bajos=3000, orden=2)
    return x - medios_voz * 0.25


def _comprimir(x: np.ndarray, thr_db: float = -20.0, ratio: float = 3.0,
               ataque_ms: float = 6.0, relajo_ms: float = 120.0, bloque_ms: float = 5.0) -> np.ndarray:
    n = x.shape[0]
    if n == 0:
        return x
    blk = max(1, int(SR * bloque_ms / 1000))
    nb = n // blk + 1
    pico = np.pad(np.abs(x).max(axis=1), (0, nb * blk - n)).reshape(nb, blk).max(axis=1)
    db = 20 * np.log10(np.maximum(pico, 1e-6))
    objetivo = -np.maximum(db - thr_db, 0.0) * (1.0 - 1.0 / ratio)

    a = np.exp(-bloque_ms / ataque_ms)
    r = np.exp(-bloque_ms / relajo_ms)
    suave = np.zeros(nb)
    g = 0.0
    for i in range(nb):
        coef = a if objetivo[i] < g else r
        g = coef * g + (1.0 - coef) * objetivo[i]
        suave[i] = g

    centros = (np.arange(nb) + 0.5) * blk
    ganancia = np.interp(np.arange(n), centros, _g_array(suave))
    return x * ganancia[:, None]


def _recortar_silencio(x: np.ndarray, umbral: float = 0.018) -> np.ndarray:
    env = np.abs(x).max(axis=1)
    activos = np.where(env > umbral)[0]
    if len(activos) == 0:
        return x
    ini = max(0, activos[0] - int(SR * 0.020))
    fin = min(len(x), activos[-1] + int(SR * 0.035))
    return _fade(x[ini:fin], 12, 25)


def _extraer_ultima_palabra(x: np.ndarray) -> np.ndarray:
    n = len(x)
    min_muestras = int(SR * 0.32)
    max_muestras = min(int(SR * 0.70), int(n * 0.65))
    if max_muestras <= min_muestras:
        return _fade(x.copy(), 25, 35)

    tramo_busqueda = np.abs(x[n - max_muestras : n - min_muestras]).max(axis=1)
    win = max(1, int(SR * 0.012))
    kernel = np.ones(win) / win
    env_suave = np.convolve(tramo_busqueda, kernel, mode="same")

    offset_min = int(np.argmin(env_suave))
    idx_corte = (n - max_muestras) + offset_min
    trozo = x[idx_corte:].copy()
    return _fade(trozo, 25, 35)


def _estirar_pitch_slow(trozo: np.ndarray, factor: float = 1.28) -> np.ndarray:
    """Desacelera y baja el tono (Pitch-Down / Slow) con apertura estéreo Haas."""
    n_orig = len(trozo)
    n_nuevo = max(1, int(n_orig * factor))
    idx_orig = np.arange(n_orig)
    idx_nuevo = np.linspace(0, n_orig - 1, n_nuevo)
    l = np.interp(idx_nuevo, idx_orig, trozo[:, 0])
    r = np.interp(idx_nuevo, idx_orig, trozo[:, 1])

    d = int(SR * 0.015)
    r_w = np.pad(r, (d, 0))[:n_nuevo]
    out = np.column_stack((l, r_w))
    return _fade(_filtrar(out, pasa_altos=75, pasa_bajos=2800), 35, 70)


def _capa_reverb_low_fm(x: np.ndarray) -> np.ndarray:
    """
    Efecto 'Reverb Low' mítico de Urbana Play / La 100:
    Crea una voz grave sub-octavada envuelta en Reverb Hall oscura que acompaña por debajo a la voz líder.
    """
    n = len(x)
    grave_estirado = _estirar_pitch_slow(x, factor=1.22)
    # Ajustamos al largo de la frase con cola de reverb oscura
    rev_oscura = _reverb_estereo(grave_estirado, seg=1.35, mezcla=0.75, brillo=1800)
    rev_oscura = _filtrar(rev_oscura, pasa_altos=70, pasa_bajos=1600)
    return _fade(rev_oscura, 20, 250)


def _phaser_estereo_fx(x: np.ndarray) -> np.ndarray:
    t = np.arange(len(x)) / SR
    mod_l = 0.5 + 0.5 * np.sin(2 * np.pi * 1.5 * t)
    mod_r = 0.5 + 0.5 * np.cos(2 * np.pi * 1.5 * t)
    alto = _filtrar(x, pasa_altos=550, pasa_bajos=2200)
    bajo = _filtrar(x, pasa_altos=1800, pasa_bajos=4800)
    out = x * 0.55 + np.column_stack((
        alto[:, 0] * mod_l + bajo[:, 0] * (1 - mod_l),
        alto[:, 1] * mod_r + bajo[:, 1] * (1 - mod_r)
    )) * 0.60
    return out


def procesar_tag_inicio_fm(tag_crudo: np.ndarray) -> tuple[np.ndarray, float, str]:
    """
    Procesa EXCLUSIVAMENTE el Tag Mítico Inicial ('Escuchás Nogaradio', 'Conectados en Nogaradio')
    con estética de artística radial (Urbana Play / La 100) sin tocar para nada el texto del clima.
    """
    estilo = random.choice(["urbana_reverb_low_swell", "la100_reverse_pingpong", "megaphone_phaser_drop"])
    n_pre = min(int(SR * 0.38), len(tag_crudo) // 2)
    trozo_ini = _fade(tag_crudo[:n_pre].copy(), 20, 35)

    # 1. Pre-Swell invertido que anticipa la entrada
    rev_in = _filtrar(trozo_ini[::-1].copy(), pasa_altos=300, pasa_bajos=3800)
    rev_in = _reverb_estereo(rev_in, seg=0.75, mezcla=0.70, brillo=4000)[: len(rev_in)]
    curva = (np.linspace(0.0, 1.0, len(rev_in)) ** 2.1)[:, None]
    swell = _fade(_pico(rev_in * curva, 0.72), 25, 12)
    pre_ms = _dur_ms(swell)

    # 2. Procesamiento del cuerpo del Tag + Cola de Eco corta hacia el saludo del clima
    ultima_pal = _extraer_ultima_palabra(tag_crudo)
    eco_pal = _filtrar(ultima_pal, pasa_altos=380, pasa_bajos=3200)

    if estilo == "urbana_reverb_low_swell":
        # Suma la capa Reverb Low debajo del tag + 2 rebotes estéreo
        low_layer = _capa_reverb_low_fm(tag_crudo)
        largo = max(len(tag_crudo) + int(SR * 0.55), len(low_layer))
        cuerpo = _pista(largo * 1000.0 / SR)
        _sumar(cuerpo, tag_crudo, 0.0, 1.0)
        _sumar(cuerpo, low_layer, 20.0, 0.42)
        _sumar(cuerpo, eco_pal, _dur_ms(tag_crudo) - 20.0, 0.48)
        return np.vstack((swell, cuerpo)), pre_ms, "Urbana Reverb-Low + Reverse Swell"

    elif estilo == "la100_reverse_pingpong":
        # Tag brillante con doble rebote Ping-Pong L/R en 'Nogaradio'
        cuerpo = np.pad(tag_crudo, ((0, int(SR * 0.65)), (0, 0)))
        base_ms = _dur_ms(tag_crudo) - 25.0
        for rep, c in [(1, 0), (2, 1)]:
            tramo = np.zeros_like(eco_pal)
            tramo[:, c] = eco_pal[:, c] * 0.95
            tramo[:, 1 - c] = eco_pal[:, 1 - c] * 0.25
            _sumar(cuerpo, tramo, base_ms + (rep - 1) * 230.0, 0.52 ** rep)
        return np.vstack((swell, cuerpo)), pre_ms, "La 100 Reverse + Ping-Pong Tag"

    else:
        # Efecto Radio/Phaser FM en la primera mitad del tag abriendo a HD + Eco
        cuerpo = tag_crudo.copy()
        mitad = len(cuerpo) // 2
        radio = _phaser_estereo_fx(np.tanh(_filtrar(cuerpo[:mitad], pasa_altos=520, pasa_bajos=2800) * 2.2) * 0.72)
        cuerpo[:mitad] = cuerpo[:mitad] * 0.30 + radio * 0.80
        cuerpo = np.pad(cuerpo, ((0, int(SR * 0.50)), (0, 0)))
        _sumar(cuerpo, _phaser_estereo_fx(eco_pal), _dur_ms(tag_crudo) - 20.0, 0.46)
        return np.vstack((swell, cuerpo)), pre_ms, "FM Phaser Filter + Swell Tag"


def procesar_remate_final_fm(remate_crudo: np.ndarray) -> tuple[np.ndarray, str]:
    """
    Procesa la FRASE MÍTICA FINAL (dicha por el otro locutor tras el pase Hombre/Mujer).
    Como es un clip aislado, aplica Reverb Low, Eco Ping-Pong, Slow Grave y Stutter con precisión perfecta.
    """
    estilo = random.choice([
        "reverb_low_mas_pingpong",
        "pingpong_mas_slow_grave",
        "stutter_urbana_mas_eco",
        "phaser_hall_throw",
    ])

    palabra_fin = _extraer_ultima_palabra(remate_crudo)
    pal_eq = _filtrar(palabra_fin, pasa_altos=350, pasa_bajos=3400)
    pal_rev = _reverb_estereo(pal_eq, seg=1.25, mezcla=0.48, brillo=4000)[: len(pal_eq)]

    if estilo == "reverb_low_mas_pingpong":
        # El clásico de FM: la frase final sale acompañada por una sombra grave "Reverb Low"
        # y la última palabra ("con vos" / "para vos") rebota 4 veces en Ping-Pong estéreo.
        low_layer = _capa_reverb_low_fm(remate_crudo)
        cola_ms = 1650
        out = _pista(_dur_ms(remate_crudo) + cola_ms)
        _sumar(out, remate_crudo, 0.0, 1.0)
        _sumar(out, low_layer, 15.0, 0.45)

        base_ms = _dur_ms(remate_crudo) - 25.0
        for rep in range(1, 5):
            pos_ms = base_ms + (rep - 1) * 260.0
            c = rep % 2
            tramo = np.zeros_like(pal_rev)
            tramo[:, c] = pal_rev[:, c] * 0.95
            tramo[:, 1 - c] = pal_rev[:, 1 - c] * 0.28
            _sumar(out, tramo, pos_ms, 0.58 ** rep)
        return out, "Reverb Low FM + Eco Ping-Pong"

    elif estilo == "pingpong_mas_slow_grave":
        # Hace 2 rebotes Ping-Pong limpios y un 3er rebote en Slow-Motion grave profundo con Reverb Hall
        pal_slow = _estirar_pitch_slow(palabra_fin, factor=1.32)
        pal_slow = _reverb_estereo(pal_slow, seg=1.3, mezcla=0.45, brillo=2600)[: len(pal_slow)]

        cola_ms = 1750
        out = _pista(_dur_ms(remate_crudo) + cola_ms)
        _sumar(out, remate_crudo, 0.0, 1.0)

        base_ms = _dur_ms(remate_crudo) - 20.0
        for rep, c in [(1, 0), (2, 1)]:
            tramo = np.zeros_like(pal_rev)
            tramo[:, c] = pal_rev[:, c] * 0.95
            tramo[:, 1 - c] = pal_rev[:, 1 - c] * 0.25
            _sumar(out, tramo, base_ms + (rep - 1) * 255.0, 0.58 ** rep)

        # Remate Slow grave en el tercer tiempo
        _sumar(out, pal_slow, base_ms + 510.0, 0.58)
        _sumar(out, _filtrar(pal_slow, pasa_bajos=1800), base_ms + 820.0, 0.30)
        return out, "Eco Ping-Pong + Slow Grave FM"

    elif estilo == "stutter_urbana_mas_eco":
        # Stutter rítmico en el arranque del remate + Reverb Low + Cola de Eco Ping-Pong
        n_st = min(int(SR * 0.115), len(remate_crudo) // 4)
        sil = _fade(_filtrar(remate_crudo[:n_st].copy(), pasa_altos=420, pasa_bajos=3800), 10, 15) * 0.72
        s_l = np.column_stack((sil[:, 0] * 0.95, sil[:, 1] * 0.20))
        s_r = np.column_stack((sil[:, 0] * 0.20, sil[:, 1] * 0.95))

        base_voz = np.vstack((s_l, s_r, remate_crudo))
        low_layer = _capa_reverb_low_fm(remate_crudo)
        cola_ms = 1550
        out = _pista(_dur_ms(base_voz) + cola_ms)
        _sumar(out, base_voz, 0.0, 1.0)
        _sumar(out, low_layer, _dur_ms(s_l) * 2, 0.38)

        base_ms = _dur_ms(base_voz) - 25.0
        for rep in range(1, 5):
            c = rep % 2
            tramo = np.zeros_like(pal_rev)
            tramo[:, c] = pal_rev[:, c] * 0.95
            tramo[:, 1 - c] = pal_rev[:, 1 - c] * 0.25
            _sumar(out, tramo, base_ms + (rep - 1) * 255.0, 0.56 ** rep)
        return out, "Stutter Urbana + Reverb Low + Eco"

    else:
        # "phaser_hall_throw": Última palabra con Phaser espacial FM + 4 rebotes + sombra grave
        pal_ph = _phaser_estereo_fx(pal_rev)
        pal_slow = _estirar_pitch_slow(palabra_fin, factor=1.26)
        cola_ms = 1650
        out = _pista(_dur_ms(remate_crudo) + cola_ms)
        _sumar(out, remate_crudo, 0.0, 1.0)

        base_ms = _dur_ms(remate_crudo) - 25.0
        for rep in range(1, 5):
            c = (rep + 1) % 2
            tramo = np.zeros_like(pal_ph)
            tramo[:, c] = pal_ph[:, c] * 0.95
            tramo[:, 1 - c] = pal_ph[:, 1 - c] * 0.30
            _sumar(out, tramo, base_ms + (rep - 1) * 255.0, 0.58 ** rep)
        _sumar(out, pal_slow, base_ms + 255.0, 0.38)
        return out, "Phaser Espacial FM + Eco + Low"


def _ducking_por_voz(fondo: np.ndarray, pista_voz: np.ndarray, reduccion_db: float = -3.5) -> np.ndarray:
    n = min(len(fondo), len(pista_voz))
    blk = int(SR * 0.015)
    nb = n // blk + 1
    env = np.pad(np.abs(pista_voz[:n]).max(axis=1), (0, nb * blk - n)).reshape(nb, blk).max(axis=1)
    hablando = (env > 0.04).astype(np.float64)

    a = np.exp(-15.0 / 25.0)
    r = np.exp(-15.0 / 240.0)
    suave = np.zeros(nb)
    val = 0.0
    for i in range(nb):
        coef = a if hablando[i] > val else r
        val = coef * val + (1.0 - coef) * hablando[i]
        suave[i] = val

    centros = (np.arange(nb) + 0.5) * blk
    curva_db = np.interp(np.arange(len(fondo)), centros, suave * reduccion_db)
    return fondo * _g_array(curva_db)[:, None]


def _stereo_delay(x: np.ndarray, delay_l_ms: float = 220.0, delay_r_ms: float = 330.0, feedback: float = 0.35, mezcla: float = 0.30) -> np.ndarray:
    out = x.copy()
    dl = int(SR * delay_l_ms / 1000)
    dr = int(SR * delay_r_ms / 1000)
    eco = _filtrar(x, pasa_altos=250, pasa_bajos=4200)
    for rep in range(1, 4):
        gan = (feedback ** rep) * mezcla
        desp_l = rep * dl
        desp_r = rep * dr
        if desp_l < len(out):
            out[desp_l:, 0] += eco[:-desp_l, 1] * gan
        if desp_r < len(out):
            out[desp_r:, 1] += eco[:-desp_r, 0] * gan
    return out


def _reverb_estereo(x: np.ndarray, seg: float = 1.2, mezcla: float = 0.30, brillo: float = 5200.0) -> np.ndarray:
    ir_n = int(SR * seg)
    t = np.arange(ir_n) / SR
    rng = np.random.default_rng(99)
    ir = rng.standard_normal((ir_n, 2)) * np.exp(-6.0 * t / seg)[:, None]
    ir = _filtrar(ir, pasa_altos=180, pasa_bajos=brillo)
    predelay = int(SR * 0.022)
    ir = np.pad(ir, ((predelay, 0), (0, 0)))[:ir_n]
    ir /= (np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True)) + 1e-9)

    largo = x.shape[0] + ir_n
    nfft = 1 << (largo - 1).bit_length()
    humedo = np.fft.irfft(np.fft.rfft(x, nfft, axis=0) * np.fft.rfft(ir, nfft, axis=0), nfft, axis=0)[:largo]
    seco = np.pad(x, ((0, ir_n), (0, 0)))
    return seco + humedo * mezcla


# ---------------------------------------------------------------------------
# Sintetizadores FM & Urbanos
# ---------------------------------------------------------------------------

def _synth_brass(freqs: list[float], dur_ms: float, corte_lp: float = 3600.0) -> np.ndarray:
    t = _t(dur_ms)
    out = np.zeros((len(t), 2))
    rng = np.random.default_rng(33)
    for f in freqs:
        for det, pl, pr in [(-0.010, 0.9, 0.1), (-0.004, 0.7, 0.3), (0.0, 0.5, 0.5), (0.004, 0.3, 0.7), (0.010, 0.1, 0.9)]:
            f_act = f * (1.0 + det)
            fase = rng.uniform(0, 1)
            saw = 2.0 * ((t * f_act + fase) % 1.0) - 1.0
            out[:, 0] += saw * pl
            out[:, 1] += saw * pr

    ataque = min(int(SR * 0.055), len(t))
    env = np.exp(-2.0 * t)
    env[:ataque] *= np.linspace(0.1, 1.0, ataque)
    out = _filtrar(out * env[:, None], pasa_altos=95, pasa_bajos=corte_lp)
    return np.tanh(out * 0.45) * 0.38


def _pop_pluck(freq: float, dur_ms: float = 240, pan: float = 0.5) -> np.ndarray:
    t = _t(dur_ms)
    mod = 2.4 * np.exp(-t * 26.0) * np.sin(2 * np.pi * (freq * 2.0) * t)
    onda = np.sin(2 * np.pi * freq * t + mod) + 0.38 * np.sin(2 * np.pi * (freq * 0.5) * t)
    env = np.exp(-t * 10.0)
    ataque = min(int(SR * 0.003), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    mono = onda * env * 0.36
    return np.column_stack((mono * pan, mono * (1.0 - pan)))


def _funky_bass(freq: float, dur_ms: float) -> np.ndarray:
    t = _t(dur_ms)
    mod = 1.8 * np.exp(-t * 20.0) * np.sin(2 * np.pi * (freq * 2.0) * t)
    sub = np.sin(2 * np.pi * freq * t + mod)
    arm = 0.40 * np.sin(2 * np.pi * (freq * 2.0) * t) * np.exp(-t * 10.0)
    env = np.exp(-3.8 * t)
    ataque = min(int(SR * 0.006), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    return _est(np.tanh((sub + arm) * env * 1.6) * 0.44)


def _kick_punch(dur_ms: float = 240) -> np.ndarray:
    t = _t(dur_ms)
    f_env = 48.0 + 145.0 * np.exp(-t * 42.0)
    fase = 2 * np.pi * np.cumsum(f_env) / SR
    cuerpo = np.tanh(np.sin(fase) * 1.8) * np.exp(-t * 10.5)
    click = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=1000, pasa_bajos=4500)
    click *= np.exp(-t * 110.0)[:, None] * 0.24
    return _est(cuerpo * 0.88) + click


def _snare_pop(dur_ms: float = 190) -> np.ndarray:
    t = _t(dur_ms)
    tono = np.sin(2 * np.pi * 185.0 * t) * np.exp(-t * 28.0) * 0.42
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=700, pasa_bajos=7500)
    ruido *= np.exp(-t * 20.0)[:, None] * 0.35
    return _est(tono) + ruido


def _hihat_crisp(dur_ms: float = 65, abierto: bool = False) -> np.ndarray:
    dur = 180 if abierto else dur_ms
    t = _t(dur)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=6500, pasa_bajos=13500)
    env = np.exp(-t * (14.0 if abierto else 50.0))
    return ruido * env[:, None] * (0.22 if abierto else 0.16)


def _whoosh_aerodinamico(dur_ms: float = 700, f_ini: float = 250.0, f_fin: float = 4800.0) -> np.ndarray:
    t = _t(dur_ms)
    prog = t / (dur_ms / 1000.0)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=300, pasa_bajos=7500)
    f_curva = f_ini * ((f_fin / f_ini) ** (prog ** 1.6))
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    tono = np.sin(fase) * 0.26
    env = np.sin(np.pi * (prog ** 1.3)) ** 2
    pan_l = 0.10 + 0.90 * prog
    pan_r = 1.0 - 0.90 * prog
    out = (ruido * 0.65 + np.column_stack((tono * pan_l, tono * pan_r))) * env[:, None]
    return _pico(out, 0.90)


def _sub_drop_cine(dur_ms: float = 1200) -> np.ndarray:
    t = _t(dur_ms)
    f_curva = 32.0 + 85.0 * np.exp(-t * 7.5)
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    onda = np.tanh(np.sin(fase) * 1.8) * np.exp(-t * 2.4)
    return _est(onda * 0.88)


def _laser_drop(dur_ms: float = 420) -> np.ndarray:
    t = _t(dur_ms)
    f_curva = 65.0 + 3200.0 * np.exp(-t * 14.0)
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    onda = np.tanh(np.sin(fase) * 1.6) * np.exp(-t * 5.5)
    return _est(onda * 0.62)


def _radio_tuning_dial(dur_ms: float = 750) -> np.ndarray:
    t = _t(dur_ms)
    prog = t / (dur_ms / 1000.0)
    f_dial = 650.0 + 1900.0 * np.sin(2 * np.pi * 4.0 * prog) + 2000.0 * (prog ** 2)
    fase = 2 * np.pi * np.cumsum(f_dial) / SR
    silbido = np.sin(fase) * 0.25
    estatica = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=900, pasa_bajos=6000) * 0.28
    return _fade(estatica + _est(silbido), 30, 40)


# ---------------------------------------------------------------------------
# Sweepers: 4 Intros, 4 Transiciones Notorias y Whip de Pase Hombre/Mujer
# ---------------------------------------------------------------------------

def sintetizar_intro() -> np.ndarray:
    estilo = random.choice(["dial_tuning_pips", "urbana_wah_groove", "cyber_glitch_stutter", "stadium_arena_hit"])
    print(f"Estilo de Intro FM elegido: [{estilo}]")
    pista = _pista(2800)

    if estilo == "dial_tuning_pips":
        _sumar(pista, _radio_tuning_dial(760), 0, 0.80)
        for i, pos in enumerate([120, 340, 560, 780]):
            es_ultimo = (i == 3)
            dur = 300 if es_ultimo else 70
            freq = 1760.0 if es_ultimo else 880.0
            t = _t(dur)
            pip = (np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * freq * 2 * t)) * np.exp(-t * (6.0 if es_ultimo else 22.0))
            _sumar(pista, _est(pip * 0.45), pos)

        _sumar(pista, _laser_drop(420), 780, 0.85)
        _sumar(pista, _kick_punch(260), 800, 1.0)
        _sumar(pista, _synth_brass([220.0, 261.63, 329.63, 392.00, 493.88], 1600), 800, 0.90)
        notas = [(880.0, 0, 0.2), (1046.50, 100, 0.8), (1318.51, 200, 0.25), (1567.98, 300, 0.75), (1760.0, 400, 0.5)]

    elif estilo == "urbana_wah_groove":
        acorde_wah = _synth_brass([174.61, 220.0, 261.63, 329.63, 392.00], 800, corte_lp=3800)
        acorde_wah = _phaser_estereo_fx(acorde_wah) * (np.linspace(0.2, 1.0, len(acorde_wah))[:, None])
        _sumar(pista, acorde_wah, 0, 0.85)
        for pos_k in [0, 260, 520, 800, 1050]:
            _sumar(pista, _kick_punch(220), pos_k, 0.95)
        _sumar(pista, _snare_pop(180), 400, 0.85)
        _sumar(pista, _snare_pop(180), 800, 0.95)
        _sumar(pista, _sub_drop_cine(1300), 800, 0.95)
        _sumar(pista, _synth_brass([196.0, 246.94, 293.66, 392.0, 493.88], 1650), 800, 0.90)
        notas = [(783.99, 0, 0.7), (987.77, 130, 0.3), (1174.66, 260, 0.7), (1567.98, 390, 0.5)]

    elif estilo == "cyber_glitch_stutter":
        freqs_g = [1400, 2100, 1050, 2800, 1600, 3200, 1200, 2400]
        for idx_g, pos_g in enumerate(range(0, 720, 90)):
            t_g = _t(55)
            bip = np.sin(2 * np.pi * freqs_g[idx_g % len(freqs_g)] * t_g) * np.hanning(len(t_g)) * 0.38
            c_l = 0.9 if idx_g % 2 == 0 else 0.1
            _sumar(pista, np.column_stack((bip * c_l, bip * (1.0 - c_l))), pos_g)
        _sumar(pista, _whoosh_aerodinamico(780, 200, 5800), 20, 0.85)
        _sumar(pista, _sub_drop_cine(1400), 780, 1.0)
        _sumar(pista, _laser_drop(380), 780, 0.80)
        _sumar(pista, _synth_brass([164.81, 196.0, 246.94, 293.66, 369.99], 1600), 800, 0.90)
        notas = [(1318.51, 0, 0.2), (987.77, 90, 0.8), (1174.66, 180, 0.3), (1567.98, 270, 0.7), (1975.53, 360, 0.5)]

    else:
        for idx_r, pos_r in enumerate([0, 180, 360, 480, 600, 700]):
            gan_r = 0.45 + 0.55 * (idx_r / 5.0)
            _sumar(pista, _snare_pop(190), pos_r, gan_r)
            _sumar(pista, _kick_punch(220), pos_r, gan_r * 0.8)
        _sumar(pista, _whoosh_aerodinamico(750, 300, 4600), 50, 0.85)
        _sumar(pista, _sub_drop_cine(1400), 800, 1.0)
        _sumar(pista, _kick_punch(300), 800, 1.0)
        _sumar(pista, _synth_brass([261.63, 329.63, 392.00, 493.88, 587.33], 1700), 800, 0.95)
        notas = [(523.25, 0, 0.3), (783.99, 110, 0.7), (1046.50, 220, 0.3), (1318.51, 330, 0.7), (1567.98, 440, 0.5)]

    logo = _pista(1800)
    for f, pos_n, pan in notas:
        _sumar(logo, _pop_pluck(f, 520, pan=pan), pos_n, 1.0)
    _sumar(pista, _stereo_delay(logo, 200, 300, 0.42, 0.38), 830, 0.95)

    return _fade(_reverb_estereo(pista, seg=1.35, mezcla=0.35), 15, 380)


def sintetizar_transicion() -> np.ndarray:
    """
    Gran Separador FM Notorio (2.0 s) entre Clima y Noticias:
    Suena con la cortina del clima ya silenciada para que el oyente sienta claramente el cambio de bloque.
    """
    estilo = random.choice(["flash_news_radar", "double_laser_whip", "reverse_suction_drop", "tuning_stinger_hit"])
    print(f"Estilo de Transición Clima->Noticias elegido: [{estilo}]")
    pista = _pista(2000)

    if estilo == "flash_news_radar":
        for i, pos_b in enumerate([0, 150, 300, 450]):
            t_b = _t(70)
            f_s = 1760.0 if i == 3 else 1318.51
            sonar = (np.sin(2 * np.pi * f_s * t_b) + 0.3 * np.sin(2 * np.pi * f_s * 2 * t_b)) * np.hanning(len(t_b)) * 0.50
            _sumar(pista, _est(sonar), pos_b)
        _sumar(pista, _whoosh_aerodinamico(520, 300, 5400), 40, 0.95)
        _sumar(pista, _sub_drop_cine(1250), 500, 1.0)
        _sumar(pista, _kick_punch(280), 500, 1.0)
        notas = [587.33, 880.0, 1174.66, 1318.51, 1760.0]
        ini_st = 500

    elif estilo == "double_laser_whip":
        _sumar(pista, _laser_drop(320), 0, 0.90)
        _sumar(pista, _laser_drop(380), 220, 1.0)
        _sumar(pista, _whoosh_aerodinamico(480, 5000, 280), 40, 0.95)
        _sumar(pista, _kick_punch(280), 460, 1.0)
        _sumar(pista, _snare_pop(200), 460, 0.95)
        _sumar(pista, _sub_drop_cine(1200), 460, 1.0)
        notas = [659.25, 987.77, 1318.51, 1567.98, 1975.53]
        ini_st = 460

    elif estilo == "reverse_suction_drop":
        _sumar(pista, _whoosh_aerodinamico(550, 180, 5800), 0, 1.0)
        _sumar(pista, _laser_drop(350), 520, 0.85)
        _sumar(pista, _sub_drop_cine(1300), 520, 1.0)
        _sumar(pista, _kick_punch(280), 520, 1.0)
        notas = [880.0, 659.25, 987.77, 1318.51, 1760.0]
        ini_st = 520

    else:
        _sumar(pista, _radio_tuning_dial(480), 0, 0.88)
        _sumar(pista, _whoosh_aerodinamico(480, 400, 4800), 20, 0.90)
        _sumar(pista, _kick_punch(280), 460, 1.0)
        _sumar(pista, _sub_drop_cine(1200), 460, 1.0)
        notas = [523.25, 659.25, 783.99, 1046.50, 1318.51]
        ini_st = 460

    stinger = _pista(1400)
    for i, f in enumerate(notas):
        _sumar(stinger, _pop_pluck(f, 450, pan=0.15 if i % 2 == 0 else 0.85), i * 85, 1.0)
    _sumar(stinger, _synth_brass([notas[0] * 0.5, notas[1] * 0.5, notas[2] * 0.5], 1050), 150, 0.90)
    _sumar(pista, _stereo_delay(stinger, 165, 250, 0.40, 0.36), ini_st, 1.0)

    return _fade(_reverb_estereo(pista, seg=1.05, mezcla=0.32), 10, 250)


def sintetizar_whip_pase_voces() -> np.ndarray:
    """
    Efecto rápido y moderno de transición (600ms) para el pase entre la voz de Noticias (ej. Mujer)
    y la voz del Remate Final (ej. Hombre): Whoosh estéreo rápido + Sub-Impacto + Destello FM.
    """
    pista = _pista(750)
    _sumar(pista, _whoosh_aerodinamico(360, 600, 5200), 0, 0.85)
    _sumar(pista, _laser_drop(260), 280, 0.72)
    _sumar(pista, _kick_punch(240), 300, 0.95)
    _sumar(pista, _sub_drop_cine(650), 300, 0.80)
    _sumar(pista, _pop_pluck(1318.51, 350, pan=0.25), 300, 0.80)
    _sumar(pista, _pop_pluck(1760.00, 400, pan=0.75), 370, 0.85)
    return _fade(_reverb_estereo(pista, seg=0.7, mezcla=0.28)[: len(pista)], 10, 180)


def sintetizar_cierre() -> np.ndarray:
    pista = _pista(2800)
    _sumar(pista, _whoosh_aerodinamico(450, 350, 3400), 0, 0.70)
    _sumar(pista, _sub_drop_cine(1600), 360, 0.90)
    _sumar(pista, _kick_punch(300), 380, 0.95)
    _sumar(pista, _funky_bass(55.0, 1900), 380, 0.90)

    acorde_fin = _synth_brass([220.0, 261.63, 329.63, 392.00, 523.25], 2100, corte_lp=2800)
    _sumar(pista, acorde_fin, 380, 0.85)

    notas_fin = [1318.51, 1046.50, 880.0, 659.25]
    arp = _pista(2200)
    for i, f in enumerate(notas_fin):
        _sumar(arp, _pop_pluck(f, 700 if i < 3 else 1200, pan=0.75 - i * 0.16), 380 + i * 115, 0.92)
    _sumar(pista, _stereo_delay(arp, 220, 330, 0.38, 0.32), 0, 0.90)

    out = _reverb_estereo(pista, seg=1.6, mezcla=0.40)[: len(pista)]
    return _fade(out, 15, 1200)


# ---------------------------------------------------------------------------
# Cortinas Musicales
# ---------------------------------------------------------------------------

def bed_clima_groove(modo: str, dur_ms: float) -> np.ndarray:
    pista = _pista(dur_ms)
    t = _t(dur_ms)
    beat_ms = 500.0
    semi_ms = 125.0

    if modo in ("lluvia", "tormenta"):
        acordes = [
            ([220.0, 261.63, 329.63, 392.0], [440.0, 523.25, 659.25, 783.99], 55.0),
            ([174.61, 220.0, 261.63, 349.23], [349.23, 440.0, 523.25, 659.25], 43.65),
        ]
    else:
        acordes = [
            ([261.63, 329.63, 392.0, 493.88], [523.25, 659.25, 783.99, 987.77], 65.41),
            ([196.0, 246.94, 293.66, 392.0], [392.0, 493.88, 587.33, 783.99], 49.00),
            ([220.0, 261.63, 329.63, 440.0], [440.0, 523.25, 659.25, 880.0], 55.00),
            ([174.61, 220.0, 261.63, 349.23], [349.23, 440.0, 523.25, 698.46], 43.65),
        ]

    compas_ms = beat_ms * 4
    num_compases = int(dur_ms / compas_ms) + 2
    musica = _pista(dur_ms)

    for c in range(num_compases):
        ini_c = c * compas_ms
        if ini_c >= dur_ms:
            break
        pad_f, arp_f, bajo_f = acordes[c % len(acordes)]
        _sumar(musica, _synth_brass(pad_f, compas_ms, corte_lp=1800), ini_c, 0.58)

        for b in range(8):
            f_b = bajo_f * 2.0 if b in (3, 7) else bajo_f
            _sumar(musica, _funky_bass(f_b, 230), ini_c + b * 250.0, 0.80)

        for s in range(16):
            nota_f = arp_f[s % len(arp_f)]
            pan = 0.20 if s % 2 == 0 else 0.80
            _sumar(musica, _pop_pluck(nota_f, 180, pan=pan), ini_c + s * semi_ms, 0.55)

    fase_beat = (t * 2.0) % 1.0
    sidechain = 0.50 + 0.50 * (1.0 - np.exp(-fase_beat * 5.0))
    musica *= sidechain[:, None]
    pista += _stereo_delay(musica, 250, 375, 0.30, 0.25)

    kick = _kick_punch(230)
    snare = _snare_pop(180)
    hh_c = _hihat_crisp(55, abierto=False)
    hh_o = _hihat_crisp(160, abierto=True)

    for b in range(int(dur_ms / beat_ms) + 1):
        pos_b = b * beat_ms
        _sumar(pista, kick, pos_b, 0.85)
        if b % 2 == 1:
            _sumar(pista, snare, pos_b, 0.72)
        _sumar(pista, hh_c, pos_b, 0.62)
        _sumar(pista, hh_o, pos_b + 250.0, 0.58)

    if modo in ("lluvia", "tormenta"):
        agua = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=1100, pasa_bajos=7200)
        oleaje = 0.75 + 0.25 * np.sin(2 * np.pi * 0.3 * t)
        pista += agua * oleaje[:, None] * 0.20
        if modo == "tormenta":
            for pos_tr in (dur_ms * 0.20, dur_ms * 0.65):
                t_tr = _t(2400)
                trueno = _filtrar(np.random.uniform(-1, 1, (len(t_tr), 2)), pasa_altos=30, pasa_bajos=150)
                env_tr = np.minimum(t_tr / 0.15, 1.0) * np.exp(-t_tr * 1.3)
                _sumar(pista, _pico(trueno * env_tr[:, None], 0.75), pos_tr, 0.65)

    pista = _hueco_vocal_cortina(pista)
    return _filtrar(pista, pasa_altos=40, pasa_bajos=10500)


def bed_noticias_groove(dur_ms: float) -> np.ndarray:
    pista = _pista(dur_ms)
    beat_ms = 476.19
    semi_ms = beat_ms / 4.0
    compas_ms = beat_ms * 4

    progresion_not = [
        [220.0, 261.63, 329.63, 493.88],
        [174.61, 220.0, 261.63, 329.63],
        [146.83, 220.0, 261.63, 349.23],
        [164.81, 220.0, 246.94, 329.63],
    ]
    for c in range(int(dur_ms / compas_ms) + 2):
        ini_c = c * compas_ms
        if ini_c >= dur_ms:
            break
        ac = progresion_not[c % len(progresion_not)]
        _sumar(pista, _synth_brass(ac, compas_ms, corte_lp=1650), ini_c, 0.54)

    secuencia = [220.0, 220.0, 440.0, 220.0, 329.63, 220.0, 392.0, 329.63]
    for i, pos in enumerate(np.arange(0, dur_ms - semi_ms, semi_ms)):
        f = secuencia[i % len(secuencia)]
        pan = 0.22 if i % 2 == 0 else 0.78
        _sumar(pista, _pop_pluck(f, 125, pan=pan), pos, 0.64)
        if i % 2 == 0:
            _sumar(pista, _funky_bass(f * 0.5, 210), pos, 0.82)
        if i % 8 == 0:
            t_b = _t(55)
            bip = (np.sin(2 * np.pi * 1318.51 * t_b) + 0.3 * np.sin(2 * np.pi * 2637.02 * t_b)) * np.hanning(len(t_b))
            c_sonar = 0 if (i // 8) % 2 == 0 else 1
            sonar_est = np.zeros((len(t_b), 2))
            sonar_est[:, c_sonar] = bip * 0.28
            sonar_est[:, 1 - c_sonar] = bip * 0.10
            _sumar(pista, sonar_est, pos)

    kick = _kick_punch(210)
    snare = _snare_pop(165)
    hh_c = _hihat_crisp(50, abierto=False)
    hh_o = _hihat_crisp(140, abierto=True)

    for b, pos in enumerate(np.arange(0, dur_ms - beat_ms, beat_ms)):
        _sumar(pista, kick, pos, 0.88)
        if b % 2 == 1:
            _sumar(pista, snare, pos, 0.72)
        _sumar(pista, hh_c, pos, 0.60)
        _sumar(pista, hh_o, pos + beat_ms * 0.5, 0.55)

    pista = _hueco_vocal_cortina(pista)
    return _stereo_delay(_filtrar(pista, pasa_altos=45, pasa_bajos=10500), 238, 357, 0.26, 0.22)


# ---------------------------------------------------------------------------
# Carga de Assets opcionales
# ---------------------------------------------------------------------------

def buscar_asset(base: str) -> AudioSegment | None:
    candidatos = []
    for ext in EXTENSIONES:
        candidatos += glob.glob(os.path.join(ASSETS_DIR, f"{base}*{ext}"))
    if not candidatos:
        return None
    ruta = random.choice(sorted(candidatos))
    print(f"Usando archivo propio: {ruta}")
    return AudioSegment.from_file(ruta)


def _extender(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n].copy()
    cf = max(1, min(int(SR * 0.4), len(x) // 3))
    salida = x.copy()
    while len(salida) < n:
        fade = np.linspace(0, 1, cf)[:, None]
        salida[-cf:] = salida[-cf:] * (1 - fade) + x[:cf] * fade
        salida = np.vstack((salida, x[cf:]))
    return salida[:n]


def cargar_efecto(nombre: str, sintetizador) -> np.ndarray:
    seg = buscar_asset(nombre)
    x = seg_a_array(seg) if seg is not None else sintetizador()
    return _pico(x, 0.96)


def construir_ambiente(nombre: str, dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset(f"clima/{nombre}")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print(f"Generando cortina V9 Pop/Dance ({nombre})...")
        base = bed_clima_groove(nombre, dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 250, 450)


def construir_cama_noticias(dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset("noticias_cama")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print("Generando cortina V9 Breaking News...")
        base = bed_noticias_groove(dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 200, 2600)


def cargar_pista_opcional(base: str) -> np.ndarray | None:
    seg = buscar_asset(base)
    if seg is None:
        return None
    return _rms(seg_a_array(seg), REFERENCIA_RMS_DB)


# ---------------------------------------------------------------------------
# Voces (Google Cloud TTS) - 100% SECAS Y CLARAS EN LA LECTURA
# ---------------------------------------------------------------------------

def texto_a_ssml(texto: str, rate: str = "1.03") -> str:
    seguro = texto.replace("&", " y ").replace("<", "").replace(">", "")
    seguro = re.sub(r'\.\s+', '. <break time="250ms"/> ', seguro)
    seguro = re.sub(r':\s+', ': <break time="180ms"/> ', seguro)
    return f"<speak><prosody rate='{rate}'>{seguro}</prosody></speak>"


def sintetizar_voz(client, texto: str, nombre_voz: str, audio_config, rate: str = "1.03") -> np.ndarray:
    entrada = texttospeech.SynthesisInput(ssml=texto_a_ssml(texto, rate=rate))
    voz = texttospeech.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
    resp = client.synthesize_speech(input=entrada, voice=voz, audio_config=audio_config)
    seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
    return _recortar_silencio(seg_a_array(seg))


def procesar_voz_seca(x: np.ndarray) -> np.ndarray:
    """
    Procesamiento 100% SECO (CERO eco y CERO reverb) para el cuerpo del Clima y de las Noticias.
    Solo aplica ecualización limpia de micrófono y compresión para máxima nitidez.
    """
    x = _filtrar(x, pasa_altos=85)
    cuerpo = _filtrar(x, pasa_altos=110, pasa_bajos=240)
    claridad = _filtrar(x, pasa_altos=2400, pasa_bajos=5600)
    aire = np.tanh(_filtrar(x, pasa_altos=6500) * 1.8) * 0.10

    x = x + 0.20 * cuerpo + 0.30 * claridad + aire
    x = _comprimir(x, thr_db=-23.5, ratio=3.8, ataque_ms=3.5, relajo_ms=110.0)
    return _pico(x, 0.94)


# ---------------------------------------------------------------------------
# Ensamblado V9 (Tag Inicio FM -> Clima Seco -> Gran Transición -> Noticias Secas -> Whip Dúo -> Remate FM)
# ---------------------------------------------------------------------------

def ensamblar_spot(
    voz_tag_ini_cruda: np.ndarray,
    voz_clima_seca: np.ndarray,
    voz_noticias_seca: np.ndarray,
    voz_remate_cruda: np.ndarray,
    intro: np.ndarray,
    trans: np.ndarray,
    whip_duo: np.ndarray,
    cierre: np.ndarray,
    ambiente: np.ndarray,
    cama: np.ndarray | None,
    cama_noticias: np.ndarray,
) -> np.ndarray:
    # 1. Procesamos el Tag Mítico Inicial con efecto Urbana Play / La 100
    tag_ini_fx, pre_ms_tag, nombre_fx_ini = procesar_tag_inicio_fm(voz_tag_ini_cruda)
    print(f"FX Tag Inicial: [{nombre_fx_ini}]")

    # 2. Procesamos el Remate Final (dicho por el otro locutor tras el pase) con Reverb Low / Eco Ping-Pong / Slow
    remate_final_fx, nombre_fx_fin = procesar_remate_final_fm(voz_remate_cruda)
    print(f"FX Remate Final Dúo: [{nombre_fx_fin}]")

    # Línea de tiempo exacta
    inicio_habla_tag = 1600.0
    inicio_pista_tag = max(650.0, inicio_habla_tag - pre_ms_tag)
    fin_tag_seco = inicio_habla_tag + _dur_ms(voz_tag_ini_cruda)

    # El cuerpo del clima (100% seco) arranca apenas termina el tag inicial
    inicio_clima = fin_tag_seco + 220.0
    fin_clima = inicio_clima + _dur_ms(voz_clima_seca)

    # GRAN TRANSICIÓN NOTORIA CLIMA -> NOTICIAS (1.45 segundos libres de voz para que golpee bien al frente)
    inicio_trans = fin_clima + 120.0
    inicio_noticias = inicio_trans + 1450.0
    fin_noticias = inicio_noticias + _dur_ms(voz_noticias_seca)

    # PASE HOMBRE / MUJER AL FINAL: suena el Whip FM y entra el otro locutor con la frase mítica final
    inicio_whip_duo = fin_noticias - 80.0
    inicio_remate_duo = fin_noticias + 360.0
    fin_remate_seco = inicio_remate_duo + _dur_ms(voz_remate_cruda)
    fin_cola_remate = inicio_remate_duo + _dur_ms(remate_final_fx)

    # El cierre entra acompañando el eco del remate y la música baja despacito durante 3 segundos
    inicio_cierre = fin_remate_seco + 120.0
    total_ms = max(inicio_cierre + _dur_ms(cierre), fin_cola_remate + 1300.0)

    # 1. Bus de Voces
    bus_voces = _pista(total_ms + 800)
    _sumar(bus_voces, tag_ini_fx, inicio_pista_tag, 1.0)
    _sumar(bus_voces, voz_clima_seca, inicio_clima, 1.0)
    _sumar(bus_voces, voz_noticias_seca, inicio_noticias, 1.0)
    _sumar(bus_voces, remate_final_fx, inicio_remate_duo, 1.0)

    # 2. Bus de Cortinas (Cortina Clima se apaga antes de la Transición; Cortina Noticias arranca tras el golpe)
    bus_cortinas = _pista(total_ms + 800)
    dur_tramo_clima = int(SR * (inicio_trans + 150.0 - 800.0) / 1000)
    if dur_tramo_clima > 0:
        amb_recortado = _fade(ambiente[: min(len(ambiente), dur_tramo_clima)], 250, 350)
        _sumar(bus_cortinas, amb_recortado, 800.0, _g(AMBIENTE_DB))

    _sumar(bus_cortinas, cama_noticias, inicio_trans + 520.0, _g(TICKER_NOTICIAS_DB))
    if cama is not None:
        _sumar(bus_cortinas, _extender(cama, len(bus_cortinas)), 0.0, _g(CAMA_DB_LIBRE - 3.0))

    bus_cortinas = _ducking_por_voz(bus_cortinas, bus_voces, reduccion_db=-3.5)

    # Fade-out lento y limpio al final (baja despacito mientras rebota el eco final)
    n_total = int(SR * total_ms / 1000)
    ini_fade_cama = max(0, int(SR * (fin_remate_seco + 150.0) / 1000))
    if n_total > ini_fade_cama:
        curva_cama = np.linspace(1.0, 0.0, n_total - ini_fade_cama) ** 1.5
        bus_cortinas[ini_fade_cama:n_total] *= curva_cama[:, None]
        bus_cortinas[n_total:] = 0.0

    # 3. Bus de Sweepers y Transiciones (Al 100% sin ducking)
    bus_fx = _pista(total_ms + 800)
    _sumar(bus_fx, intro, 0.0, _g(EFECTOS_DB))
    _sumar(bus_fx, trans, inicio_trans, _g(EFECTOS_DB))
    _sumar(bus_fx, whip_duo, inicio_whip_duo, _g(EFECTOS_DB - 1.0))
    _sumar(bus_fx, cierre, inicio_cierre, _g(EFECTOS_DB - 1.5))

    master = (bus_voces + bus_cortinas + bus_fx)[:n_total]
    return _fade(master, 15, 950)


def masterizar(x: np.ndarray) -> np.ndarray:
    x = _filtrar(x, pasa_altos=35)

    graves = _comprimir(_filtrar(x, pasa_bajos=200), thr_db=-18.0, ratio=3.2, ataque_ms=10.0, relajo_ms=160.0)
    medios = _comprimir(_filtrar(x, pasa_altos=200, pasa_bajos=4200), thr_db=-17.0, ratio=2.6, ataque_ms=8.0, relajo_ms=140.0)
    agudos = _comprimir(_filtrar(x, pasa_altos=4200), thr_db=-19.0, ratio=2.4, ataque_ms=4.0, relajo_ms=90.0)

    x = graves * 1.08 + medios * 1.04 + agudos * 1.12

    mid = 0.5 * (x[:, 0] + x[:, 1])
    side = 0.5 * (x[:, 0] - x[:, 1])
    side_amplio = _filtrar(_est(side), pasa_altos=240)[:, 0] * 1.25
    x = np.column_stack((mid + side_amplio, mid - side_amplio))

    x = _comprimir(x, thr_db=-13.5, ratio=3.8, ataque_ms=5.0, relajo_ms=120.0)
    x = np.tanh(x * 1.14)
    return _fade(_pico(x, 0.95), 10, 800)


def generar_spot(
    tag_ini: str,
    texto_clima: str,
    texto_noticias: str,
    texto_remate: str,
    ambiente_nombre: str,
    path_salida: str,
) -> None:
    client = texttospeech.TextToSpeechClient()
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    ahora = datetime.now(ZONA_HORARIA)

    # Rotación por hora:
    # Hora Par -> Mujer abre y da Clima | Hombre da Noticias | Mujer vuelve para el Remate Final Mítico.
    # Hora Impar -> Hombre abre y da Clima | Mujer da Noticias | Hombre vuelve para el Remate Final Mítico.
    if ahora.hour % 2 == 0:
        voz_a, voz_b = "es-US-Neural2-A", "es-US-Neural2-B"
        print("Turno (Hora Par): Mujer (Tag + Clima) -> Hombre (Noticias) -> Pase FM -> Mujer (Remate Final)")
    else:
        voz_a, voz_b = "es-US-Neural2-B", "es-US-Neural2-A"
        print("Turno (Hora Impar): Hombre (Tag + Clima) -> Mujer (Noticias) -> Pase FM -> Hombre (Remate Final)")

    voz_tag_ini = procesar_voz_seca(sintetizar_voz(client, tag_ini, voz_a, audio_config, rate="1.00"))
    voz_clima = procesar_voz_seca(sintetizar_voz(client, texto_clima, voz_a, audio_config, rate="1.03"))
    voz_noticias = procesar_voz_seca(sintetizar_voz(client, texto_noticias, voz_b, audio_config, rate="1.03"))
    voz_remate = procesar_voz_seca(sintetizar_voz(client, texto_remate, voz_a, audio_config, rate="0.99"))

    intro = cargar_efecto("intro", sintetizar_intro)
    trans = cargar_efecto("transicion", sintetizar_transicion)
    whip_duo = sintetizar_whip_pase_voces()
    cierre = cargar_efecto("cierre", sintetizar_cierre)

    dur_bloque_clima = _dur_ms(voz_tag_ini) + _dur_ms(voz_clima) + 2500.0
    ambiente = construir_ambiente(ambiente_nombre, dur_bloque_clima)
    cama = cargar_pista_opcional("cama")
    dur_bloque_not = _dur_ms(voz_noticias) + _dur_ms(voz_remate) + 5000.0
    cama_noticias = construir_cama_noticias(dur_bloque_not)

    mezcla = ensamblar_spot(
        voz_tag_ini, voz_clima, voz_noticias, voz_remate,
        intro, trans, whip_duo, cierre, ambiente, cama, cama_noticias
    )
    final = masterizar(mezcla)
    array_a_seg(final).export(path_salida, format="mp3", bitrate="192k")
    print(f"¡Spot V9 Duo Imaging ({len(final) / SR:.1f} s) guardado exitosamente en {path_salida}!")


# ---------------------------------------------------------------------------
# Subida SFTP y Main
# ---------------------------------------------------------------------------

def subir_sftp(local_path: str, host: str, port: int, user: str, password: str, remote_dir: str) -> None:
    transport = paramiko.Transport((host, port))
    try:
        transport.connect(username=user, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        try:
            remote_path = f"{remote_dir.rstrip('/')}/{REMOTE_FILENAME}"
            sftp.put(local_path, remote_path)
            print(f"Subido correctamente a {remote_path}")
        finally:
            sftp.close()
    finally:
        transport.close()


def main():
    print("=== EJECUTANDO NOGARADIO VERSION V9 DUO IMAGING (URBANA PLAY / LA 100 STYLE) ===")
    sftp_host = os.environ.get("SFTP_HOST")
    sftp_port = int(os.environ.get("SFTP_PORT", "2022"))
    sftp_user = os.environ.get("SFTP_USER")
    sftp_pass = os.environ.get("SFTP_PASS")
    sftp_dir = os.environ.get("SFTP_REMOTE_DIR", "/clima")

    faltantes = [n for n, v in [("SFTP_HOST", sftp_host), ("SFTP_USER", sftp_user), ("SFTP_PASS", sftp_pass)] if not v]
    if faltantes:
        print(f"Faltan variables: {', '.join(faltantes)}", file=sys.stderr)
        sys.exit(1)

    print("Consultando clima...")
    datos_clima = obtener_clima()
    tag_ini, texto_clima = armar_textos_clima(datos_clima)
    ambiente_nombre = elegir_ambiente(datos_clima)

    print("Consultando y verificando noticias...")
    titular_mundo = obtener_titular_verificado(FEEDS_MUNDO, 20)
    titular_musica = obtener_titular_verificado(FEEDS_MUSICA, 20)
    texto_noticias, texto_remate = armar_textos_noticias_y_remate(titular_mundo, titular_musica)

    print("Generando spot con procesador V9 Duo Imaging...")
    generar_spot(tag_ini, texto_clima, texto_noticias, texto_remate, ambiente_nombre, LOCAL_MP3)

    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
