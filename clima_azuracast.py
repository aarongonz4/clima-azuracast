#!/usr/bin/env python3
"""
Spot automático de clima y noticias para AzuraCast (Nogaradio) - Edición FM Moderna (Sound Design Pro).

Qué incluye:
  - Síntesis avanzada (Supersaw estéreo, síntesis FM, Sub-drops, Whooshes con filtro resonante y Glitches).
  - Texturas electrónicas dinámicas según el clima (sin grillos ni pájaros artificiales: usa Pads, Plucks y Synths).
  - Bed rítmico automático estilo "News Ticker" (pulso de noticiero moderno a 125 BPM) bajo las noticias.
  - Procesamiento vocal Broadcast (EQ + Compresión + Exciter) y Sidechain Ducking automático.
  - Sigue aceptando tus propios MP3/WAV en la carpeta assets/ si algún día querés pisar los sintetizados.
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

CIUDAD = "Resistencia, Argentina"
LAT = -27.4514
LON = -58.9867
ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")

LOCAL_MP3 = "clima.mp3"
REMOTE_FILENAME = "clima.mp3"

RSS_MUNDO = "https://feeds.bbci.co.uk/mundo/rss.xml"
RSS_MUSICA = "https://www.planeta.pe/noticias/musica/feed.xml"

ASSETS_DIR = "assets"
EXTENSIONES = (".mp3", ".wav", ".ogg", ".m4a", ".flac")
SR = 44100

# Niveles de mezcla en dB
REFERENCIA_RMS_DB = -20.0
EFECTOS_DB = -3.5          # Sweepers (intro, transición, cierre) bien presentes
AMBIENTE_DB = -14.5        # Textura electrónica de clima bajo el locutor
TICKER_NOTICIAS_DB = -15.5 # Pulso rítmico de noticiero bajo las noticias
CAMA_DB_LIBRE = -5.0       # Cama externa (si subís assets/cama.mp3) cuando no hablan
CAMA_DB_VOZ = -14.0        # Cama externa bajo la voz

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
        return random.choice(["Buenos días", "Muy buenos días", "Hola, buenos días", "Excelente mañana"])
    elif 12 <= hora_actual < 20:
        return random.choice(["Buenas tardes", "Muy buenas tardes", "Hola, buenas tardes", "Excelente tarde"])
    return random.choice(["Buenas noches", "Muy buenas noches", "Excelente noche", "Hola, buenas noches"])


# ---------------------------------------------------------------------------
# Clima y Noticias
# ---------------------------------------------------------------------------

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


PLANTILLAS_CLIMA = [
    "{saludo}. En Argentina son las {hora} horas. Nogaradio te cuenta cómo está el tiempo en Resistencia: {descripcion}, con {temp} grados y sensación térmica de {sensacion} grados. Humedad del {humedad} por ciento y vientos de {viento} kilómetros por hora. Ahora los dejamos con las noticias.",
    "{saludo}. En Argentina son las {hora} horas. Este es el informe del tiempo de Nogaradio. En Resistencia tenemos {descripcion}, {temp} grados de temperatura y una sensación térmica de {sensacion} grados. La humedad es del {humedad} por ciento. A continuación, el resumen de noticias.",
    "Nogaradio te informa. En Argentina son las {hora} horas. Así está el clima en Resistencia: {descripcion}, {temp} grados de temperatura y {sensacion} grados de sensación térmica. Humedad del {humedad} por ciento y viento de {viento} kilómetros por hora. Pasamos a la información.",
]


def armar_texto_clima(data: dict | None) -> str:
    ahora = datetime.now(ZONA_HORARIA)
    hora_str = hora_en_espanol(ahora.hour, ahora.minute)
    saludo = saludo_segun_hora(ahora.hour)

    if not data or "current" not in data:
        return f"{saludo}. En Argentina son las {hora_str} horas. Nogaradio te acompaña en cada momento. A continuación, pasamos al resumen de noticias."

    actual = data["current"]
    temp = round(actual["temperature_2m"])
    sensacion = round(actual["apparent_temperature"])
    humedad = round(actual["relative_humidity_2m"])
    viento = round(actual["wind_speed_10m"])
    descripcion = CODIGOS_CLIMA.get(actual["weather_code"], "condiciones variables")

    plantilla = random.choice(PLANTILLAS_CLIMA)
    return plantilla.format(saludo=saludo, hora=hora_str, descripcion=descripcion, temp=temp,
                            sensacion=sensacion, humedad=humedad, viento=viento)


def elegir_ambiente(data: dict | None) -> str:
    """Elige el perfil de diseño sonoro electrónico según el estado del clima y la hora."""
    hora = datetime.now(ZONA_HORARIA).hour
    es_dia = 6 <= hora < 20

    if not data or "current" not in data:
        return "dia" if es_dia else "noche"

    actual = data["current"]
    codigo = actual.get("weather_code", 0)
    temp = actual.get("temperature_2m", 24)

    if codigo in (95, 96, 99):
        return "tormenta"
    if codigo in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "lluvia"
    if codigo in (3, 45, 48, 71, 73, 75):
        return "nublado"
    if not es_dia:
        return "noche"
    if temp is not None and temp >= 30:
        return "calor"
    return "dia"


def limpiar_texto_rss(texto: str) -> str:
    texto_limpio = re.sub(r'<[^>]+>', '', texto)
    texto_limpio = texto_limpio.replace("&nbsp;", " ")
    return " ".join(texto_limpio.strip().split())


def obtener_titular(url_feed: str, cantidad: int = 15) -> str | None:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        resp = requests.get(url_feed, headers=headers, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)

        entradas_validas = []
        for entry in feed.entries[:cantidad]:
            titulo = limpiar_texto_rss(getattr(entry, "title", ""))
            descripcion = limpiar_texto_rss(getattr(entry, "description", ""))
            if titulo:
                noticia_completa = f"{titulo}. {descripcion}" if descripcion else titulo
                entradas_validas.append(noticia_completa)

        if not entradas_validas:
            return None

        ahora = datetime.now(ZONA_HORARIA)
        return entradas_validas[ahora.hour % len(entradas_validas)]
    except Exception as e:
        print(f"Error al leer el feed {url_feed}: {e}", file=sys.stderr)
        return None


def armar_texto_noticias(titular_mundo: str | None, titular_musica: str | None) -> str:
    partes = []
    if titular_mundo and titular_musica:
        partes.append(f"En la actualidad: {titular_mundo}. Y en música: {titular_musica}. Ya estás informado. Seguimos con más música en Nogaradio.")
    elif titular_mundo:
        partes.append(f"En la actualidad: {titular_mundo}. Ya estás informado. Seguimos con más música en Nogaradio.")
    elif titular_musica:
        partes.append(f"Una novedad del mundo de la música: {titular_musica}. Seguimos con más música en Nogaradio.")
    return " ".join(partes)


# ---------------------------------------------------------------------------
# Motor DSP y Síntesis Pro (FM & Supersaw)
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


def _comprimir(x: np.ndarray, thr_db: float = -20.0, ratio: float = 3.0,
               ataque_ms: float = 6.0, relajo_ms: float = 120.0, bloque_ms: float = 5.0) -> np.ndarray:
    n = x.shape[0]
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


def _reverb_estereo(x: np.ndarray, seg: float = 1.1, mezcla: float = 0.38, brillo: float = 5500.0) -> np.ndarray:
    """Reverb espacial estéreo con pre-delay de 18ms para sonido amplio de estudio."""
    ir_n = int(SR * seg)
    t = np.arange(ir_n) / SR
    rng = np.random.default_rng(42)
    ir = rng.standard_normal((ir_n, 2)) * np.exp(-6.5 * t / seg)[:, None]
    ir = _filtrar(ir, pasa_altos=180, pasa_bajos=brillo)
    predelay = int(SR * 0.018)
    ir = np.pad(ir, ((predelay, 0), (0, 0)))[:ir_n]
    ir /= (np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True)) + 1e-9)

    largo = x.shape[0] + ir_n
    nfft = 1 << (largo - 1).bit_length()
    humedo = np.fft.irfft(np.fft.rfft(x, nfft, axis=0) * np.fft.rfft(ir, nfft, axis=0), nfft, axis=0)[:largo]
    seco = np.pad(x, ((0, ir_n), (0, 0)))
    return seco + humedo * mezcla


# --- Sintetizadores de Radio Imaging ---

def _supersaw(freq: float, dur_ms: float, decay: float = 3.5, detune: float = 0.012, corte_lp: float = 5000.0) -> np.ndarray:
    """Sintetizador Supersaw estéreo (5 voces desafinadas) típico de jingles de radio pop/dance."""
    t = _t(dur_ms)
    factores = [1.0 - detune, 1.0 - detune * 0.45, 1.0, 1.0 + detune * 0.45, 1.0 + detune]
    pan_l = [0.9, 0.7, 0.5, 0.3, 0.1]
    pan_r = [0.1, 0.3, 0.5, 0.7, 0.9]
    out = np.zeros((len(t), 2))
    rng = np.random.default_rng(int(freq * 10))

    for f_mul, pl, pr in zip(factores, pan_l, pan_r):
        f = freq * f_mul
        fase_ini = rng.uniform(0, 1)
        # Onda diente de sierra + sub-octava cuadrada suave
        saw = 2.0 * ((t * f + fase_ini) % 1.0) - 1.0
        out[:, 0] += saw * pl
        out[:, 1] += saw * pr

    env = np.exp(-decay * t)
    ataque = min(int(SR * 0.006), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    out = _filtrar(out * env[:, None], pasa_altos=90, pasa_bajos=corte_lp)
    return np.tanh(out * 0.6) * 0.45


def _fm_bell(freq: float, dur_ms: float, decay: float = 5.0, mod_idx: float = 2.8) -> np.ndarray:
    """Síntesis FM cristalina (estilo Yamaha DX7 / Marimba electrónica de noticiero)."""
    t = _t(dur_ms)
    mod_freq = freq * 2.0
    env_mod = np.exp(-decay * 2.2 * t)
    env_amp = np.exp(-decay * t)
    ataque = min(int(SR * 0.003), len(t))
    env_amp[:ataque] *= np.linspace(0, 1, ataque)

    modulador = mod_idx * env_mod * np.sin(2 * np.pi * mod_freq * t)
    portadora_l = np.sin(2 * np.pi * freq * t + modulador)
    portadora_r = np.sin(2 * np.pi * (freq * 1.002) * t + modulador * 0.95)
    return np.column_stack((portadora_l, portadora_r)) * env_amp[:, None] * 0.35


def _sub_drop(dur_ms: float = 1500, f_ini: float = 95.0, f_fin: float = 32.0) -> np.ndarray:
    """Impacto grave de cine/radio con caída exponencial de frecuencia y punch transiente."""
    t = _t(dur_ms)
    f_curva = f_fin + (f_ini - f_fin) * np.exp(-t * 6.5)
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    cuerpo = np.tanh(np.sin(fase) * 1.8) * np.exp(-t * 2.2)

    # Click/Punch inicial en los primeros 35ms
    punch = np.sin(2 * np.pi * 160 * t) * np.exp(-t * 45) * 0.7
    ruido_impacto = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=120, pasa_bajos=4500)
    ruido_impacto *= np.exp(-t * 18)[:, None] * 0.45

    return _fade(_est(cuerpo * 0.85 + punch) + ruido_impacto, 2, 120)


def _whoosh_pro(dur_ms: float = 900, f_ini: float = 250.0, f_fin: float = 4800.0) -> np.ndarray:
    """Barrido aerodinámico estéreo con paneo y barrido de frecuencia real."""
    t = _t(dur_ms)
    n = len(t)
    prog = t / (dur_ms / 1000.0)

    # Chirp resonante + ruido filtrado por tramos
    f_curva = f_ini * ((f_fin / f_ini) ** (prog ** 1.6))
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    laser_suave = np.sin(fase) * 0.22

    ruido = np.random.uniform(-1, 1, (n, 2))
    ruido_grave = _filtrar(ruido, pasa_altos=150, pasa_bajos=1200)
    ruido_agudo = _filtrar(ruido, pasa_altos=1200, pasa_bajos=8500)
    mezcla_ruido = ruido_grave * (1 - prog)[:, None] + ruido_agudo * (prog ** 1.5)[:, None]

    # Envolvente con pico al 80% de la duración
    env = np.sin(np.pi * (prog ** 1.3)) ** 2
    pan_l = 0.3 + 0.7 * prog
    pan_r = 1.0 - 0.7 * prog

    out = mezcla_ruido * 0.65 + np.column_stack((laser_suave * pan_l, laser_suave * pan_r))
    return _pico(out * env[:, None], 0.85)


def _glitch_stutter(dur_ms: float = 280) -> np.ndarray:
    """Efecto de telemetría/stutter digital moderno antes del impacto."""
    pista = _pista(dur_ms)
    paso = 45
    freqs = [1800, 2400, 1600, 3200, 2100, 2800]
    for i, pos in enumerate(range(0, int(dur_ms) - paso, paso)):
        f = freqs[i % len(freqs)]
        t = _t(30)
        bip = np.sin(2 * np.pi * f * t) * np.hanning(len(t)) * 0.28
        estereo = np.column_stack((bip * (i % 2), bip * ((i + 1) % 2))) + _est(bip * 0.3)
        _sumar(pista, estereo, pos)
    return pista


# ---------------------------------------------------------------------------
# Sweepers Sintetizados (Intro, Transición, Cierre)
# ---------------------------------------------------------------------------

def sintetizar_intro() -> np.ndarray:
    """Intro estilo FM Top 40: Glitch -> Whoosh + Riser -> Sub-drop -> Acorde Supersaw + Arpegio FM."""
    pista = _pista(2600)
    _sumar(pista, _glitch_stutter(300), 100, 0.55)
    _sumar(pista, _whoosh_pro(850, 200, 5200), 50, 0.85)
    _sumar(pista, _sub_drop(1600, 100, 32), 820, 0.95)

    # Acorde Maj9 moderno en Supersaw (Do Mayor 9: C4, E4, G4, B4, D5)
    for f in [261.63, 329.63, 392.00, 493.88, 587.33]:
        _sumar(pista, _supersaw(f, 1500, decay=2.4, corte_lp=4200), 820, 0.25)

    # Logo sonoro (5 notas FM brillantes rápidas)
    notas_logo = [523.25, 659.25, 783.99, 987.77, 1174.66]  # C5, E5, G5, B5, D6
    for i, f in enumerate(notas_logo):
        dur = 650 if i < 4 else 1100
        _sumar(pista, _fm_bell(f, dur, decay=4.0 if i < 4 else 2.5), 840 + i * 95, 0.65)

    return _fade(_reverb_estereo(pista, seg=1.1, mezcla=0.35), 10, 350)


def sintetizar_transicion() -> np.ndarray:
    """Separador informativo rápido (Stinger de noticias): Whoosh corto + Sub + Pulso de 4 notas."""
    pista = _pista(1700)
    _sumar(pista, _whoosh_pro(480, 3500, 300), 0, 0.75)
    _sumar(pista, _sub_drop(1100, 90, 35), 180, 0.85)

    # Firma de noticiero (A4 - E5 - A5 - B5) con sonido FM percusivo
    for i, f in enumerate([440.0, 659.25, 880.0, 987.77]):
        _sumar(pista, _fm_bell(f, 550, decay=5.5, mod_idx=3.5), 180 + i * 85, 0.75)
        _sumar(pista, _supersaw(f / 2, 450, decay=6.0, corte_lp=2800), 180 + i * 85, 0.22)

    return _fade(_reverb_estereo(pista, seg=0.85, mezcla=0.32), 10, 250)


def sintetizar_cierre() -> np.ndarray:
    """Cierre radial moderno: Acorde descendente luminoso + impacto grave final."""
    pista = _pista(2000)
    notas_cierre = [1174.66, 987.77, 783.99, 523.25]  # D6 -> B5 -> G5 -> C5
    for i, f in enumerate(notas_cierre):
        _sumar(pista, _fm_bell(f, 900 if i < 3 else 1400, decay=3.2), i * 110, 0.7)

    # Acorde cálido final en el último golpe
    _sumar(pista, _whoosh_pro(450, 400, 3800), 0, 0.5)
    _sumar(pista, _sub_drop(1400, 85, 30), 330, 0.9)
    for f in [261.63, 329.63, 392.00, 523.25]:
        _sumar(pista, _supersaw(f, 1400, decay=2.8, corte_lp=3500), 330, 0.25)

    return _fade(_reverb_estereo(pista, seg=1.2, mezcla=0.4), 10, 500)


# ---------------------------------------------------------------------------
# Camas Electrónicas según el Clima + Bed Rítmico de Noticias
# ---------------------------------------------------------------------------

def bed_clima_electronico(modo: str, dur_ms: float) -> np.ndarray:
    """
    En vez de imitar pájaros o grillos, genera una cama musical electrónica (Ambient/Synth)
    acorde a la energía del clima actual.
    """
    pista = _pista(dur_ms)
    t = _t(dur_ms)

    if modo in ("tormenta", "lluvia"):
        # Pulso electrónico tenso (estilo Weather Channel moderno) + textura de lluvia filtrada suave
        acorde = [110.0, 130.81, 164.81, 196.0]  # Am7 grave
        for f in acorde:
            pad = _supersaw(f, dur_ms, decay=0.05, corte_lp=900)
            pista += pad * 0.22
        # Pulso de bajo en semicorcheas (120 BPM -> cada 250ms)
        for pos in range(0, int(dur_ms) - 250, 250):
            pulso = _fm_bell(110.0 if (pos // 250) % 2 == 0 else 220.0, 180, decay=14.0, mod_idx=1.8)
            _sumar(pista, pulso, pos, 0.35)
        # Textura de lluvia de alta frecuencia muy sutil y controlada
        lluvia_fina = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=2800, pasa_bajos=7500) * 0.08
        pista += lluvia_fina
        if modo == "tormenta":
            _sumar(pista, _sub_drop(1800, 75, 28), dur_ms * 0.25, 0.45)
            _sumar(pista, _sub_drop(1800, 75, 28), dur_ms * 0.68, 0.45)

    elif modo == "calor":
        # Groove electrónico radiante y veraniego (acordes Pluck sincopados)
        acorde = [261.63, 329.63, 392.00, 493.88]  # Cmaj7
        for f in acorde:
            pista += _supersaw(f, dur_ms, decay=0.05, corte_lp=1600) * 0.16
        arp = [523.25, 659.25, 783.99, 987.77, 783.99, 659.25]
        for i, pos in enumerate(range(0, int(dur_ms) - 220, 220)):
            nota = _fm_bell(arp[i % len(arp)], 200, decay=10.0, mod_idx=1.2)
            _sumar(pista, nota, pos, 0.28)

    elif modo == "noche":
        # Synthwave / Chill nocturno profundo y elegante
        acorde = [130.81, 196.00, 246.94, 293.66]  # Cmaj9 grave
        for f in acorde:
            pista += _supersaw(f, dur_ms, decay=0.04, corte_lp=1100) * 0.22
        notas_noche = [392.00, 493.88, 587.33, 659.25]
        for i, pos in enumerate(range(0, int(dur_ms) - 450, 450)):
            _sumar(pista, _fm_bell(notas_noche[i % len(notas_noche)], 420, decay=6.0, mod_idx=0.8), pos, 0.22)

    else:
        # "dia" o "nublado": Pad cálido de radio FM con arpegio suave
        corte = 1800 if modo == "dia" else 1000
        acorde = [220.0, 277.18, 329.63, 415.30] if modo == "dia" else [196.0, 246.94, 293.66, 369.99]
        for f in acorde:
            pista += _supersaw(f, dur_ms, decay=0.04, corte_lp=corte) * 0.20
        arp = [440.0, 554.37, 659.25, 880.0]
        for i, pos in enumerate(range(0, int(dur_ms) - 320, 320)):
            _sumar(pista, _fm_bell(arp[i % len(arp)], 280, decay=8.0, mod_idx=1.0), pos, 0.24)

    # Modulación suave (efecto Sidechain / Pumping ligero)
    bombeo = 0.78 + 0.22 * np.sin(2 * np.pi * 2.0 * t)
    return _reverb_estereo(pista * bombeo[:, None], seg=0.9, mezcla=0.3)[:len(t)]


def bed_noticias_ticker(dur_ms: float) -> np.ndarray:
    """
    Genera una cortina rítmica moderna de informativo radial (125 BPM):
    Pulso electrónico grave + teletipo/hi-hat estéreo sutil.
    """
    pista = _pista(dur_ms)
    paso_corchea = 240  # 125 BPM (negra = 480ms, corchea = 240ms)

    # Colchón grave de tensión informativa (La menor)
    pista += _supersaw(110.0, dur_ms, decay=0.02, corte_lp=650) * 0.22
    pista += _supersaw(164.81, dur_ms, decay=0.02, corte_lp=850) * 0.15

    # Pulso rítmico y ticks de reloj/teletipo moderno
    secuencia_f = [220.0, 220.0, 329.63, 220.0, 261.63, 220.0, 329.63, 246.94]
    for i, pos in enumerate(range(0, int(dur_ms) - paso_corchea, paso_corchea)):
        f = secuencia_f[i % len(secuencia_f)]
        pulso = _fm_bell(f, 160, decay=15.0, mod_idx=2.0)
        _sumar(pista, pulso, pos, 0.35)

        # Tick de alta precisión paneado izq/der
        t_tick = _t(25)
        tick = _filtrar(np.random.uniform(-1, 1, (len(t_tick), 2)), pasa_altos=5000, pasa_bajos=11000)
        tick *= np.exp(-t_tick * 140)[:, None] * 0.25
        if i % 2 == 0:
            tick[:, 0] *= 0.3
        else:
            tick[:, 1] *= 0.3
        _sumar(pista, tick, pos)

    return pista


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
        ini = random.randint(0, len(x) - n)
        return x[ini:ini + n].copy()
    cf = max(1, min(int(SR * 0.5), len(x) // 3))
    salida = x.copy()
    while len(salida) < n:
        fade = np.linspace(0, 1, cf)[:, None]
        salida[-cf:] = salida[-cf:] * (1 - fade) + x[:cf] * fade
        salida = np.vstack((salida, x[cf:]))
    return salida[:n]


def cargar_efecto(nombre: str, sintetizador) -> np.ndarray:
    seg = buscar_asset(nombre)
    x = seg_a_array(seg) if seg is not None else sintetizador()
    return _pico(x, 0.92)


def construir_ambiente(nombre: str, dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset(f"clima/{nombre}")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print(f"Sintetizando bed electrónico de clima: {nombre}")
        base = bed_clima_electronico(nombre, dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 500, 1000)


def construir_cama_noticias(dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset("noticias_cama")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print("Sintetizando bed rítmico de noticias (News Ticker)")
        base = bed_noticias_ticker(dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 400, 800)


def cargar_pista_opcional(base: str) -> np.ndarray | None:
    seg = buscar_asset(base)
    if seg is None:
        return None
    return _rms(seg_a_array(seg), REFERENCIA_RMS_DB)


# ---------------------------------------------------------------------------
# Voces (Google Cloud TTS) + Procesamiento Broadcast FM
# ---------------------------------------------------------------------------

def sintetizar_voz(client, texto: str, nombre_voz: str, audio_config) -> np.ndarray:
    entrada = texttospeech.SynthesisInput(text=texto)
    voz = texttospeech.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
    resp = client.synthesize_speech(input=entrada, voice=voz, audio_config=audio_config)
    seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
    return seg_a_array(seg)


def procesar_voz(x: np.ndarray) -> np.ndarray:
    """Procesador vocal tipo FM (Filtro subgrave + Cuerpo 160Hz + Presencia 3.5kHz + Aire + Compresión)."""
    x = _filtrar(x, pasa_altos=85)
    cuerpo = _filtrar(x, pasa_altos=110, pasa_bajos=240)
    presencia = _filtrar(x, pasa_altos=2400, pasa_bajos=6000)
    aire = np.tanh(_filtrar(x, pasa_altos=6500) * 2.0) * 0.12
    x = x + 0.20 * cuerpo + 0.28 * presencia + aire
    x = _comprimir(x, thr_db=-23.0, ratio=3.5, ataque_ms=5.0, relajo_ms=130.0)
    return _pico(x, 0.88)


# ---------------------------------------------------------------------------
# Ensamblado y Masterización Final
# ---------------------------------------------------------------------------

def ensamblar_spot(voz_clima, voz_noticias, intro, trans, cierre, ambiente, cama, cama_noticias) -> np.ndarray:
    # La voz del clima pisa la cola reverberante de la intro como en radio real
    inicio_clima = max(1450.0, _dur_ms(intro) - 1100.0)
    fin_clima = inicio_clima + _dur_ms(voz_clima)

    capas = [(intro, 0.0, EFECTOS_DB), (voz_clima, inicio_clima, 0.0)]
    tramos_voz = [(inicio_clima, fin_clima)]

    if ambiente is not None:
        capas.append((ambiente, max(0.0, inicio_clima - 400.0), AMBIENTE_DB))

    fin_voces = fin_clima
    inicio_not = None
    if voz_noticias is not None:
        inicio_trans = fin_clima - 100.0
        inicio_not = inicio_trans + max(850.0, _dur_ms(trans) - 750.0)
        fin_not = inicio_not + _dur_ms(voz_noticias)
        capas += [(trans, inicio_trans, EFECTOS_DB), (voz_noticias, inicio_not, 0.0)]
        tramos_voz.append((inicio_not, fin_not))
        fin_voces = fin_not

        if cama_noticias is not None:
            capas.append((cama_noticias, inicio_not - 300.0, TICKER_NOTICIAS_DB))

    inicio_cierre = fin_voces - 200.0
    capas.append((cierre, inicio_cierre, EFECTOS_DB))
    total_ms = inicio_cierre + _dur_ms(cierre)

    master = _pista(total_ms + 400)
    for x, ini, db in capas:
        _sumar(master, x, ini, _g(db))

    # Si subiste un assets/cama.mp3 general, se suma con sidechain ducking
    if cama is not None:
        puntos = [(0.0, CAMA_DB_LIBRE)]
        for ini, fin in tramos_voz:
            puntos += [(ini - 150, CAMA_DB_LIBRE), (ini + 300, CAMA_DB_VOZ),
                       (fin - 100, CAMA_DB_VOZ), (fin + 350, CAMA_DB_LIBRE)]
        puntos += [(total_ms - 1000, CAMA_DB_LIBRE), (total_ms, -60.0)]
        puntos.sort(key=lambda p: p[0])
        eje_ms = np.arange(len(master)) * 1000.0 / SR
        env_db = np.interp(eje_ms, [p[0] for p in puntos], [p[1] for p in puntos])
        master += _extender(cama, len(master)) * _g_array(env_db)[:, None]

    return master[: int(SR * total_ms / 1000)]


def masterizar(x: np.ndarray) -> np.ndarray:
    """Compresor bus master + limitador suave para volumen competitivo en AzuraCast."""
    x = _filtrar(x, pasa_altos=35)
    x = _comprimir(x, thr_db=-15.0, ratio=2.8, ataque_ms=12.0, relajo_ms=180.0)
    x = np.tanh(x * 1.15)
    return _pico(x, 0.92)


def generar_spot(texto_clima: str, texto_noticias: str, ambiente_nombre: str, path_salida: str) -> None:
    client = texttospeech.TextToSpeechClient()
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    ahora = datetime.now(ZONA_HORARIA)

    if ahora.hour % 2 == 0:
        voz_clima_nombre, voz_noticias_nombre = "es-US-Neural2-A", "es-US-Neural2-B"
        print("Turno (hora par): Mujer (Clima) -> Hombre (Noticias)")
    else:
        voz_clima_nombre, voz_noticias_nombre = "es-US-Neural2-B", "es-US-Neural2-A"
        print("Turno (hora impar): Hombre (Clima) -> Mujer (Noticias)")

    voz_clima = procesar_voz(sintetizar_voz(client, texto_clima, voz_clima_nombre, audio_config))
    voz_noticias = None
    if texto_noticias:
        voz_noticias = procesar_voz(sintetizar_voz(client, texto_noticias, voz_noticias_nombre, audio_config))

    intro = cargar_efecto("intro", sintetizar_intro)
    trans = cargar_efecto("transicion", sintetizar_transicion)
    cierre = cargar_efecto("cierre", sintetizar_cierre)

    ambiente = construir_ambiente(ambiente_nombre, _dur_ms(voz_clima) + 1200)
    cama = cargar_pista_opcional("cama")
    cama_noticias = construir_cama_noticias(_dur_ms(voz_noticias) + 900) if voz_noticias is not None else None

    mezcla = ensamblar_spot(voz_clima, voz_noticias, intro, trans, cierre, ambiente, cama, cama_noticias)
    final = masterizar(mezcla)
    array_a_seg(final).export(path_salida, format="mp3", bitrate="192k")
    print(f"¡Spot final ({len(final) / SR:.1f} s) guardado exitosamente en {path_salida}!")


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
    texto_clima = armar_texto_clima(datos_clima)
    ambiente_nombre = elegir_ambiente(datos_clima)

    print("Consultando noticias...")
    titular_mundo = obtener_titular(RSS_MUNDO, 15)
    titular_musica = obtener_titular(RSS_MUSICA, 15)
    texto_noticias = armar_texto_noticias(titular_mundo, titular_musica)

    print("Generando spot con diseño sonoro FM...")
    generar_spot(texto_clima, texto_noticias, ambiente_nombre, LOCAL_MP3)

    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
