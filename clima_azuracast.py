#!/usr/bin/env python3
"""
Spot automático de clima y noticias para AzuraCast (Nogaradio) - VERSION V4 TURBO FM.
Cambio radical de diseño sonoro:
  - Intro con Tuning Dial FX + Laser Drop + 4 Pips Horarios + Acorde Brass Synth.
  - Cortinas mucho más presentes en volumen con Arpegios Pop/Dance en estéreo.
  - Transición con Doble Impacto + Whoosh inverso.
  - Efectos atmosféricos SIEMPRE audibles (trueno/lluvia, brisa espacial nocturna o destellos diurnos).
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

# Niveles de mezcla en dB (Subidos notablemente para que la artística se escuche bien al frente)
REFERENCIA_RMS_DB = -18.0
EFECTOS_DB = -1.5          # Sweepers bien potentes al frente
AMBIENTE_DB = -8.5         # Cortina de clima bien audible debajo de la voz
TICKER_NOTICIAS_DB = -9.0  # Cortina de noticias bien marcada
CAMA_DB_LIBRE = -4.0
CAMA_DB_VOZ = -11.0

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
    "{saludo}. En Argentina son las {hora}. Nogaradio te cuenta cómo está el tiempo en Resistencia: tenemos {descripcion}, con {temp} grados y una sensación térmica de {sensacion}. La humedad es del {humedad} por ciento. Quedate con nosotros, que ya llegan las noticias.",
    "{saludo}, ¿cómo estás? Pasaron las {hora} en todo el país. Así está el tiempo ahora en Resistencia: {descripcion}, {temp} grados de temperatura y {sensacion} de sensación térmica. Humedad del {humedad} por ciento. A continuación, compartimos el flash informativo.",
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
# Motor DSP
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


def _ducking_por_voz(fondo: np.ndarray, pista_voz: np.ndarray, reduccion_db: float = -4.5) -> np.ndarray:
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


def _stereo_delay(x: np.ndarray, delay_l_ms: float = 220.0, delay_r_ms: float = 330.0, feedback: float = 0.35, mezcla: float = 0.32) -> np.ndarray:
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


def _reverb_estereo(x: np.ndarray, seg: float = 1.2, mezcla: float = 0.32, brillo: float = 5200.0) -> np.ndarray:
    ir_n = int(SR * seg)
    t = np.arange(ir_n) / SR
    rng = np.random.default_rng(99)
    ir = rng.standard_normal((ir_n, 2)) * np.exp(-6.0 * t / seg)[:, None]
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
# Nuevos Sintetizadores V4 Turbo FM (Synth Brass, Pluck Pop, Slap Bass, FX)
# ---------------------------------------------------------------------------

def _synth_brass(freqs: list[float], dur_ms: float, corte_lp: float = 3800.0) -> np.ndarray:
    """Acorde potente de sintetizador estilo Pop/The Weeknd/Dua Lipa con apertura de brillo."""
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

    # Envolvente "swell" tipo brass (abre en los primeros 60ms)
    ataque = min(int(SR * 0.055), len(t))
    env = np.exp(-2.0 * t)
    env[:ataque] *= np.linspace(0.1, 1.0, ataque)
    out = _filtrar(out * env[:, None], pasa_altos=95, pasa_bajos=corte_lp)
    return np.tanh(out * 0.45) * 0.38


def _pop_pluck(freq: float, dur_ms: float = 240, pan: float = 0.5) -> np.ndarray:
    """Nota arpegiada brillante y percusiva en estéreo."""
    t = _t(dur_ms)
    mod = 2.5 * np.exp(-t * 28.0) * np.sin(2 * np.pi * (freq * 2.0) * t)
    onda = np.sin(2 * np.pi * freq * t + mod) + 0.35 * np.sin(2 * np.pi * (freq * 0.5) * t)
    env = np.exp(-t * 11.0)
    ataque = min(int(SR * 0.003), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    mono = onda * env * 0.35
    return np.column_stack((mono * pan, mono * (1.0 - pan)))


def _funky_bass(freq: float, dur_ms: float) -> np.ndarray:
    """Bajo electrónico con golpe FM ('slap') bien marcado para que se sienta en celulares y parlantes."""
    t = _t(dur_ms)
    mod = 1.8 * np.exp(-t * 22.0) * np.sin(2 * np.pi * (freq * 2.0) * t)
    sub = np.sin(2 * np.pi * freq * t + mod)
    arm = 0.4 * np.sin(2 * np.pi * (freq * 2.0) * t) * np.exp(-t * 10.0)
    env = np.exp(-4.0 * t)
    ataque = min(int(SR * 0.006), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    return _est(np.tanh((sub + arm) * env * 1.6) * 0.42)


def _kick_punch(dur_ms: float = 240) -> np.ndarray:
    t = _t(dur_ms)
    f_env = 48.0 + 140.0 * np.exp(-t * 42.0)
    fase = 2 * np.pi * np.cumsum(f_env) / SR
    cuerpo = np.tanh(np.sin(fase) * 1.8) * np.exp(-t * 11.0)
    click = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=1000, pasa_bajos=4500)
    click *= np.exp(-t * 110.0)[:, None] * 0.25
    return _est(cuerpo * 0.85) + click


def _snare_pop(dur_ms: float = 190) -> np.ndarray:
    t = _t(dur_ms)
    tono = np.sin(2 * np.pi * 185.0 * t) * np.exp(-t * 28.0) * 0.4
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=700, pasa_bajos=7500)
    ruido *= np.exp(-t * 20.0)[:, None] * 0.35
    return _est(tono) + ruido


def _hihat_crisp(dur_ms: float = 65, abierto: bool = False) -> np.ndarray:
    dur = 180 if abierto else dur_ms
    t = _t(dur)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=6500, pasa_bajos=13500)
    env = np.exp(-t * (14.0 if abierto else 50.0))
    return ruido * env[:, None] * (0.22 if abierto else 0.16)


def _radio_tuning_fx(dur_ms: float = 750) -> np.ndarray:
    """Efecto de sintonía de dial de radio FM + barrido láser rápido."""
    t = _t(dur_ms)
    prog = t / (dur_ms / 1000.0)
    # Chirp de sintonizador FM que sube y baja rápido
    f_dial = 600.0 + 1800.0 * np.sin(2 * np.pi * 3.5 * prog) + 2200.0 * (prog ** 2)
    fase = 2 * np.pi * np.cumsum(f_dial) / SR
    dial = np.sin(fase) * 0.22
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=800, pasa_bajos=6500) * 0.30
    pan_l = 0.2 + 0.8 * prog
    pan_r = 1.0 - 0.8 * prog
    out = ruido + np.column_stack((dial * pan_l, dial * pan_r))
    return _fade(out, 30, 40)


def _laser_drop(dur_ms: float = 450) -> np.ndarray:
    """Impacto láser descendente de radio Top 40 (de 3200Hz a 60Hz)."""
    t = _t(dur_ms)
    f_curva = 60.0 + 3100.0 * np.exp(-t * 14.0)
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    onda = np.tanh(np.sin(fase) * 1.5) * np.exp(-t * 5.5)
    return _est(onda * 0.55)


# ---------------------------------------------------------------------------
# Sweepers Nuevos V4 (Imposibles de confundir con los anteriores)
# ---------------------------------------------------------------------------

def sintetizar_intro() -> np.ndarray:
    """
    Intro V4 Turbo FM (2.8 s):
    1) Efecto de sintonía de dial + 3 Pips cortos y 1 Pip largo agudo (estilo Top 40).
    2) Laser Drop + Doble golpe de bombo síncopado.
    3) Acorde Synth Brass gigante + Fanfarria ascendente de 4 notas en estéreo.
    """
    pista = _pista(2800)

    # 1. Tuning FX + Pips horarios bien audibles (0 a 900ms)
    _sumar(pista, _radio_tuning_fx(800), 0, 0.75)
    for i, pos in enumerate([120, 340, 560, 780]):
        es_ultimo = (i == 3)
        dur = 320 if es_ultimo else 75
        freq = 1760.0 if es_ultimo else 880.0  # A5 -> A5 -> A5 -> A6 (clásico pip horario)
        t = _t(dur)
        pip = (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t)) * np.exp(-t * (6.0 if es_ultimo else 22.0))
        _sumar(pista, _est(pip * 0.42), pos)

    # 2. Explosión en el ms 800: Laser Drop + Doble Kick + Acorde Brass Am9
    _sumar(pista, _laser_drop(420), 780, 0.85)
    _sumar(pista, _kick_punch(260), 800, 1.0)
    _sumar(pista, _kick_punch(260), 1020, 0.9)
    _sumar(pista, _funky_bass(55.0, 1400), 800, 1.0)
    _sumar(pista, _synth_brass([220.0, 261.63, 329.63, 392.00, 493.88], 1600), 800, 0.90)

    # 3. Logo melódico súper brillante (A5 -> C6 -> E6 -> G6 -> A6)
    notas_fanfarria = [880.0, 1046.50, 1318.51, 1567.98, 1760.0]
    logo = _pista(1800)
    for idx, f in enumerate(notas_fanfarria):
        pan = 0.2 + 0.6 * (idx / 4.0)
        _sumar(logo, _pop_pluck(f, 450 if idx < 4 else 900, pan=pan), idx * 105, 1.0)
    _sumar(pista, _stereo_delay(logo, 190, 285, 0.40, 0.38), 860, 0.95)

    return _fade(_reverb_estereo(pista, seg=1.25, mezcla=0.32), 10, 350)


def sintetizar_transicion() -> np.ndarray:
    """Separador de Noticias con Laser Drop + Doble golpe + Stinger informativo rápido."""
    pista = _pista(1700)
    _sumar(pista, _radio_tuning_fx(380), 0, 0.65)
    _sumar(pista, _laser_drop(350), 320, 0.80)
    _sumar(pista, _kick_punch(250), 340, 1.0)
    _sumar(pista, _snare_pop(200), 520, 0.85)
    _sumar(pista, _funky_bass(73.42, 1100), 340, 0.95)

    # Secuencia rápida tipo Flash de Noticias (D5 - A5 - D6 - E6)
    stinger = _pista(1200)
    for i, f in enumerate([587.33, 880.0, 1174.66, 1318.51]):
        _sumar(stinger, _pop_pluck(f, 400, pan=0.3 if i % 2 == 0 else 0.7), i * 85, 1.0)
    _sumar(stinger, _synth_brass([293.66, 349.23, 440.0, 523.25], 1000), 240, 0.85)
    _sumar(pista, _stereo_delay(stinger, 160, 240, 0.35, 0.32), 340, 0.95)

    return _fade(_reverb_estereo(pista, seg=0.95, mezcla=0.28), 10, 250)


def sintetizar_cierre() -> np.ndarray:
    """Cierre potente con arpegio descendente + Acorde Brass final + Sub Drop."""
    pista = _pista(2100)
    notas_fin = [1760.0, 1567.98, 1318.51, 1046.50, 880.0]
    arp = _pista(1800)
    for i, f in enumerate(notas_fin):
        _sumar(arp, _pop_pluck(f, 500, pan=0.8 - i * 0.15), i * 95, 0.95)
    _sumar(pista, _stereo_delay(arp, 200, 300, 0.35, 0.30), 0, 0.9)

    _sumar(pista, _laser_drop(500), 380, 0.75)
    _sumar(pista, _kick_punch(320), 400, 1.0)
    _sumar(pista, _funky_bass(55.0, 1500), 400, 1.0)
    _sumar(pista, _synth_brass([220.0, 261.63, 329.63, 440.0, 523.25], 1500), 400, 0.95)

    return _fade(_reverb_estereo(pista, seg=1.35, mezcla=0.35), 10, 450)


# ---------------------------------------------------------------------------
# Cortinas Musicales V4 (Con Arpegios Pop/Dance + Atmosfera siempre audible)
# ---------------------------------------------------------------------------

def bed_clima_groove(modo: str, dur_ms: float) -> np.ndarray:
    """
    Base Pop/Dance moderna (120 BPM) con arpegios estéreo constantes, batería bien marcada
    y efectos atmosféricos audibles en CUALQUIER clima (no solo cuando llueve).
    """
    pista = _pista(dur_ms)
    t = _t(dur_ms)
    beat_ms = 500.0   # 120 BPM exactos
    semi_ms = 125.0   # Semicorcheas para el arpegio

    # 1. Acordes y Arpegios según el clima
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
        _sumar(musica, _synth_brass(pad_f, compas_ms, corte_lp=1800), ini_c, 0.55)

        # Bajo Funky con ritmo en corcheas
        for b in range(8):
            f_b = bajo_f * 2.0 if b in (3, 7) else bajo_f
            _sumar(musica, _funky_bass(f_b, 230), ini_c + b * 250.0, 0.75)

        # Arpegio Pop cristalino moviéndose de izquierda a derecha en semicorcheas
        for s in range(16):
            nota_f = arp_f[s % len(arp_f)]
            pan = 0.25 if s % 2 == 0 else 0.75
            _sumar(musica, _pop_pluck(nota_f, 180, pan=pan), ini_c + s * semi_ms, 0.55)

    # Bombeo Sidechain bien marcado a 120 BPM
    fase_beat = (t * 2.0) % 1.0
    sidechain = 0.45 + 0.55 * (1.0 - np.exp(-fase_beat * 5.0))
    musica *= sidechain[:, None]
    pista += _stereo_delay(musica, 250, 375, 0.30, 0.25)

    # 2. Batería Pop completa (Kick, Snare en 2 y 4, Hi-Hats)
    kick = _kick_punch(230)
    snare = _snare_pop(180)
    hh_c = _hihat_crisp(55, abierto=False)
    hh_o = _hihat_crisp(160, abierto=True)

    for b in range(int(dur_ms / beat_ms) + 1):
        pos_b = b * beat_ms
        _sumar(pista, kick, pos_b, 0.85)
        if b % 2 == 1:
            _sumar(pista, snare, pos_b, 0.75)
        _sumar(pista, hh_c, pos_b, 0.65)
        _sumar(pista, hh_o, pos_b + 250.0, 0.60)

    # 3. Efectos de ambiente SIEMPRE presentes (según toque lluvia, viento, noche o día)
    if modo in ("lluvia", "tormenta"):
        agua = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=1000, pasa_bajos=7500)
        oleaje = 0.75 + 0.25 * np.sin(2 * np.pi * 0.3 * t)
        pista += agua * oleaje[:, None] * 0.22
        for pos_tr in (dur_ms * 0.18, dur_ms * 0.62):
            t_tr = _t(2500)
            trueno = _filtrar(np.random.uniform(-1, 1, (len(t_tr), 2)), pasa_altos=30, pasa_bajos=160)
            env_tr = np.minimum(t_tr / 0.15, 1.0) * np.exp(-t_tr * 1.3)
            _sumar(pista, _pico(trueno * env_tr[:, None], 0.8), pos_tr, 0.75)
    else:
        # Barridos aerodinámicos espaciales y destellos cristalinos cada 4 segundos
        for pos_sw in np.arange(1500, dur_ms - 1500, 4000):
            _sumar(pista, _radio_tuning_fx(600) * 0.35, pos_sw, 0.50)

    return _filtrar(pista, pasa_altos=40, pasa_bajos=11000)


def bed_noticias_groove(dur_ms: float) -> np.ndarray:
    """
    Cortina de Noticias de alta energía (128 BPM):
    Pulso electrónico de sintetizador en semicorcheas + Teletipo digital estéreo + Batería ágil.
    """
    pista = _pista(dur_ms)
    beat_ms = 468.75   # 128 BPM
    semi_ms = beat_ms / 4.0

    # Secuencia de sintetizador estilo Breaking News
    secuencia = [220.0, 220.0, 440.0, 220.0, 329.63, 220.0, 392.0, 329.63]
    for i, pos in enumerate(np.arange(0, dur_ms - semi_ms, semi_ms)):
        f = secuencia[i % len(secuencia)]
        pan = 0.3 if i % 2 == 0 else 0.7
        _sumar(pista, _pop_pluck(f, 110, pan=pan), pos, 0.60)
        if i % 4 == 0:
            _sumar(pista, _funky_bass(f * 0.5, 200), pos, 0.75)
        # Beep de teletipo agudo sutil cada 2 semicorcheas
        if i % 2 == 0:
            t_b = _t(35)
            bip = np.sin(2 * np.pi * (1760.0 if i % 8 == 0 else 1318.51) * t_b) * np.hanning(len(t_b))
            _sumar(pista, _est(bip * 0.18), pos)

    kick = _kick_punch(210)
    snare = _snare_pop(160)
    hh = _hihat_crisp(50, abierto=False)

    for b, pos in enumerate(np.arange(0, dur_ms - beat_ms, beat_ms)):
        _sumar(pista, kick, pos, 0.85)
        if b % 2 == 1:
            _sumar(pista, snare, pos, 0.70)
        for s in range(4):
            _sumar(pista, hh, pos + s * semi_ms, 0.75 if s == 2 else 0.40)

    return _stereo_delay(_filtrar(pista, pasa_altos=45, pasa_bajos=11000), 234, 351, 0.25, 0.22)


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
    return _pico(x, 0.95)


def construir_ambiente(nombre: str, dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset(f"clima/{nombre}")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print(f"Generando cortina V4 Pop/Dance + FX ({nombre})...")
        base = bed_clima_groove(nombre, dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 300, 800)


def construir_cama_noticias(dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset("noticias_cama")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print("Generando cortina V4 Breaking News (128 BPM)...")
        base = bed_noticias_groove(dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 250, 700)


def cargar_pista_opcional(base: str) -> np.ndarray | None:
    seg = buscar_asset(base)
    if seg is None:
        return None
    return _rms(seg_a_array(seg), REFERENCIA_RMS_DB)


# ---------------------------------------------------------------------------
# Voces (Google Cloud TTS con SSML) + Procesador Vocal "Radio FM SM7B"
# ---------------------------------------------------------------------------

def texto_a_ssml(texto: str) -> str:
    seguro = texto.replace("&", " y ").replace("<", "").replace(">", "")
    seguro = re.sub(r'\.\s+', '. <break time="260ms"/> ', seguro)
    seguro = re.sub(r':\s+', ': <break time="180ms"/> ', seguro)
    return f"<speak><prosody rate='1.05'>{seguro}</prosody></speak>"


def sintetizar_voz(client, texto: str, nombre_voz: str, audio_config) -> np.ndarray:
    entrada = texttospeech.SynthesisInput(ssml=texto_a_ssml(texto))
    voz = texttospeech.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
    resp = client.synthesize_speech(input=entrada, voice=voz, audio_config=audio_config)
    seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
    return seg_a_array(seg)


def procesar_voz(x: np.ndarray) -> np.ndarray:
    x = _filtrar(x, pasa_altos=85)
    pecho = _filtrar(x, pasa_altos=110, pasa_bajos=240)
    presencia = _filtrar(x, pasa_altos=2600, pasa_bajos=5800)
    aire = np.tanh(_filtrar(x, pasa_altos=6500) * 2.2) * 0.12

    x = x + 0.24 * pecho + 0.28 * presencia + aire

    d_l = int(SR * 0.011)
    d_r = int(SR * 0.017)
    doble = np.zeros_like(x)
    doble[d_l:, 0] = x[:-d_l, 0] * 0.14
    doble[d_r:, 1] = x[:-d_r, 1] * 0.14
    x = x + _filtrar(doble, pasa_altos=250, pasa_bajos=4000)

    x = _comprimir(x, thr_db=-23.0, ratio=3.8, ataque_ms=4.0, relajo_ms=110.0)
    return _pico(x, 0.90)


# ---------------------------------------------------------------------------
# Ensamblado y Masterización
# ---------------------------------------------------------------------------

def ensamblar_spot(voz_clima, voz_noticias, intro, trans, cierre, ambiente, cama, cama_noticias) -> np.ndarray:
    inicio_clima = 1650.0
    fin_clima = inicio_clima + _dur_ms(voz_clima)

    fin_voces = fin_clima
    inicio_trans = None
    inicio_not = None
    if voz_noticias is not None:
        inicio_trans = fin_clima - 100.0
        inicio_not = inicio_trans + 950.0
        fin_not = inicio_not + _dur_ms(voz_noticias)
        fin_voces = fin_not

    inicio_cierre = fin_voces - 200.0
    total_ms = inicio_cierre + _dur_ms(cierre)

    bus_voces = _pista(total_ms + 400)
    _sumar(bus_voces, voz_clima, inicio_clima, 1.0)
    if voz_noticias is not None and inicio_not is not None:
        _sumar(bus_voces, voz_noticias, inicio_not, 1.0)

    bus_cortinas = _pista(total_ms + 400)
    if ambiente is not None:
        _sumar(bus_cortinas, ambiente, 800.0, _g(AMBIENTE_DB))
    if cama_noticias is not None and inicio_trans is not None:
        _sumar(bus_cortinas, cama_noticias, inicio_trans + 340.0, _g(TICKER_NOTICIAS_DB))
    if cama is not None:
        _sumar(bus_cortinas, _extender(cama, len(bus_cortinas)), 0.0, _g(CAMA_DB_LIBRE - 3.0))

    bus_cortinas = _ducking_por_voz(bus_cortinas, bus_voces, reduccion_db=-4.5)

    bus_fx = _pista(total_ms + 400)
    _sumar(bus_fx, intro, 0.0, _g(EFECTOS_DB))
    if inicio_trans is not None:
        _sumar(bus_fx, trans, inicio_trans, _g(EFECTOS_DB))
    _sumar(bus_fx, cierre, inicio_cierre, _g(EFECTOS_DB))

    master = bus_voces + bus_cortinas + bus_fx
    return master[: int(SR * total_ms / 1000)]


def masterizar(x: np.ndarray) -> np.ndarray:
    x = _filtrar(x, pasa_altos=35)

    graves = _comprimir(_filtrar(x, pasa_bajos=200), thr_db=-18.0, ratio=3.5, ataque_ms=10.0, relajo_ms=160.0)
    medios = _comprimir(_filtrar(x, pasa_altos=200, pasa_bajos=4200), thr_db=-17.0, ratio=2.8, ataque_ms=8.0, relajo_ms=140.0)
    agudos = _comprimir(_filtrar(x, pasa_altos=4200), thr_db=-19.0, ratio=2.5, ataque_ms=4.0, relajo_ms=90.0)

    x = graves * 1.10 + medios * 1.0 + agudos * 1.15

    mid = 0.5 * (x[:, 0] + x[:, 1])
    side = 0.5 * (x[:, 0] - x[:, 1])
    side_amplio = _filtrar(_est(side), pasa_altos=220)[:, 0] * 1.32
    x = np.column_stack((mid + side_amplio, mid - side_amplio))

    x = _comprimir(x, thr_db=-13.0, ratio=4.0, ataque_ms=5.0, relajo_ms=120.0)
    x = np.tanh(x * 1.18)
    return _pico(x, 0.95)


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
    cama_noticias = construir_cama_noticias(_dur_ms(voz_noticias) + 1000) if voz_noticias is not None else None

    mezcla = ensamblar_spot(voz_clima, voz_noticias, intro, trans, cierre, ambiente, cama, cama_noticias)
    final = masterizar(mezcla)
    array_a_seg(final).export(path_salida, format="mp3", bitrate="192k")
    print(f"¡Spot V4 ({len(final) / SR:.1f} s) guardado exitosamente en {path_salida}!")


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
    print("=== EJECUTANDO VERSION V4 TURBO FM ===")
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

    print("Generando spot con procesador V4 Turbo FM...")
    generar_spot(texto_clima, texto_noticias, ambiente_nombre, LOCAL_MP3)

    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
