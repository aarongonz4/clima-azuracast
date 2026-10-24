#!/usr/bin/env python3
"""
Spot automático exclusivo para Radio Redimidos por Jesús (run_emisora.py).
Estilo de audio: Inspiracional, Cálido y Armónico (Piano Rhodes, Pad Celestial, Chimes suaves y Percusión Acústica ligera).
Voces: Microsoft Edge TTS (es-AR Elena y Tomás) turnándose por hora.
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
VELOCIDAD_VOZ = os.environ.get("VELOCIDAD_VOZ", "+4%").strip()

CIUDAD = "Resistencia"
LAT = -27.4514
LON = -58.9867
ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")

LOCAL_MP3 = "clima.mp3"
REMOTE_FILENAME = "clima.mp3"

ASSETS_DIR = "assets"
EXTENSIONES = (".mp3", ".wav", ".ogg", ".m4a", ".flac")
SR = 44100

REFERENCIA_RMS_DB = -19.0
EFECTOS_DB = -3.0
AMBIENTE_DB = -10.5

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
        return random.choice(["Buenos días, bendiciones", "Muy buenos días", "Hola, excelente mañana"])
    elif 12 <= hora_actual < 20:
        return random.choice(["Buenas tardes, bendiciones", "Muy buenas tardes", "Hola, excelente tarde"])
    return random.choice(["Buenas noches, bendiciones", "Muy buenas noches", "Excelente noche para todos"])


# ---------------------------------------------------------------------------
# Clima
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


def armar_texto_clima(data: dict | None) -> str:
    ahora = datetime.now(ZONA_HORARIA)
    hora_str = hora_en_espanol(ahora.hour, ahora.minute)
    saludo = saludo_segun_hora(ahora.hour)

    if not data or "current" not in data:
        return f"{saludo}. En Argentina son las {hora_str}. Seguimos en la sintonía de {NOMBRE_EMISORA}, acompañándote en cada momento."

    actual = data["current"]
    temp = round(actual["temperature_2m"])
    sensacion = round(actual["apparent_temperature"])
    humedad = round(actual["relative_humidity_2m"])
    viento = round(actual["wind_speed_10m"])
    descripcion = CODIGOS_CLIMA.get(actual["weather_code"], "condiciones variables")

    plantillas = [
        f"{saludo}. En Argentina son las {hora_str}. {NOMBRE_EMISORA} te informa los datos del tiempo en {CIUDAD}: tenemos {descripcion}, con {temp} grados de temperatura y una sensación térmica de {sensacion} grados. Humedad del {humedad} por ciento. Seguimos acompañándote con la mejor música en {NOMBRE_EMISORA}.",
        f"{saludo}. Pasaron las {hora_str} en todo el país. Compartimos el estado del tiempo para {CIUDAD}: cielo con {descripcion}, {temp} grados de temperatura y {sensacion} de sensación térmica. La humedad es del {humedad} por ciento y vientos de {viento} kilómetros por hora. Quedate en la compañía de {NOMBRE_EMISORA}.",
    ]
    return random.choice(plantillas)


def elegir_ambiente(data: dict | None) -> str:
    hora = datetime.now(ZONA_HORARIA).hour
    es_dia = 6 <= hora < 20
    if not data or "current" not in data:
        return "dia" if es_dia else "noche"
    codigo = data["current"].get("weather_code", 0)
    if codigo in (95, 96, 99, 51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "lluvia"
    return "dia" if es_dia else "noche"


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


def _ducking_por_voz(fondo: np.ndarray, pista_voz: np.ndarray, reduccion_db: float = -5.0) -> np.ndarray:
    n = min(len(fondo), len(pista_voz))
    blk = int(SR * 0.015)
    nb = n // blk + 1
    env = np.pad(np.abs(pista_voz[:n]).max(axis=1), (0, nb * blk - n)).reshape(nb, blk).max(axis=1)
    hablando = (env > 0.04).astype(np.float64)

    a = np.exp(-15.0 / 30.0)
    r = np.exp(-15.0 / 280.0)
    suave = np.zeros(nb)
    val = 0.0
    for i in range(nb):
        coef = a if hablando[i] > val else r
        val = coef * val + (1.0 - coef) * hablando[i]
        suave[i] = val

    centros = (np.arange(nb) + 0.5) * blk
    curva_db = np.interp(np.arange(len(fondo)), centros, suave * reduccion_db)
    return fondo * _g_array(curva_db)[:, None]


def _stereo_delay(x: np.ndarray, delay_l_ms: float = 280.0, delay_r_ms: float = 420.0, feedback: float = 0.35, mezcla: float = 0.30) -> np.ndarray:
    out = x.copy()
    dl = int(SR * delay_l_ms / 1000)
    dr = int(SR * delay_r_ms / 1000)
    eco = _filtrar(x, pasa_altos=220, pasa_bajos=3500)
    for rep in range(1, 4):
        gan = (feedback ** rep) * mezcla
        desp_l = rep * dl
        desp_r = rep * dr
        if desp_l < len(out):
            out[desp_l:, 0] += eco[:-desp_l, 1] * gan
        if desp_r < len(out):
            out[desp_r:, 1] += eco[:-desp_r, 0] * gan
    return out


def _reverb_estereo(x: np.ndarray, seg: float = 1.5, mezcla: float = 0.36, brillo: float = 4500.0) -> np.ndarray:
    ir_n = int(SR * seg)
    t = np.arange(ir_n) / SR
    rng = np.random.default_rng(55)
    ir = rng.standard_normal((ir_n, 2)) * np.exp(-5.5 * t / seg)[:, None]
    ir = _filtrar(ir, pasa_altos=180, pasa_bajos=brillo)
    predelay = int(SR * 0.024)
    ir = np.pad(ir, ((predelay, 0), (0, 0)))[:ir_n]
    ir /= (np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True)) + 1e-9)

    largo = x.shape[0] + ir_n
    nfft = 1 << (largo - 1).bit_length()
    humedo = np.fft.irfft(np.fft.rfft(x, nfft, axis=0) * np.fft.rfft(ir, nfft, axis=0), nfft, axis=0)[:largo]
    seco = np.pad(x, ((0, ir_n), (0, 0)))
    return seco + humedo * mezcla


# ---------------------------------------------------------------------------
# Instrumentos Estilo Inspiracional / Cálido (Redimidos)
# ---------------------------------------------------------------------------

def _piano_calido(freqs: list[float], dur_ms: float, decay: float = 2.0) -> np.ndarray:
    """Acorde de Piano Eléctrico / Acústico cálido con armónicos suaves."""
    t = _t(dur_ms)
    out = np.zeros((len(t), 2))
    ataque = min(int(SR * 0.010), len(t))
    for i, f in enumerate(freqs):
        martillo = 0.20 * np.sin(2 * np.pi * f * 3.0 * t) * np.exp(-t * 16.0)
        onda_l = np.sin(2 * np.pi * f * t + martillo) + 0.18 * np.sin(2 * np.pi * f * 2.0 * t)
        onda_r = np.sin(2 * np.pi * (f * 1.0012) * t + martillo) + 0.18 * np.sin(2 * np.pi * (f * 2.002) * t)
        env = np.exp(-decay * t)
        env[:ataque] *= np.linspace(0, 1, ataque)
        pan = 0.3 + 0.4 * (i / max(1, len(freqs) - 1))
        out[:, 0] += onda_l * env * pan
        out[:, 1] += onda_r * env * (1.0 - pan)
    return _filtrar(out, pasa_altos=95, pasa_bajos=3400) * 0.32


def _bell_chime(freq: float, dur_ms: float = 900, pan: float = 0.5) -> np.ndarray:
    """Campana cristalina suave estilo Worship / Inspiracional."""
    t = _t(dur_ms)
    mod = 1.6 * np.exp(-t * 12.0) * np.sin(2 * np.pi * (freq * 3.0) * t)
    onda = np.sin(2 * np.pi * freq * t + mod) + 0.25 * np.sin(2 * np.pi * (freq * 2.0) * t)
    env = np.exp(-t * 3.2)
    ataque = min(int(SR * 0.005), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    mono = onda * env * 0.28
    return np.column_stack((mono * pan, mono * (1.0 - pan)))


def _pad_celestial(freqs: list[float], dur_ms: float) -> np.ndarray:
    """Colchón ambiental envolvente y sereno."""
    t = _t(dur_ms)
    out = np.zeros((len(t), 2))
    rng = np.random.default_rng(7)
    for f in freqs:
        for det, pl, pr in [(-0.006, 0.8, 0.2), (0.0, 0.5, 0.5), (0.006, 0.2, 0.8)]:
            fa = f * (1.0 + det)
            fase = rng.uniform(0, 1)
            saw = 2.0 * ((t * fa + fase) % 1.0) - 1.0
            out[:, 0] += saw * pl
            out[:, 1] += saw * pr
    out = _filtrar(out, pasa_altos=90, pasa_bajos=1250)
    return _fade(out * 0.14, 400, 500)


def _bajo_acustico(freq: float, dur_ms: float) -> np.ndarray:
    t = _t(dur_ms)
    onda = np.tanh((np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2.0 * t)) * 1.3)
    env = np.exp(-2.8 * t)
    ataque = min(int(SR * 0.012), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    return _est(_filtrar(_est(onda * env), pasa_bajos=200)[:, 0] * 0.40)


def _bombo_suave(dur_ms: float = 280) -> np.ndarray:
    t = _t(dur_ms)
    f_env = 44.0 + 90.0 * np.exp(-t * 30.0)
    fase = 2 * np.pi * np.cumsum(f_env) / SR
    cuerpo = np.tanh(np.sin(fase) * 1.4) * np.exp(-t * 9.5)
    return _est(cuerpo * 0.65)


def _shaker_acustico(dur_ms: float = 90) -> np.ndarray:
    t = _t(dur_ms)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=5500, pasa_bajos=11500)
    env = np.sin(np.pi * (t / (dur_ms / 1000.0))) ** 2
    return ruido * env[:, None] * 0.14


# ---------------------------------------------------------------------------
# Sweepers Cálidos para Radio Redimidos
# ---------------------------------------------------------------------------

def sintetizar_intro() -> np.ndarray:
    """Intro luminosa y serena: Swell de Piano en Sol Mayor 9 + Arpegio de campanas cálidas."""
    pista = _pista(2700)
    # Swell de entrada suave
    t = _t(850)
    swell = _piano_calido([392.00, 493.88, 587.33, 740.00], 850, decay=0.2) * ((t / 0.85) ** 2.5)[:, None]
    _sumar(pista, swell, 0, 0.85)

    # Acorde principal Gmaj9 en ms 820
    _sumar(pista, _bombo_suave(350), 820, 0.9)
    _sumar(pista, _bajo_acustico(98.00, 1700), 820, 0.95)
    _sumar(pista, _piano_calido([392.00, 493.88, 587.33, 740.00, 880.00], 1700, decay=1.8), 820, 0.90)

    # Melodía inspiracional de 4 notas (G5 -> A5 -> B5 -> D6)
    notas = [(783.99, 0, 0.3), (880.00, 150, 0.7), (987.77, 300, 0.35), (1174.66, 450, 0.65)]
    logo = _pista(1800)
    for f, pos, pan in notas:
        _sumar(logo, _bell_chime(f, 950, pan=pan), pos, 0.95)
    _sumar(pista, _stereo_delay(logo, 260, 390, 0.38, 0.35), 840, 0.90)

    return _fade(_reverb_estereo(pista, seg=1.6, mezcla=0.38), 10, 400)


def sintetizar_cierre() -> np.ndarray:
    """Cierre cálido con resolución en Sol Mayor y campanas suaves."""
    pista = _pista(2200)
    notas = [(1174.66, 0, 0.7), (987.77, 150, 0.3), (880.00, 300, 0.65), (783.99, 450, 0.5)]
    mel = _pista(1900)
    for f, pos, pan in notas:
        _sumar(mel, _bell_chime(f, 1100, pan=pan), pos, 0.90)
    _sumar(pista, _stereo_delay(mel, 280, 420, 0.35, 0.32), 0, 0.90)

    _sumar(pista, _bombo_suave(350), 450, 0.90)
    _sumar(pista, _bajo_acustico(98.00, 1600), 450, 0.95)
    _sumar(pista, _piano_calido([392.00, 493.88, 587.33, 783.99], 1600, decay=2.0), 450, 0.95)

    return _fade(_reverb_estereo(pista, seg=1.7, mezcla=0.40), 10, 500)


def bed_redimidos(modo: str, dur_ms: float) -> np.ndarray:
    """
    Cortina musical inspiracional a 96 BPM:
    Progresión Gmaj9 -> D/F# -> Em7 -> Cmaj9 con Piano Eléctrico, Pad Celestial y Shaker suave.
    """
    pista = _pista(dur_ms)
    t = _t(dur_ms)
    beat_ms = 625.0  # 96 BPM (tempo tranquilo y amable)
    compas_ms = beat_ms * 4

    progresion = [
        ([392.00, 493.88, 587.33, 740.00], [783.99, 987.77, 1174.66], 98.00),   # Gmaj7
        ([369.99, 440.00, 587.33, 740.00], [740.00, 880.00, 1174.66], 92.50),   # D/F#
        ([329.63, 392.00, 493.88, 587.33], [659.25, 783.99, 987.77], 82.41),    # Em7
        ([261.63, 329.63, 392.00, 493.88], [523.25, 659.25, 783.99], 65.41),    # Cmaj7
    ]

    num_compases = int(dur_ms / compas_ms) + 2
    armonia = _pista(dur_ms)

    for c in range(num_compases):
        ini_c = c * compas_ms
        if ini_c >= dur_ms:
            break
        acorde, arp, bajo = progresion[c % len(progresion)]
        _sumar(armonia, _pad_celestial(acorde, compas_ms + 300), ini_c, 0.90)
        _sumar(armonia, _piano_calido(acorde, compas_ms * 0.95, decay=1.6), ini_c, 0.75)
        _sumar(armonia, _bajo_acustico(bajo, beat_ms * 2.2), ini_c, 0.80)
        _sumar(armonia, _bajo_acustico(bajo, beat_ms * 1.5), ini_c + beat_ms * 2.0, 0.65)

        # Arpegio suave de campana/piano en cada tiempo del compás
        for b in range(4):
            nota_arp = arp[b % len(arp)]
            pan = 0.35 if b % 2 == 0 else 0.65
            _sumar(armonia, _bell_chime(nota_arp, 550, pan=pan), ini_c + b * beat_ms + beat_ms * 0.5, 0.42)

    pista += _stereo_delay(armonia, 312, 468, 0.30, 0.25)

    # Pulso rítmico súper suave (Bombo redondo en 1 y 3, Shaker acústico en corcheas)
    bombo = _bombo_suave(260)
    shaker = _shaker_acustico(80)
    for b in range(int(dur_ms / beat_ms) + 1):
        pos_b = b * beat_ms
        if b % 2 == 0:
            _sumar(pista, bombo, pos_b, 0.65)
        _sumar(pista, shaker, pos_b, 0.55)
        _sumar(pista, shaker * 0.6, pos_b + beat_ms * 0.5, 0.45)

    if modo == "lluvia":
        agua = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=1100, pasa_bajos=6800)
        pista += agua * 0.14

    return _filtrar(pista, pasa_altos=40, pasa_bajos=9500)


# ---------------------------------------------------------------------------
# Voces Edge TTS (Elena y Tomás) + Ensamblado
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
    print(f"Locución con voz argentina: {voz_edge}")
    audio_bytes = asyncio.run(_edge_tts_bytes(texto, voz_edge))
    seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
    return seg_a_array(seg)


def procesar_voz(x: np.ndarray) -> np.ndarray:
    x = _filtrar(x, pasa_altos=80)
    calidez = _filtrar(x, pasa_altos=110, pasa_bajos=250)
    presencia = _filtrar(x, pasa_altos=2500, pasa_bajos=5600)
    x = x + 0.22 * calidez + 0.25 * presencia
    x = _comprimir(x, thr_db=-22.0, ratio=3.4, ataque_ms=5.0, relajo_ms=130.0)
    return _pico(x, 0.90)


def generar_spot(texto_clima: str, ambiente_nombre: str, path_salida: str) -> None:
    ahora = datetime.now(ZONA_HORARIA)
    es_mujer = (ahora.hour % 2 == 0)

    voz_clima = procesar_voz(sintetizar_voz(texto_clima, es_mujer))
    intro = _pico(sintetizar_intro(), 0.92)
    cierre = _pico(sintetizar_cierre(), 0.92)

    dur_cama = _dur_ms(voz_clima) + 1200
    ambiente = _fade(_rms(bed_redimidos(ambiente_nombre, dur_cama), REFERENCIA_RMS_DB), 400, 900)

    inicio_clima = 1450.0
    fin_clima = inicio_clima + _dur_ms(voz_clima)
    inicio_cierre = fin_clima - 200.0
    total_ms = inicio_cierre + _dur_ms(cierre)

    bus_voces = _pista(total_ms + 400)
    _sumar(bus_voces, voz_clima, inicio_clima, 1.0)

    bus_cortina = _pista(total_ms + 400)
    _sumar(bus_cortina, ambiente, 820.0, _g(AMBIENTE_DB))
    bus_cortina = _ducking_por_voz(bus_cortina, bus_voces, reduccion_db=-5.0)

    bus_fx = _pista(total_ms + 400)
    _sumar(bus_fx, intro, 0.0, _g(EFECTOS_DB))
    _sumar(bus_fx, cierre, inicio_cierre, _g(EFECTOS_DB))

    master = (bus_voces + bus_cortina + bus_fx)[: int(SR * total_ms / 1000)]
    master = _comprimir(_filtrar(master, pasa_altos=35), thr_db=-14.5, ratio=3.0, ataque_ms=10.0, relajo_ms=150.0)
    final = _pico(np.tanh(master * 1.12), 0.94)

    array_a_seg(final).export(path_salida, format="mp3", bitrate="192k")
    print(f"¡Spot Redimidos ({len(final) / SR:.1f} s) guardado exitosamente en {path_salida}!")


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
    print(f"=== GENERANDO SPOT ESTILO CALIDO PARA: {NOMBRE_EMISORA} ===")
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
    texto_clima = armar_texto_clima(datos_clima)
    ambiente_nombre = elegir_ambiente(datos_clima)

    generar_spot(texto_clima, ambiente_nombre, LOCAL_MP3)
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
