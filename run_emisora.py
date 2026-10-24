#!/usr/bin/env python3
"""
Spot automático exclusivo para Radio Redimidos por Jesús (run_emisora.py) - Edición Dúo FM Dinámico.

Qué hace esta versión:
  - Dúo de voces en cada spot (Elena y Tomás combinados): Uno da la hora/clima -> Transición FM -> El otro hace el cierre institucional.
  - Rotación horaria: En horas pares abre Mujer y cierra Hombre; en impares abre Hombre y cierra Mujer.
  - Música y efectos variables según la HORA (Mañana luminosa, Tarde rítmica, Noche profunda) + CLIMA (Lluvia, Tormenta, Viento o Despejado).
  - Canción instrumental de fondo más presente con Sidechain Ducking suave.
"""

import asyncio
import glob
import io
import os
import random
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import edge_tts
import numpy as np
import paramiko
import requests
from pydub import AudioSegment

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

NOMBRE_EMISORA = os.environ.get("NOMBRE_EMISORA", "Radio Redimidos por Jesús").strip()
# Velocidad neutra para que Elena y Tomás suenen más conversacionales.
# Se puede personalizar sin modificar el código mediante la variable VELOCIDAD_VOZ.
VELOCIDAD_VOZ = os.environ.get("VELOCIDAD_VOZ", "+0%").strip()

CIUDAD = "Resistencia"
LAT = -27.4514
LON = -58.9867
ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")

LOCAL_MP3 = "clima.mp3"
REMOTE_FILENAME = "clima.mp3"

ASSETS_DIR = "assets"
EXTENSIONES = (".mp3", ".wav", ".ogg", ".m4a", ".flac")
SR = 44100

# Niveles de mezcla en dB (Canción de fondo y efectos más presentes)
REFERENCIA_RMS_DB = -18.5
EFECTOS_DB = -2.0          # Intro, Transición FM y Cierre bien claros
AMBIENTE_DB = -8.0         # Canción de fondo + efectos del clima bien audibles
CAMA_EXTERNA_DB = -8.5     # Por si algún día subís un assets/cama.mp3

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


def momento_del_dia(hora_actual: int) -> str:
    if 6 <= hora_actual < 12:
        return "manana"
    elif 12 <= hora_actual < 20:
        return "tarde"
    return "noche"


def saludo_segun_hora(hora_actual: int) -> str:
    momento = momento_del_dia(hora_actual)
    if momento == "manana":
        return random.choice(["Buenos días, qué lindo acompañarte", "Muy buenos días, bendiciones", "Hola, excelente mañana para vos"])
    elif momento == "tarde":
        return random.choice(["Buenas tardes, qué bueno estar con vos", "Muy buenas tardes, bendiciones", "Hola, excelente tarde"])
    return random.choice(["Buenas noches, gracias por tu compañía", "Muy buenas noches, bendiciones", "Excelente noche para vos"])


# ---------------------------------------------------------------------------
# Textos a Dos Voces (Voz 1: Hora y Clima | Voz 2: Frase Institucional FM)
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


def armar_dialogo_spot(data: dict | None) -> tuple[str, str]:
    """Devuelve (texto_voz_1, texto_voz_2) para que hablen intercalados con transición en el medio."""
    ahora = datetime.now(ZONA_HORARIA)
    hora_str = hora_en_espanol(ahora.hour, ahora.minute)
    saludo = saludo_segun_hora(ahora.hour)

    # Frases para el segundo locutor (después del efecto de transición FM)
    frases_cierre_duo = [
        f"Seguí conectado a {NOMBRE_EMISORA}. Seguimos con vos, compartiendo la mejor música y una palabra de bendición para tu vida.",
        f"Seguimos con vos en cada momento. Quedate en la sintonía de {NOMBRE_EMISORA}, donde la buena música te acompaña siempre.",
        f"No te muevas de ahí, seguí conectado con nosotros. En {NOMBRE_EMISORA} tenemos mucho más para compartir con vos.",
        f"Cada hora junto a vos. Seguimos conectados en la sintonía de {NOMBRE_EMISORA}, acompañando tu día con la mejor programación.",
    ]
    texto_voz_2 = random.choice(frases_cierre_duo)

    if not data or "current" not in data:
        texto_voz_1 = f"{saludo}. En Argentina pasaron las {hora_str}. Así te actualizamos la hora en la sintonía de {NOMBRE_EMISORA}."
        return texto_voz_1, texto_voz_2

    actual = data["current"]
    temp = round(actual["temperature_2m"])
    sensacion = round(actual["apparent_temperature"])
    humedad = round(actual["relative_humidity_2m"])
    viento = round(actual["wind_speed_10m"])
    descripcion = CODIGOS_CLIMA.get(actual["weather_code"], "condiciones variables")

    plantillas_voz_1 = [
        f"{saludo}. En Argentina son las {hora_str}. Actualizamos el estado del tiempo en {CIUDAD}: tenemos {descripcion}, con {temp} grados de temperatura y una sensación térmica de {sensacion} grados. La humedad es del {humedad} por ciento.",
        f"{saludo}. Pasaron las {hora_str} en todo el país. Así está el clima ahora en {CIUDAD}: cielo con {descripcion}, temperatura actual de {temp} grados y térmica de {sensacion}. Humedad del {humedad} por ciento y vientos de {viento} kilómetros por hora.",
        f"{saludo}. Son las {hora_str} en la República Argentina. Te contamos cómo está el tiempo en {CIUDAD}: {descripcion}, {temp} grados de temperatura, sensación térmica de {sensacion} grados y una humedad del {humedad} por ciento.",
    ]
    return random.choice(plantillas_voz_1), texto_voz_2


def analizar_estado_clima(data: dict | None) -> str:
    if not data or "current" not in data:
        return "despejado"
    actual = data["current"]
    codigo = actual.get("weather_code", 0)
    viento = actual.get("wind_speed_10m", 0)

    if codigo in (95, 96, 99):
        return "tormenta"
    if codigo in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "lluvia"
    if codigo in (3, 45, 48, 71, 73, 75) or (viento and viento >= 25):
        return "viento"
    return "despejado"


# ---------------------------------------------------------------------------
# Utilidades DSP
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


def _ducking_por_voz(fondo: np.ndarray, pista_voz: np.ndarray, reduccion_db: float = -3.8) -> np.ndarray:
    """Baja apenas la canción cuando hablan (-3.8 dB) y la sube al frente en las pausas y transiciones."""
    n = min(len(fondo), len(pista_voz))
    blk = int(SR * 0.015)
    nb = n // blk + 1
    env = np.pad(np.abs(pista_voz[:n]).max(axis=1), (0, nb * blk - n)).reshape(nb, blk).max(axis=1)
    hablando = (env > 0.04).astype(np.float64)

    a = np.exp(-15.0 / 30.0)
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


def _stereo_delay(x: np.ndarray, delay_l_ms: float = 260.0, delay_r_ms: float = 390.0, feedback: float = 0.35, mezcla: float = 0.32) -> np.ndarray:
    out = x.copy()
    dl = int(SR * delay_l_ms / 1000)
    dr = int(SR * delay_r_ms / 1000)
    eco = _filtrar(x, pasa_altos=240, pasa_bajos=3800)
    for rep in range(1, 4):
        gan = (feedback ** rep) * mezcla
        desp_l = rep * dl
        desp_r = rep * dr
        if desp_l < len(out):
            out[desp_l:, 0] += eco[:-desp_l, 1] * gan
        if desp_r < len(out):
            out[desp_r:, 1] += eco[:-desp_r, 0] * gan
    return out


def _reverb_estereo(x: np.ndarray, seg: float = 1.4, mezcla: float = 0.34, brillo: float = 4800.0) -> np.ndarray:
    ir_n = int(SR * seg)
    t = np.arange(ir_n) / SR
    rng = np.random.default_rng(55)
    ir = rng.standard_normal((ir_n, 2)) * np.exp(-5.8 * t / seg)[:, None]
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
# Instrumentos y Efectos Radio FM (Cálidos pero con punch radial)
# ---------------------------------------------------------------------------

def _piano_calido(freqs: list[float], dur_ms: float, decay: float = 2.0) -> np.ndarray:
    t = _t(dur_ms)
    out = np.zeros((len(t), 2))
    ataque = min(int(SR * 0.008), len(t))
    for i, f in enumerate(freqs):
        martillo = 0.25 * np.sin(2 * np.pi * f * 3.0 * t) * np.exp(-t * 16.0)
        onda_l = np.sin(2 * np.pi * f * t + martillo) + 0.20 * np.sin(2 * np.pi * f * 2.0 * t)
        onda_r = np.sin(2 * np.pi * (f * 1.0015) * t + martillo) + 0.20 * np.sin(2 * np.pi * (f * 2.003) * t)
        env = np.exp(-decay * t)
        env[:ataque] *= np.linspace(0, 1, ataque)
        pan = 0.25 + 0.5 * (i / max(1, len(freqs) - 1))
        out[:, 0] += onda_l * env * pan
        out[:, 1] += onda_r * env * (1.0 - pan)
    return _filtrar(out, pasa_altos=95, pasa_bajos=3800) * 0.34


def _bell_chime(freq: float, dur_ms: float = 800, pan: float = 0.5) -> np.ndarray:
    t = _t(dur_ms)
    mod = 2.0 * np.exp(-t * 14.0) * np.sin(2 * np.pi * (freq * 2.0) * t)
    onda = np.sin(2 * np.pi * freq * t + mod) + 0.30 * np.sin(2 * np.pi * (freq * 3.0) * t) * np.exp(-t * 8.0)
    env = np.exp(-t * 3.5)
    ataque = min(int(SR * 0.004), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    mono = onda * env * 0.32
    return np.column_stack((mono * pan, mono * (1.0 - pan)))


def _pad_celestial(freqs: list[float], dur_ms: float, corte_lp: float = 1500.0) -> np.ndarray:
    t = _t(dur_ms)
    out = np.zeros((len(t), 2))
    rng = np.random.default_rng(19)
    for f in freqs:
        for det, pl, pr in [(-0.007, 0.85, 0.15), (0.0, 0.5, 0.5), (0.007, 0.15, 0.85)]:
            fa = f * (1.0 + det)
            fase = rng.uniform(0, 1)
            saw = 2.0 * ((t * fa + fase) % 1.0) - 1.0
            out[:, 0] += saw * pl
            out[:, 1] += saw * pr
    out = _filtrar(out, pasa_altos=90, pasa_bajos=corte_lp)
    return _fade(out * 0.16, 300, 400)


def _bajo_fm(freq: float, dur_ms: float) -> np.ndarray:
    t = _t(dur_ms)
    onda = np.tanh((np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * freq * 2.0 * t)) * 1.5)
    env = np.exp(-3.2 * t)
    ataque = min(int(SR * 0.008), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    return _est(_filtrar(_est(onda * env), pasa_bajos=230)[:, 0] * 0.44)


def _bombo_radio(dur_ms: float = 260) -> np.ndarray:
    t = _t(dur_ms)
    f_env = 46.0 + 110.0 * np.exp(-t * 35.0)
    fase = 2 * np.pi * np.cumsum(f_env) / SR
    cuerpo = np.tanh(np.sin(fase) * 1.6) * np.exp(-t * 10.0)
    click = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=900, pasa_bajos=3800)
    click *= np.exp(-t * 90.0)[:, None] * 0.18
    return _est(cuerpo * 0.78) + click


def _clap_suave(dur_ms: float = 160) -> np.ndarray:
    t = _t(dur_ms)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=850, pasa_bajos=4500)
    env = np.exp(-t * 24.0)
    return ruido * env[:, None] * 0.22


def _shaker_estereo(dur_ms: float = 80, acento: bool = False) -> np.ndarray:
    t = _t(dur_ms)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=5800, pasa_bajos=12500)
    env = np.sin(np.pi * (t / (dur_ms / 1000.0))) ** 2
    return ruido * env[:, None] * (0.20 if acento else 0.12)


def _whoosh_fm(dur_ms: float = 650, f_ini: float = 300.0, f_fin: float = 4200.0) -> np.ndarray:
    """Barrido de aire FM moderno que se mueve de izquierda a derecha."""
    t = _t(dur_ms)
    prog = t / (dur_ms / 1000.0)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=350, pasa_bajos=6500)
    f_curva = f_ini * ((f_fin / f_ini) ** (prog ** 1.5))
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    tono = np.sin(fase) * 0.18
    env = np.sin(np.pi * (prog ** 1.4)) ** 2
    pan_l = 0.2 + 0.8 * prog
    pan_r = 1.0 - 0.8 * prog
    out = (ruido * 0.55 + np.column_stack((tono * pan_l, tono * pan_r))) * env[:, None]
    return _pico(out, 0.80)


def _sub_drop_suave(dur_ms: float = 1100) -> np.ndarray:
    """Golpe subgrave redondo de separador de radio."""
    t = _t(dur_ms)
    f_curva = 34.0 + 65.0 * np.exp(-t * 7.0)
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    onda = np.tanh(np.sin(fase) * 1.5) * np.exp(-t * 2.6)
    return _est(onda * 0.70)


# ---------------------------------------------------------------------------
# Sweepers Radio Redimidos (Adaptados según Mañana, Tarde o Noche)
# ---------------------------------------------------------------------------

def sintetizar_intro(momento: str) -> np.ndarray:
    """
    Intro radial con Pips horarios suaves + Whoosh FM + Impacto Grave + Melodía que cambia según la hora.
    """
    pista = _pista(2700)

    # Pips horarios cristalinos + Whoosh de entrada
    _sumar(pista, _whoosh_fm(780, 250, 4500), 40, 0.80)
    for i, pos in enumerate([160, 400, 640]):
        dur = 220 if i == 2 else 65
        f_pip = 1174.66 if i == 2 else 587.33  # D5 -> D5 -> D6
        t = _t(dur)
        pip = np.sin(2 * np.pi * f_pip * t) * np.exp(-t * (8.0 if i == 2 else 24.0))
        _sumar(pista, _est(pip * 0.32), pos)

    # Golpe en ms 800
    _sumar(pista, _sub_drop_suave(1200), 780, 0.85)
    _sumar(pista, _bombo_radio(300), 800, 1.0)

    if momento == "manana":
        # Mañana: Sol Mayor brillante ascendente
        acorde = [392.00, 493.88, 587.33, 740.00, 880.00]
        notas_logo = [(783.99, 0, 0.25), (987.77, 120, 0.75), (1174.66, 240, 0.30), (1567.98, 360, 0.70)]
        bajo_f = 98.00
    elif momento == "tarde":
        # Tarde: La Mayor / Pop moderno energético
        acorde = [440.00, 554.37, 659.25, 830.61, 987.77]
        notas_logo = [(880.00, 0, 0.3), (1108.73, 110, 0.7), (1318.51, 220, 0.3), (1760.00, 330, 0.65)]
        bajo_f = 110.00
    else:
        # Noche: Fa Mayor 9 cálido y reflexivo
        acorde = [349.23, 440.00, 523.25, 659.25, 783.99]
        notas_logo = [(698.46, 0, 0.3), (880.00, 140, 0.7), (1046.50, 280, 0.4), (1318.51, 420, 0.6)]
        bajo_f = 87.31

    _sumar(pista, _bajo_fm(bajo_f, 1600), 800, 0.95)
    _sumar(pista, _piano_calido(acorde, 1650, decay=1.8), 800, 0.95)

    logo = _pista(1800)
    for f, pos, pan in notas_logo:
        _sumar(logo, _bell_chime(f, 850, pan=pan), pos, 1.0)
    _sumar(pista, _stereo_delay(logo, 240, 360, 0.38, 0.35), 830, 0.95)

    return _fade(_reverb_estereo(pista, seg=1.5, mezcla=0.35), 10, 350)


def sintetizar_transicion(momento: str) -> np.ndarray:
    """
    Separador FM entre el Locutor 1 (Clima) y el Locutor 2 (Frase institucional):
    Whoosh rápido + Sub-drop + Stinger musical de 3 notas con eco.
    """
    pista = _pista(1550)
    _sumar(pista, _whoosh_fm(420, 3200, 400), 0, 0.85)
    _sumar(pista, _sub_drop_suave(900), 280, 0.85)
    _sumar(pista, _bombo_radio(260), 300, 0.95)
    _sumar(pista, _clap_suave(160), 300, 0.80)

    notas_stinger = (
        [587.33, 880.00, 1174.66] if momento == "manana"
        else ([659.25, 987.77, 1318.51] if momento == "tarde" else [523.25, 783.99, 1046.50])
    )
    stinger = _pista(1150)
    for i, f in enumerate(notas_stinger):
        _sumar(stinger, _bell_chime(f, 600, pan=0.3 if i % 2 == 0 else 0.7), i * 95, 1.0)
    _sumar(stinger, _piano_calido(notas_stinger, 900, decay=2.4), 180, 0.85)
    _sumar(pista, _stereo_delay(stinger, 180, 270, 0.35, 0.32), 300, 0.95)

    return _fade(_reverb_estereo(pista, seg=1.1, mezcla=0.32), 10, 250)


def sintetizar_cierre(momento: str) -> np.ndarray:
    """Cierre final redondo con cascada de campanas + acorde cálido y golpe suave."""
    pista = _pista(2150)
    _sumar(pista, _whoosh_fm(450, 400, 3500), 0, 0.65)

    notas = [1174.66, 987.77, 880.00, 783.99]
    mel = _pista(1850)
    for i, f in enumerate(notas):
        _sumar(mel, _bell_chime(f, 950, pan=0.7 - i * 0.15), i * 120, 0.95)
    _sumar(pista, _stereo_delay(mel, 260, 390, 0.35, 0.32), 0, 0.90)

    _sumar(pista, _sub_drop_suave(1200), 380, 0.85)
    _sumar(pista, _bombo_radio(320), 400, 0.95)
    _sumar(pista, _bajo_fm(98.00, 1550), 400, 0.95)
    _sumar(pista, _piano_calido([392.00, 493.88, 587.33, 783.99], 1550, decay=1.9), 400, 0.95)

    return _fade(_reverb_estereo(pista, seg=1.6, mezcla=0.38), 10, 450)


# ---------------------------------------------------------------------------
# Canción de Fondo + Efectos del Clima (Variables por Hora y Estado del Tiempo)
# ---------------------------------------------------------------------------

def bed_redimidos_dinamico(momento: str, estado_clima: str, dur_ms: float) -> np.ndarray:
    """
    Genera una canción instrumental de fondo que cambia su ritmo y acordes según la hora (Mañana/Tarde/Noche)
    y superpone efectos climáticos reales (Lluvia, Truenos, Viento o Destellos FM) según el tiempo en Resistencia.
    """
    pista = _pista(dur_ms)
    t = _t(dur_ms)

    # Tempo según la hora del día: Tarde más movida (112 BPM), Mañana fresca (106 BPM), Noche tranquila (96 BPM)
    bpm = 112.0 if momento == "tarde" else (106.0 if momento == "manana" else 96.0)
    beat_ms = 60000.0 / bpm
    compas_ms = beat_ms * 4

    # Progresiones musicales según hora y clima
    if estado_clima in ("lluvia", "tormenta"):
        # Armonía emotiva y cálida bajo la lluvia (Em9 -> Cmaj9 -> Gmaj7 -> D6)
        progresion = [
            ([329.63, 392.00, 493.88, 587.33, 740.00], [659.25, 783.99, 987.77, 1174.66], 82.41),
            ([261.63, 329.63, 392.00, 493.88, 587.33], [523.25, 659.25, 783.99, 987.77], 65.41),
            ([392.00, 493.88, 587.33, 740.00, 783.99], [783.99, 987.77, 1174.66, 1480.0], 98.00),
            ([293.66, 369.99, 440.00, 587.33, 659.25], [587.33, 740.00, 880.00, 1174.66], 73.42),
        ]
    elif momento == "tarde":
        # Tarde luminosa y arriba (Aadd9 -> E/G# -> F#m7 -> Dmaj9)
        progresion = [
            ([440.00, 554.37, 659.25, 987.77], [880.00, 1108.73, 1318.51, 987.77], 110.00),
            ([415.30, 493.88, 659.25, 783.99], [830.61, 987.77, 1318.51, 1567.98], 103.83),
            ([369.99, 440.00, 554.37, 659.25], [740.00, 880.00, 1108.73, 1318.51], 92.50),
            ([293.66, 369.99, 440.00, 554.37], [587.33, 740.00, 880.00, 1108.73], 73.42),
        ]
    else:
        # Mañana / Noche: Sol Mayor inspiracional (Gmaj9 -> D/F# -> Em7 -> Cmaj9)
        progresion = [
            ([392.00, 493.88, 587.33, 740.00], [783.99, 987.77, 1174.66, 1480.0], 98.00),
            ([369.99, 440.00, 587.33, 740.00], [740.00, 880.00, 1174.66, 1318.51], 92.50),
            ([329.63, 392.00, 493.88, 587.33], [659.25, 783.99, 987.77, 1174.66], 82.41),
            ([261.63, 329.63, 392.00, 493.88], [523.25, 659.25, 783.99, 987.77], 65.41),
        ]

    # 1. Construcción de la canción instrumental (Pad + Piano Rhodes rítmico + Bajo + Arpegio de campanas)
    num_compases = int(dur_ms / compas_ms) + 2
    musica = _pista(dur_ms)

    for c in range(num_compases):
        ini_c = c * compas_ms
        if ini_c >= dur_ms:
            break
        acorde, arp, bajo = progresion[c % len(progresion)]
        _sumar(musica, _pad_celestial(acorde, compas_ms + 250, corte_lp=1600), ini_c, 0.90)
        _sumar(musica, _piano_calido(acorde, compas_ms * 0.85, decay=1.8), ini_c, 0.85)
        # Acorde sincopado de piano en el pulso 2.5 para darle swing de canción real
        _sumar(musica, _piano_calido(acorde, beat_ms * 1.5, decay=2.5), ini_c + beat_ms * 1.5, 0.65)
        _sumar(musica, _piano_calido(acorde, beat_ms * 1.0, decay=3.0), ini_c + beat_ms * 3.0, 0.55)

        # Línea de bajo musical
        _sumar(musica, _bajo_fm(bajo, beat_ms * 1.8), ini_c, 0.90)
        _sumar(musica, _bajo_fm(bajo * 1.5 if c % 2 == 0 else bajo, beat_ms * 1.2), ini_c + beat_ms * 2.0, 0.75)
        _sumar(musica, _bajo_fm(bajo, beat_ms * 0.8), ini_c + beat_ms * 3.5, 0.70)

        # Melodía punteada (Arpegio en corcheas)
        corchea_ms = beat_ms * 0.5
        for k in range(8):
            nota_f = arp[k % len(arp)]
            pan = 0.25 if k % 2 == 0 else 0.75
            _sumar(musica, _bell_chime(nota_f, 420, pan=pan), ini_c + k * corchea_ms, 0.55)

    # Bombeo Sidechain suave para que la canción respire con la batería
    fase_beat = (t * (1000.0 / beat_ms)) % 1.0
    sidechain = 0.60 + 0.40 * (1.0 - np.exp(-fase_beat * 4.8))
    musica *= sidechain[:, None]
    pista += _stereo_delay(musica, int(beat_ms * 0.5), int(beat_ms * 0.75), 0.30, 0.26)

    # 2. Batería Pop/Acústica (Bombo en todos los tiempos, Clap suave en 2 y 4, Shakers en semicorcheas)
    bombo = _bombo_radio(240)
    clap = _clap_suave(150)
    sh_fuerte = _shaker_estereo(70, acento=True)
    sh_debil = _shaker_estereo(60, acento=False)

    for b in range(int(dur_ms / beat_ms) + 1):
        pos_b = b * beat_ms
        _sumar(pista, bombo, pos_b, 0.80)
        if b % 2 == 1:
            _sumar(pista, clap, pos_b, 0.65)
        _sumar(pista, sh_fuerte, pos_b, 0.60)
        _sumar(pista, sh_debil, pos_b + beat_ms * 0.25, 0.45)
        _sumar(pista, sh_fuerte, pos_b + beat_ms * 0.50, 0.65)
        _sumar(pista, sh_debil, pos_b + beat_ms * 0.75, 0.45)

    # 3. Efectos Climáticos y de Radio sobre la canción
    if estado_clima in ("lluvia", "tormenta"):
        # Lluvia estéreo bien clara + Truenos profundos si hay tormenta
        agua = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=1000, pasa_bajos=7500)
        oleaje = 0.75 + 0.25 * np.sin(2 * np.pi * 0.28 * t)
        pista += agua * oleaje[:, None] * 0.22
        if estado_clima == "tormenta":
            for pos_tr in (dur_ms * 0.16, dur_ms * 0.58):
                t_tr = _t(2600)
                trueno = _filtrar(np.random.uniform(-1, 1, (len(t_tr), 2)), pasa_altos=30, pasa_bajos=150)
                env_tr = np.minimum(t_tr / 0.18, 1.0) * np.exp(-t_tr * 1.3)
                _sumar(pista, _pico(trueno * env_tr[:, None], 0.80), pos_tr, 0.75)

    elif estado_clima == "viento":
        # Ráfagas de brisa aerodinámica estéreo
        brisa = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=200, pasa_bajos=850)
        mod = 0.5 + 0.5 * np.sin(2 * np.pi * 0.22 * t)
        pista += brisa * mod[:, None] * 0.22

    # Destellos de radio FM suaves cada 4.5 segundos para mantener dinamismo radial
    for pos_fx in np.arange(2200, dur_ms - 1800, 4500):
        _sumar(pista, _whoosh_fm(500, 400, 3200) * 0.35, pos_fx, 0.45)

    return _filtrar(pista, pasa_altos=40, pasa_bajos=10500)


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


# ---------------------------------------------------------------------------
# Voces Edge TTS (Elena y Tomás combinados) + Ensamblado del Spot
# ---------------------------------------------------------------------------

async def _edge_tts_bytes(texto: str, nombre_voz: str) -> bytes:
    comunicador = edge_tts.Communicate(texto, nombre_voz, rate=VELOCIDAD_VOZ)
    buffer = bytearray()
    async for chunk in comunicador.stream():
        if chunk["type"] == "audio":
            buffer.extend(chunk["data"])
    return bytes(buffer)


def sintetizar_voz(texto: str, es_mujer: bool) -> np.ndarray:
    voz_edge = "es-AR-ElenaNeural" if es_mujer else "es-AR-TomasNeural"
    audio_bytes = asyncio.run(_edge_tts_bytes(texto, voz_edge))
    seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
    return seg_a_array(seg)


def procesar_voz(x: np.ndarray) -> np.ndarray:
    """Procesamiento vocal suave: calidez, control de picos y estéreo discreto."""
    # Filtrado moderado para limpiar graves sin adelgazar la voz.
    x = _filtrar(x, pasa_altos=75, pasa_bajos=15000)
    calidez = _filtrar(x, pasa_altos=105, pasa_bajos=260)
    presencia = _filtrar(x, pasa_altos=2200, pasa_bajos=4800)
    aire = np.tanh(_filtrar(x, pasa_altos=6500) * 1.4) * 0.045
    x = x + 0.12 * calidez + 0.13 * presencia + aire

    # Micro-doubler casi imperceptible: aporta anchura sin dar sensación de voz robótica.
    d_l = int(SR * 0.012)
    d_r = int(SR * 0.016)
    doble = np.zeros_like(x)
    doble[d_l:, 0] = x[:-d_l, 0] * 0.055
    doble[d_r:, 1] = x[:-d_r, 1] * 0.055
    x = x + _filtrar(doble, pasa_altos=280, pasa_bajos=4200)

    # Compresión más tranquila para conservar los cambios naturales de la locución.
    x = _comprimir(x, thr_db=-24.0, ratio=2.3, ataque_ms=9.0, relajo_ms=155.0)
    return _pico(x, 0.86)


def agregar_eco_final_frases(x: np.ndarray) -> np.ndarray:
    """Añade un eco corto y discreto a los finales de frase detectados por las pausas.

    No modifica ni elimina palabras. Solo duplica suavemente el final de la frase,
    aprovechando las pausas naturales de la locución; al final deja una cola breve.
    """
    if x.size == 0:
        return x

    tam_bloque = max(1, int(SR * 0.010))  # análisis de energía en bloques de 10 ms
    cantidad = int(np.ceil(len(x) / tam_bloque))
    mono = np.max(np.abs(x), axis=1)
    relleno = np.pad(mono, (0, cantidad * tam_bloque - len(mono)))
    bloques = relleno.reshape(cantidad, tam_bloque)
    rms = np.sqrt(np.mean(bloques ** 2, axis=1))
    umbral = max(float(np.max(rms)) * 0.045, 0.0035)
    activo = rms > umbral

    # Detecta tramos hablados y une micro-pausas internas de hasta 110 ms.
    indices = np.flatnonzero(activo)
    if len(indices) == 0:
        return x
    tramos = []
    inicio = fin = int(indices[0])
    for indice in indices[1:]:
        indice = int(indice)
        if indice - fin <= 12:
            fin = indice
        else:
            tramos.append((inicio, fin + 1))
            inicio = fin = indice
    tramos.append((inicio, fin + 1))
    tramos = [(a * tam_bloque, min(b * tam_bloque, len(x)))
              for a, b in tramos if (b - a) >= 7]

    if not tramos:
        return x

    # Solo ampliamos el audio si el eco final no entra en el silencio que ya trae el TTS.
    fin_ultima_frase = tramos[-1][1]
    longitud_necesaria = max(len(x), fin_ultima_frase + int(SR * 0.32))
    salida = np.pad(x, ((0, longitud_necesaria - len(x)), (0, 0)))
    retardo_1 = int(SR * 0.045)
    retardo_2 = int(SR * 0.165)

    for i, (inicio, fin) in enumerate(tramos):
        es_ultimo = i == len(tramos) - 1
        siguiente_inicio = tramos[i + 1][0] if not es_ultimo else len(x)
        pausa_ms = (siguiente_inicio - fin) * 1000.0 / SR

        # No se fuerza un eco en pausas muy cortas para evitar que ensucie la siguiente frase.
        if not es_ultimo and pausa_ms < 215:
            continue

        duracion_eco = min(int(SR * 0.135), max(int(SR * 0.075), fin - inicio))
        fuente = x[max(inicio, fin - duracion_eco):fin].copy()
        if len(fuente) < int(SR * 0.060):
            continue

        # Fade-in corto y caída exponencial: eco audible, pero no exagerado.
        t = np.linspace(0.0, 1.0, len(fuente), endpoint=True)
        envolvente = np.exp(-3.0 * t)
        fade_in = min(int(SR * 0.008), len(fuente))
        if fade_in > 1:
            envolvente[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
        eco = fuente * envolvente[:, None]

        pos_1 = fin + retardo_1
        limite_1 = min(pos_1 + len(eco), len(salida))
        if limite_1 > pos_1:
            salida[pos_1:limite_1] += eco[:limite_1 - pos_1] * 0.15

        # Segunda repetición todavía más baja, solo si hay espacio suficiente o es el final.
        if es_ultimo or pausa_ms >= 320:
            pos_2 = fin + retardo_2
            limite_2 = min(pos_2 + len(eco), len(salida))
            if limite_2 > pos_2:
                salida[pos_2:limite_2] += eco[:limite_2 - pos_2] * 0.065

    return salida


def generar_spot(texto_voz_1: str, texto_voz_2: str, estado_clima: str, path_salida: str) -> None:
    ahora = datetime.now(ZONA_HORARIA)
    momento = momento_del_dia(ahora.hour)

    # Rotación por hora: Hora par -> Abre Mujer (Clima) y cierra Hombre (Frase FM). Hora impar -> Al revés.
    abre_mujer = (ahora.hour % 2 == 0)
    if abre_mujer:
        print(f"Dúo Redimidos (Hora Par - {momento}): Abre Elena (Clima) -> Transición FM -> Cierra Tomás (Institucional)")
    else:
        print(f"Dúo Redimidos (Hora Impar - {momento}): Abre Tomás (Clima) -> Transición FM -> Cierra Elena (Institucional)")

    voz_1 = agregar_eco_final_frases(procesar_voz(sintetizar_voz(texto_voz_1, abre_mujer)))
    voz_2 = agregar_eco_final_frases(procesar_voz(sintetizar_voz(texto_voz_2, not abre_mujer)))

    intro = _pico(sintetizar_intro(momento), 0.94)
    trans = _pico(sintetizar_transicion(momento), 0.94)
    cierre = _pico(sintetizar_cierre(momento), 0.94)

    # Tiempos de enganche estilo operador de FM
    inicio_v1 = 1480.0
    fin_v1 = inicio_v1 + _dur_ms(voz_1)

    inicio_trans = fin_v1 - 120.0
    inicio_v2 = inicio_trans + 820.0
    fin_v2 = inicio_v2 + _dur_ms(voz_2)

    inicio_cierre = fin_v2 - 180.0
    total_ms = inicio_cierre + _dur_ms(cierre)

    # 1. Bus de Voces (Elena + Tomás)
    bus_voces = _pista(total_ms + 400)
    _sumar(bus_voces, voz_1, inicio_v1, 1.0)
    _sumar(bus_voces, voz_2, inicio_v2, 1.0)

    # 2. Bus de Canción de Fondo + Clima (Acompaña todo el spot desde el golpe de la intro)
    dur_cancion = total_ms - 600.0
    print(f"Generando canción de fondo ({momento}) + efecto climático ({estado_clima})...")
    cancion_fondo = _fade(_rms(bed_redimidos_dinamico(momento, estado_clima, dur_cancion), REFERENCIA_RMS_DB), 350, 900)

    bus_cortina = _pista(total_ms + 400)
    _sumar(bus_cortina, cancion_fondo, 800.0, _g(AMBIENTE_DB))

    # Si subiste un archivo propio assets/cama.mp3, se suma también
    seg_cama_ext = buscar_asset("cama")
    if seg_cama_ext is not None:
        cama_ext = _rms(seg_a_array(seg_cama_ext), REFERENCIA_RMS_DB)
        _sumar(bus_cortina, _extender(cama_ext, len(bus_cortina)), 0.0, _g(CAMA_EXTERNA_DB))

    bus_cortina = _ducking_por_voz(bus_cortina, bus_voces, reduccion_db=-3.8)

    # 3. Bus de Sweepers FM (Intro, Transición del medio y Cierre)
    bus_fx = _pista(total_ms + 400)
    _sumar(bus_fx, intro, 0.0, _g(EFECTOS_DB))
    _sumar(bus_fx, trans, inicio_trans, _g(EFECTOS_DB))
    _sumar(bus_fx, cierre, inicio_cierre, _g(EFECTOS_DB))

    # 4. Masterización Multibanda + Ensanchador Estéreo FM
    master = (bus_voces + bus_cortina + bus_fx)[: int(SR * total_ms / 1000)]
    master = _filtrar(master, pasa_altos=35)

    graves = _comprimir(_filtrar(master, pasa_bajos=200), thr_db=-18.0, ratio=3.2, ataque_ms=10.0, relajo_ms=150.0)
    medios = _comprimir(_filtrar(master, pasa_altos=200, pasa_bajos=4200), thr_db=-17.0, ratio=2.6, ataque_ms=8.0, relajo_ms=140.0)
    agudos = _comprimir(_filtrar(master, pasa_altos=4200), thr_db=-19.0, ratio=2.4, ataque_ms=4.0, relajo_ms=90.0)
    master = graves * 1.08 + medios * 1.0 + agudos * 1.12

    mid = 0.5 * (master[:, 0] + master[:, 1])
    side = 0.5 * (master[:, 0] - master[:, 1])
    side_amplio = _filtrar(_est(side), pasa_altos=220)[:, 0] * 1.28
    master = np.column_stack((mid + side_amplio, mid - side_amplio))

    master = _comprimir(master, thr_db=-13.5, ratio=3.8, ataque_ms=5.0, relajo_ms=120.0)
    final = _pico(np.tanh(master * 1.15), 0.95)

    array_a_seg(final).export(path_salida, format="mp3", bitrate="192k")
    print(f"¡Spot Dúo Redimidos ({len(final) / SR:.1f} s) guardado exitosamente en {path_salida}!")


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
            print(f"Subido correctamente a {remote_path} ({host})")
        finally:
            sftp.close()
    finally:
        transport.close()


def main():
    print(f"=== GENERANDO SPOT DUO FM PARA: {NOMBRE_EMISORA} ===")
    sftp_host = os.environ.get("SFTP_HOST", "").strip()
    sftp_port = int(os.environ.get("SFTP_PORT") or "2022")
    sftp_user = os.environ.get("SFTP_USER", "").strip()
    sftp_pass = os.environ.get("SFTP_PASS", "").strip()
    sftp_dir = os.environ.get("SFTP_REMOTE_DIR") or "/clima"

    faltantes = [n for n, v in [("SFTP_HOST", sftp_host), ("SFTP_USER", sftp_user), ("SFTP_PASS", sftp_pass)] if not v]
    if faltantes:
        print(f"Faltan variables SFTP: {', '.join(faltantes)}", file=sys.stderr)
        sys.exit(1)

    datos_clima = obtener_clima()
    texto_voz_1, texto_voz_2 = armar_dialogo_spot(datos_clima)
    estado_clima = analizar_estado_clima(datos_clima)

    generar_spot(texto_voz_1, texto_voz_2, estado_clima, LOCAL_MP3)
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
