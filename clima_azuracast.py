#!/usr/bin/env python3
"""
Spot automático de clima y noticias para AzuraCast (Nogaradio) - Edición FM Pop/Urbana Natural.

Mejoras de esta versión:
  - Groove musical real: Sintetiza batería electrónica suave (Kick, Clap, Hi-Hats), Bajo Sub y Piano Eléctrico (Rhodes).
  - Sidechain natural: Los acordes respiran al ritmo del pulso como en las cortinas de radio modernas.
  - Sweepers orgánicos: Reverse Swells, impactos profundos redondos y notas con Stereo Delay (eco espacial) en vez de pitidos.
  - Locución con SSML: Pausas naturales de respiración y entonación fluida en Google Cloud TTS.
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

# Niveles de mezcla en dB (ajustados para que la música acompañe sin tapar la voz)
REFERENCIA_RMS_DB = -20.0
EFECTOS_DB = -4.0          # Sweepers (intro, transición, cierre)
AMBIENTE_DB = -13.5        # Groove musical del clima bajo la voz
TICKER_NOTICIAS_DB = -14.5 # Groove informativo bajo las noticias
CAMA_DB_LIBRE = -5.0       # Cama externa (si subís assets/cama.mp3)
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
# Clima y Noticias (con pausas naturales SSML)
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
    "{saludo}. En Argentina son las {hora}. Nogaradio te cuenta cómo está el tiempo en Resistencia: tenemos {descripcion}, con {temp} grados y una sensación térmica de {sensacion}. La humedad es del {humedad} por ciento. Quedate con nosotros, que ya llegan las noticias.",
    "{saludo}, ¿cómo estás? Pasaron {hora} en todo el país. Así está el tiempo ahora en Resistencia: {descripcion}, {temp} grados de temperatura y {sensacion} de sensación térmica. Humedad del {humedad} por ciento. A continuación, compartimos el flash informativo.",
    "Nogaradio te informa. Son las {hora} en Argentina. Actualizamos los datos del tiempo para Resistencia: cielo con {descripcion}, temperatura de {temp} grados, térmica de {sensacion} y vientos de {viento} kilómetros por hora. Ahora sí, pasamos a las noticias.",
]


def armar_texto_clima(data: dict | None) -> str:
    ahora = datetime.now(ZONA_HORARIA)
    hora_str = hora_en_espanol(ahora.hour, ahora.minute)
    saludo = saludo_segun_hora(ahora.hour)

    if not data or "current" not in data:
        return f"{saludo}. En Argentina son las {hora_str}. Nogaradio te acompaña en cada momento. A continuación, pasamos al resumen de noticias."

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
    hora = datetime.now(ZONA_HORARIA).hour
    es_dia = 6 <= hora < 20

    if not data or "current" not in data:
        return "dia" if es_dia else "noche"

    actual = data["current"]
    codigo = actual.get("weather_code", 0)
    temp = actual.get("temperature_2m", 24)

    if codigo in (95, 96, 99, 51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "lluvia"
    if not es_dia:
        return "noche"
    if temp is not None and temp >= 30:
        return "calor"
    return "dia"


def limpiar_texto_rss(texto: str) -> str:
    texto_limpio = re.sub(r'<[^>]+>', '', texto)
    texto_limpio = texto_limpio.replace("&nbsp;", " ").replace("&amp;", "y").replace("&quot;", "")
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
    if titular_mundo and titular_musica:
        return f"En el ámbito internacional: {titular_mundo}. Pasando a la música: {titular_musica}. Hasta aquí las noticias, seguí conectado a Nogaradio."
    elif titular_mundo:
        return f"Actualidad internacional: {titular_mundo}. Seguimos con mucha más música en Nogaradio."
    elif titular_musica:
        return f"Novedades en la música: {titular_musica}. Seguimos acompañándote en Nogaradio."
    return ""


# ---------------------------------------------------------------------------
# Utilidades DSP y Efectos de Estudio (Delay, Reverb, Filtros)
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


def _stereo_delay(x: np.ndarray, delay_l_ms: float = 240.0, delay_r_ms: float = 360.0, feedback: float = 0.32, mezcla: float = 0.28) -> np.ndarray:
    """Eco Ping-Pong suave para que los acordes y notas queden flotando naturalmente."""
    out = x.copy()
    dl = int(SR * delay_l_ms / 1000)
    dr = int(SR * delay_r_ms / 1000)
    eco = _filtrar(x, pasa_altos=250, pasa_bajos=3800)
    for rep in range(1, 4):
        gan = (feedback ** rep) * mezcla
        desp_l = rep * dl
        desp_r = rep * dr
        if desp_l < len(out):
            out[desp_l:, 0] += eco[:-desp_l, 1] * gan
        if desp_r < len(out):
            out[desp_r:, 1] += eco[:-desp_r, 0] * gan
    return out


def _reverb_estereo(x: np.ndarray, seg: float = 1.2, mezcla: float = 0.30, brillo: float = 4800.0) -> np.ndarray:
    ir_n = int(SR * seg)
    t = np.arange(ir_n) / SR
    rng = np.random.default_rng(77)
    ir = rng.standard_normal((ir_n, 2)) * np.exp(-6.2 * t / seg)[:, None]
    ir = _filtrar(ir, pasa_altos=200, pasa_bajos=brillo)
    predelay = int(SR * 0.020)
    ir = np.pad(ir, ((predelay, 0), (0, 0)))[:ir_n]
    ir /= (np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True)) + 1e-9)

    largo = x.shape[0] + ir_n
    nfft = 1 << (largo - 1).bit_length()
    humedo = np.fft.irfft(np.fft.rfft(x, nfft, axis=0) * np.fft.rfft(ir, nfft, axis=0), nfft, axis=0)[:largo]
    seco = np.pad(x, ((0, ir_n), (0, 0)))
    return seco + humedo * mezcla


# ---------------------------------------------------------------------------
# Instrumentos Musicales Orgánicos (Rhodes, Warm Pad, Bajo, Batería)
# ---------------------------------------------------------------------------

def _rhodes_chord(freqs: list[float], dur_ms: float, decay: float = 2.2) -> np.ndarray:
    """Acorde de Piano Eléctrico cálido (estilo Fender Rhodes) con Chorus y Tremolo estéreo."""
    t = _t(dur_ms)
    out = np.zeros((len(t), 2))
    ataque = min(int(SR * 0.008), len(t))

    for i, f in enumerate(freqs):
        # Timbre cálido: fundamental + 2do armónico suave + golpe de púa ("tine") que cae rápido
        tine = 0.25 * np.sin(2 * np.pi * f * 3.0 * t) * np.exp(-t * 18.0)
        cuerpo_l = np.sin(2 * np.pi * f * t + tine) + 0.15 * np.sin(2 * np.pi * f * 2.0 * t)
        cuerpo_r = np.sin(2 * np.pi * (f * 1.0015) * t + tine) + 0.15 * np.sin(2 * np.pi * (f * 2.003) * t)

        env = np.exp(-decay * t)
        env[:ataque] *= np.linspace(0, 1, ataque)
        pan = 0.35 + 0.3 * (i / max(1, len(freqs) - 1))
        out[:, 0] += cuerpo_l * env * pan
        out[:, 1] += cuerpo_r * env * (1.0 - pan)

    # Tremolo estéreo suave (4.5 Hz) clásico de piano eléctrico
    trem_l = 0.88 + 0.12 * np.sin(2 * np.pi * 4.5 * t)
    trem_r = 0.88 + 0.12 * np.cos(2 * np.pi * 4.5 * t)
    out[:, 0] *= trem_l
    out[:, 1] *= trem_r
    return _filtrar(out, pasa_altos=100, pasa_bajos=3600) * 0.28


def _warm_pad(freqs: list[float], dur_ms: float, corte_lp: float = 1400.0) -> np.ndarray:
    """Colchón analógico cálido y aterciopelado (sin agudos chillones)."""
    t = _t(dur_ms)
    out = np.zeros((len(t), 2))
    rng = np.random.default_rng(12)
    for f in freqs:
        for det, pl, pr in [(-0.007, 0.8, 0.2), (0.0, 0.5, 0.5), (0.007, 0.2, 0.8)]:
            f_actual = f * (1.0 + det)
            fase = rng.uniform(0, 1)
            saw = 2.0 * ((t * f_actual + fase) % 1.0) - 1.0
            sub = np.sin(2 * np.pi * (f_actual * 0.5) * t) * 0.4
            onda = saw * 0.6 + sub
            out[:, 0] += onda * pl
            out[:, 1] += onda * pr
    out = _filtrar(out, pasa_altos=90, pasa_bajos=corte_lp)
    return _fade(out * 0.12, 300, 400)


def _sub_bass_note(freq: float, dur_ms: float) -> np.ndarray:
    """Nota de bajo electrónico profundo y redondo."""
    t = _t(dur_ms)
    onda = np.tanh((np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t)) * 1.4)
    env = np.exp(-3.5 * t)
    ataque = min(int(SR * 0.010), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    return _est(_filtrar(onda * env, pasa_bajos=220)[:, 0] if onda.ndim > 1 else onda * env) * 0.38


def _kick_pro(dur_ms: float = 260) -> np.ndarray:
    """Bombo (Kick) de música Pop/Deep House: cálido, profundo y con golpe suave."""
    t = _t(dur_ms)
    f_env = 46.0 + 115.0 * np.exp(-t * 36.0)
    fase = 2 * np.pi * np.cumsum(f_env) / SR
    cuerpo = np.tanh(np.sin(fase) * 1.5) * np.exp(-t * 10.5)
    click = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=800, pasa_bajos=3500)
    click *= np.exp(-t * 95.0)[:, None] * 0.18
    return _est(cuerpo * 0.75) + click


def _hihat_suave(dur_ms: float = 70, abierto: bool = False) -> np.ndarray:
    """Hi-hat / Shaker orgánico filtrado para dar ritmo sin molestar a la voz."""
    dur = 190 if abierto else dur_ms
    t = _t(dur)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=6200, pasa_bajos=12500)
    decay = 16.0 if abierto else 48.0
    env = np.exp(-t * decay)
    ataque = min(int(SR * 0.004), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    return ruido * env[:, None] * (0.16 if abierto else 0.12)


def _clap_suave(dur_ms: float = 160) -> np.ndarray:
    """Clap/Rimshot suave de radio."""
    t = _t(dur_ms)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=900, pasa_bajos=4200)
    env = np.exp(-t * 26.0)
    return ruido * env[:, None] * 0.18


def _reverse_swell(freqs: list[float], dur_ms: float = 850) -> np.ndarray:
    """Acorde + platillo invertido (Reverse Cymbal/Chord) que sube suavemente hacia el golpe."""
    t = _t(dur_ms)
    prog = t / (dur_ms / 1000.0)
    acorde = _rhodes_chord(freqs, dur_ms, decay=0.2)
    aire = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=2500, pasa_bajos=8000) * 0.25
    curva = (prog ** 2.6)[:, None]
    out = (acorde + aire) * curva
    return _fade(out, 20, 15)


# ---------------------------------------------------------------------------
# Sweepers Radio Pro (Intro, Transición, Cierre)
# ---------------------------------------------------------------------------

def sintetizar_intro() -> np.ndarray:
    """
    Intro cálida y moderna (2.5 s):
    Reverse Swell aspirado -> Impacto grave redondo + Acorde Rhodes Fmaj9 -> Logo de 3 notas con eco.
    """
    pista = _pista(2600)
    # 1. Swell invertido que prepara la entrada (0 a 800ms)
    _sumar(pista, _reverse_swell([349.23, 440.0, 523.25, 659.25], 820), 0, 0.85)

    # 2. Golpe grave suave + Acorde abierto en el milisegundo 800
    _sumar(pista, _kick_pro(350), 800, 1.0)
    _sumar(pista, _sub_bass_note(87.31, 1500), 800, 0.9)  # Fa grave (F2)
    acorde_intro = _rhodes_chord([349.23, 440.00, 523.25, 659.25, 783.99], 1600, decay=2.0)
    _sumar(pista, acorde_intro, 800, 0.85)

    # 3. Sonic Logo limpio de 3 notas (C5 -> G5 -> A5) con Stereo Delay
    notas_logo = [(523.25, 0), (783.99, 140), (880.00, 280)]
    logo_track = _pista(1600)
    for freq, offset in notas_logo:
        nota = _rhodes_chord([freq, freq * 2.0], 700, decay=3.8)
        _sumar(logo_track, nota, offset, 0.95)
    logo_con_eco = _stereo_delay(logo_track, 220, 330, feedback=0.35, mezcla=0.35)
    _sumar(pista, logo_con_eco, 840, 0.8)

    return _fade(_reverb_estereo(pista, seg=1.2, mezcla=0.28), 10, 350)


def sintetizar_transicion() -> np.ndarray:
    """
    Separador dinámico entre Clima y Noticias (1.6 s):
    Swell corto -> Golpe profundo + Intervalo moderno de 2 notas (Quinta ascendente) con eco.
    """
    pista = _pista(1650)
    _sumar(pista, _reverse_swell([293.66, 440.0, 659.25], 450), 0, 0.75)
    _sumar(pista, _kick_pro(300), 430, 0.95)
    _sumar(pista, _sub_bass_note(110.0, 1000), 430, 0.85)

    stinger = _pista(1150)
    _sumar(stinger, _rhodes_chord([440.0, 659.25, 880.0], 500, decay=3.5), 0, 0.9)
    _sumar(stinger, _rhodes_chord([659.25, 987.77, 1318.51], 850, decay=2.8), 130, 0.95)
    stinger = _stereo_delay(stinger, 180, 270, feedback=0.3, mezcla=0.3)
    _sumar(pista, stinger, 430, 0.85)

    return _fade(_reverb_estereo(pista, seg=0.95, mezcla=0.26), 10, 250)


def sintetizar_cierre() -> np.ndarray:
    """
    Cierre redondo de FM (1.9 s):
    Resolución musical descendente hacia acorde cálido final + Kick profundo.
    """
    pista = _pista(1950)
    _sumar(pista, _reverse_swell([392.0, 493.88, 587.33], 350), 0, 0.65)

    cierre_mel = _pista(1550)
    _sumar(cierre_mel, _rhodes_chord([783.99, 987.77], 450, decay=3.5), 0, 0.8)
    _sumar(cierre_mel, _rhodes_chord([659.25, 783.99], 450, decay=3.5), 140, 0.8)
    _sumar(cierre_mel, _rhodes_chord([261.63, 329.63, 392.00, 493.88, 523.25], 1250, decay=2.2), 280, 1.0)

    _sumar(pista, _kick_pro(350), 610, 0.95)
    _sumar(pista, _sub_bass_note(65.41, 1300), 610, 0.95)  # Do profundo (C2)
    _sumar(pista, _stereo_delay(cierre_mel, 240, 360, 0.3, 0.25), 330, 0.85)

    return _fade(_reverb_estereo(pista, seg=1.3, mezcla=0.32), 10, 450)


# ---------------------------------------------------------------------------
# Cortinas Musicales con Groove Real (Clima y Noticias)
# ---------------------------------------------------------------------------

def bed_clima_groove(modo: str, dur_ms: float) -> np.ndarray:
    """
    Cortina musical instrumental estilo Chill Pop / Lounge FM con batería electrónica suave,
    bajo y acordes de piano eléctrico que cambian de armonía cada 2 compases.
    """
    pista = _pista(dur_ms)
    # 116 BPM -> Negra = 517ms, Compás (4 negras) = 2068ms
    beat_ms = 517.0
    compas_ms = beat_ms * 4

    # Progresiones de acordes según el clima
    if modo == "lluvia":
        # Tonalidad menor suave y elegante (Am9 -> Fmaj7)
        progresion = [
            ([220.0, 261.63, 329.63, 392.00, 493.88], 55.0),
            ([174.61, 220.00, 261.63, 329.63, 392.00], 43.65),
        ]
    elif modo == "noche":
        # Tonalidad nocturna profunda (Dm9 -> G13 -> Cmaj9)
        progresion = [
            ([293.66, 349.23, 440.00, 523.25, 659.25], 73.42),
            ([196.00, 246.94, 329.63, 349.23, 440.00], 49.00),
            ([261.63, 329.63, 392.00, 493.88, 587.33], 65.41),
        ]
    else:
        # Día / Calor: Pop luminoso y optimista (Fmaj9 -> G6 -> Em7 -> Am7)
        progresion = [
            ([349.23, 440.00, 523.25, 659.25, 783.99], 87.31),
            ([392.00, 493.88, 587.33, 659.25, 783.99], 98.00),
            ([329.63, 392.00, 493.88, 587.33, 783.99], 82.41),
            ([220.00, 261.63, 329.63, 392.00, 493.88], 55.00),
        ]

    # 1. Armonía (Warm Pad + Rhodes + Bajo)
    num_compases = int(dur_ms / compas_ms) + 2
    armonia = _pista(dur_ms)
    for c in range(num_compases):
        ini_c = c * compas_ms
        if ini_c >= dur_ms:
            break
        acorde_freqs, bajo_freq = progresion[c % len(progresion)]
        _sumar(armonia, _warm_pad(acorde_freqs, compas_ms + 200, corte_lp=1350), ini_c, 0.85)
        _sumar(armonia, _rhodes_chord(acorde_freqs, compas_ms * 0.9, decay=1.8), ini_c, 0.65)
        # Toque sincopado de Rhodes en el beat 2.5
        _sumar(armonia, _rhodes_chord(acorde_freqs, beat_ms * 1.5, decay=2.8), ini_c + beat_ms * 1.5, 0.45)
        # Bajo en el beat 1 y en el 3.5
        _sumar(armonia, _sub_bass_note(bajo_freq, beat_ms * 2.0), ini_c, 0.8)
        _sumar(armonia, _sub_bass_note(bajo_freq, beat_ms * 1.2), ini_c + beat_ms * 2.5, 0.65)

    # Efecto Sidechain (la armonía baja levemente en cada golpe de bombo para respirar con swing)
    t = _t(dur_ms)
    fase_beat = (t * (1000.0 / beat_ms)) % 1.0
    sidechain = 0.65 + 0.35 * (1.0 - np.exp(-fase_beat * 4.5))
    armonia *= sidechain[:, None]
    pista += _stereo_delay(armonia, 258, 387, feedback=0.25, mezcla=0.22)

    # 2. Batería suave de radio (Kick en cada negra, Clap suave en 2 y 4, Hi-hat en corcheas)
    kick = _kick_pro(240)
    clap = _clap_suave(150)
    hh_cerrado = _hihat_suave(60, abierto=False)
    hh_abierto = _hihat_suave(160, abierto=True)

    num_beats = int(dur_ms / beat_ms) + 1
    for b in range(num_beats):
        pos_b = b * beat_ms
        _sumar(pista, kick, pos_b, 0.70)
        if b % 2 == 1:
            _sumar(pista, clap, pos_b, 0.55)
        _sumar(pista, hh_cerrado, pos_b, 0.50)
        _sumar(pista, hh_abierto, pos_b + beat_ms * 0.5, 0.45)

    return _filtrar(pista, pasa_altos=45, pasa_bajos=9500)


def bed_noticias_groove(dur_ms: float) -> np.ndarray:
    """
    Cortina de Noticias moderna (124 BPM):
    En vez de un pitido de teletipo antiguo, usa un pulso de sintetizador grave ("arp muteado"),
    bombo constante y shaker estéreo dinámico estilo BBC Radio 1 Newsbeat / FM Urbana.
    """
    pista = _pista(dur_ms)
    beat_ms = 483.87  # 124 BPM
    semi_ms = beat_ms / 4.0

    # Colchón cálido informativo en Mi menor 9 (Em9)
    pista += _warm_pad([164.81, 196.00, 246.94, 293.66, 369.99], dur_ms, corte_lp=1100) * 0.85

    # Bajo pulsante muteado en corcheas + pequeños acordes plucks en momentos clave
    notas_pulso = [82.41, 82.41, 164.81, 82.41, 98.00, 82.41, 146.83, 123.47]
    corchea_ms = beat_ms / 2.0
    for i, pos in enumerate(np.arange(0, dur_ms - corchea_ms, corchea_ms)):
        f = notas_pulso[i % len(notas_pulso)]
        _sumar(pista, _sub_bass_note(f, corchea_ms * 0.85), pos, 0.65)
        if i % 8 in (0, 3, 6):
            acorde_corto = _rhodes_chord([329.63, 392.00, 493.88, 587.33], 220, decay=7.5)
            _sumar(pista, acorde_corto, pos, 0.50)

    # Ritmo percusivo ágil (Kick + Shaker en semicorcheas con acento)
    kick = _kick_pro(220)
    hh_fuerte = _hihat_suave(55, abierto=False)
    hh_debil = hh_fuerte * 0.45

    for b, pos in enumerate(np.arange(0, dur_ms - beat_ms, beat_ms)):
        _sumar(pista, kick, pos, 0.75)
        for s in range(4):
            h = hh_fuerte if s == 2 else hh_debil
            _sumar(pista, h, pos + s * semi_ms, 0.65)

    return _stereo_delay(_filtrar(pista, pasa_altos=45, pasa_bajos=10000), 242, 363, 0.2, 0.18)


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
    return _pico(x, 0.92)


def construir_ambiente(nombre: str, dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset(f"clima/{nombre}")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print(f"Generando cortina musical groove de clima ({nombre})...")
        base = bed_clima_groove(nombre, dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 400, 900)


def construir_cama_noticias(dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset("noticias_cama")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print("Generando cortina rítmica moderna de noticias...")
        base = bed_noticias_groove(dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 300, 800)


def cargar_pista_opcional(base: str) -> np.ndarray | None:
    seg = buscar_asset(base)
    if seg is None:
        return None
    return _rms(seg_a_array(seg), REFERENCIA_RMS_DB)


# ---------------------------------------------------------------------------
# Voces (Google Cloud TTS con SSML natural) + Procesamiento Broadcast
# ---------------------------------------------------------------------------

def texto_a_ssml(texto: str) -> str:
    """Convierte el texto en SSML agregando micro-pausas naturales de respiración."""
    seguro = texto.replace("&", " y ").replace("<", "").replace(">", "")
    # Pausa suave en puntos y dos puntos para que el locutor no atropelle las frases
    seguro = re.sub(r'\.\s+', '. <break time="280ms"/> ', seguro)
    seguro = re.sub(r':\s+', ': <break time="200ms"/> ', seguro)
    return f"<speak><prosody rate='1.03'>{seguro}</prosody></speak>"


def sintetizar_voz(client, texto: str, nombre_voz: str, audio_config) -> np.ndarray:
    entrada = texttospeech.SynthesisInput(ssml=texto_a_ssml(texto))
    voz = texttospeech.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
    resp = client.synthesize_speech(input=entrada, voice=voz, audio_config=audio_config)
    seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
    return seg_a_array(seg)


def procesar_voz(x: np.ndarray) -> np.ndarray:
    """EQ de micrófono de estudio (cálido y presente) + compresión suave sin distorsión."""
    x = _filtrar(x, pasa_altos=80)
    calidez = _filtrar(x, pasa_altos=110, pasa_bajos=250)
    presencia = _filtrar(x, pasa_altos=2500, pasa_bajos=5800)
    x = x + 0.18 * calidez + 0.24 * presencia
    x = _comprimir(x, thr_db=-22.0, ratio=3.2, ataque_ms=6.0, relajo_ms=140.0)
    return _pico(x, 0.89)


# ---------------------------------------------------------------------------
# Ensamblado y Masterización
# ---------------------------------------------------------------------------

def ensamblar_spot(voz_clima, voz_noticias, intro, trans, cierre, ambiente, cama, cama_noticias) -> np.ndarray:
    # El locutor entra justo después del golpe principal de la intro (pisa la cola con eco)
    inicio_clima = 1350.0
    fin_clima = inicio_clima + _dur_ms(voz_clima)

    capas = [(intro, 0.0, EFECTOS_DB), (voz_clima, inicio_clima, 0.0)]
    tramos_voz = [(inicio_clima, fin_clima)]

    if ambiente is not None:
        # La cortina musical arranca en el golpe de la intro (800ms) para que el ritmo sea continuo
        capas.append((ambiente, 800.0, AMBIENTE_DB))

    fin_voces = fin_clima
    if voz_noticias is not None:
        inicio_trans = fin_clima - 150.0
        inicio_not = inicio_trans + 880.0
        fin_not = inicio_not + _dur_ms(voz_noticias)
        capas += [(trans, inicio_trans, EFECTOS_DB), (voz_noticias, inicio_not, 0.0)]
        tramos_voz.append((inicio_not, fin_not))
        fin_voces = fin_not

        if cama_noticias is not None:
            capas.append((cama_noticias, inicio_trans + 430.0, TICKER_NOTICIAS_DB))

    inicio_cierre = fin_voces - 250.0
    capas.append((cierre, inicio_cierre, EFECTOS_DB))
    total_ms = inicio_cierre + _dur_ms(cierre)

    master = _pista(total_ms + 400)
    for x, ini, db in capas:
        _sumar(master, x, ini, _g(db))

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
    x = _filtrar(x, pasa_altos=35)
    x = _comprimir(x, thr_db=-15.5, ratio=2.5, ataque_ms=14.0, relajo_ms=190.0)
    x = np.tanh(x * 1.12)
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

    ambiente = construir_ambiente(ambiente_nombre, _dur_ms(voz_clima) + 900)
    cama = cargar_pista_opcional("cama")
    cama_noticias = construir_cama_noticias(_dur_ms(voz_noticias) + 800) if voz_noticias is not None else None

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

    print("Generando spot con groove FM y locución SSML...")
    generar_spot(texto_clima, texto_noticias, ambiente_nombre, LOCAL_MP3)

    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
