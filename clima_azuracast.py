#!/usr/bin/env python3
"""
Spot automático de clima y noticias para AzuraCast (Nogaradio) - versión "radio pro".

Qué hace:
  - Clima (Open-Meteo, con reintentos) + noticias rotativas (RSS) + Google Cloud TTS a dos voces.
  - Sweepers modernos: intro (riser + impacto + chime), transición tipo noticiero y cierre.
  - AMBIENTE SEGÚN EL CLIMA bajo la voz del clima:
        tormenta -> lluvia + truenos | lluvia/llovizna -> lluvia | nublado/niebla/nieve -> viento
        despejado de día -> pájaros | despejado de noche -> grillos | despejado con calor -> chicharras
  - Procesado de voz (EQ, compresión), cama musical con ducking automático y masterización final.

Todos los sonidos se sintetizan solos si no hay archivos. Si existen archivos en assets/, se usan
en su lugar (y si hay varios, elige uno al azar):
    assets/intro.mp3  intro2.mp3 ...        (sweeper de entrada)
    assets/transicion.mp3                   (entre clima y noticias)
    assets/cierre.mp3                       (cierre)
    assets/cama.mp3                         (música instrumental de fondo para todo el spot)
    assets/noticias_cama.mp3                (fondo opcional solo bajo las noticias)
    assets/clima/lluvia.mp3  tormenta.mp3  viento.mp3  pajaros.mp3  grillos.mp3  chicharras.mp3
Formatos aceptados: mp3, wav, ogg, m4a, flac.
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

# Niveles de mezcla en dB. Más negativo = más bajo.
# Ambientes y camas se normalizan a REFERENCIA_RMS_DB antes de aplicar estos valores.
REFERENCIA_RMS_DB = -20.0
EFECTOS_DB = -5.0          # sweepers (intro, transición, cierre)
AMBIENTE_DB = -13.0        # ambiente del clima bajo la voz
CAMA_DB_LIBRE = -5.0       # cama musical cuando no habla nadie
CAMA_DB_VOZ = -13.0        # cama musical bajo la voz (ducking)
CAMA_NOTICIAS_DB = -14.0   # fondo opcional de noticias

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
# Clima (con reintentos y plan de emergencia)
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
                print(f"Esperando {espera} segundos antes de reintentar...")
                time.sleep(espera)

    print("AVISO: Open-Meteo no respondió. Se usará locución sin datos del clima.", file=sys.stderr)
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


def elegir_ambiente(data: dict | None) -> str | None:
    """Decide qué sonido ambiente acompaña la voz del clima."""
    if not data or "current" not in data:
        return None
    actual = data["current"]
    codigo = actual.get("weather_code")
    temp = actual.get("temperature_2m")
    hora = datetime.now(ZONA_HORARIA).hour
    es_dia = 6 <= hora < 19

    if codigo in (95, 96, 99):
        return "tormenta"
    if codigo in (51, 53, 55, 61, 63, 65, 80, 81, 82):
        return "lluvia"
    if codigo in (3, 45, 48, 71, 73, 75):
        return "viento"
    if codigo in (0, 1, 2):
        if not es_dia:
            return "grillos"
        if temp is not None and temp >= 32:
            return "chicharras"
        return "pajaros"
    return None


# ---------------------------------------------------------------------------
# Noticias (con camuflaje anti-bots y limpieza)
# ---------------------------------------------------------------------------

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
        print(f"Error bloqueado por la web al leer el feed {url_feed}: {e}", file=sys.stderr)
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
# Utilidades de audio (todo se maneja como arrays estéreo float en [-1, 1])
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


def _pico(x: np.ndarray, objetivo: float) -> np.ndarray:
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


def _filtrar(x: np.ndarray, pasa_altos: float | None = None, pasa_bajos: float | None = None,
             orden: int = 2) -> np.ndarray:
    """Filtro por FFT (fase cero). pasa_altos corta graves; pasa_bajos corta agudos."""
    n = x.shape[0]
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    mascara = np.ones_like(f)
    if pasa_bajos:
        mascara *= 1.0 / (1.0 + (f / pasa_bajos) ** (2 * orden))
    if pasa_altos:
        h = (f / pasa_altos) ** (2 * orden)
        mascara *= h / (1.0 + h)
    return np.fft.irfft(X * mascara[:, None], n=n, axis=0)


def _comprimir(x: np.ndarray, thr_db: float = -20.0, ratio: float = 3.0,
               ataque_ms: float = 8.0, relajo_ms: float = 120.0, bloque_ms: float = 5.0) -> np.ndarray:
    """Compresor de picos con ataque/relajo."""
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


def _g_array(db: np.ndarray) -> np.ndarray:
    return 10 ** (db / 20.0)


def _reverb(x: np.ndarray, seg: float = 1.0, mezcla: float = 0.5, brillo: float = 6000.0) -> np.ndarray:
    """Reverb por convolución con una respuesta sintética (ruido con decaimiento)."""
    ir_n = int(SR * seg)
    t = np.arange(ir_n) / SR
    rng = np.random.default_rng(7)
    ir = rng.standard_normal((ir_n, 2)) * np.exp(-6.9 * t / seg)[:, None]
    ir = _filtrar(ir, pasa_bajos=brillo)
    ir = ir / (np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True)) + 1e-9)

    largo = x.shape[0] + ir_n
    nfft = 1 << (largo - 1).bit_length()
    húmedo = np.fft.irfft(np.fft.rfft(x, nfft, axis=0) * np.fft.rfft(ir, nfft, axis=0), nfft, axis=0)[:largo]
    seco = np.pad(x, ((0, ir_n), (0, 0)))
    return seco + húmedo * mezcla


def _extender(x: np.ndarray, n: int) -> np.ndarray:
    """Recorta (desde un punto al azar) o repite con crossfade hasta tener n muestras."""
    if len(x) >= n:
        ini = random.randint(0, len(x) - n)
        return x[ini:ini + n].copy()
    cf = max(1, min(int(SR * 0.6), len(x) // 3))
    salida = x.copy()
    while len(salida) < n:
        fade = np.linspace(0, 1, cf)[:, None]
        salida[-cf:] = salida[-cf:] * (1 - fade) + x[:cf] * fade
        salida = np.vstack((salida, x[cf:]))
    return salida[:n]


# ---------------------------------------------------------------------------
# Archivos propios (assets/)
# ---------------------------------------------------------------------------

def buscar_asset(base: str) -> AudioSegment | None:
    """Busca assets/<base>*.ext y devuelve uno al azar si hay varios."""
    candidatos = []
    for ext in EXTENSIONES:
        candidatos += glob.glob(os.path.join(ASSETS_DIR, f"{base}*{ext}"))
    if not candidatos:
        return None
    ruta = random.choice(sorted(candidatos))
    print(f"Usando archivo propio: {ruta}")
    return AudioSegment.from_file(ruta)


def cargar_pista_opcional(base: str) -> np.ndarray | None:
    seg = buscar_asset(base)
    if seg is None:
        return None
    return _rms(seg_a_array(seg), REFERENCIA_RMS_DB)


# ---------------------------------------------------------------------------
# Sonidos sintetizados: bloques básicos
# ---------------------------------------------------------------------------

def _nota(freq: float, dur_ms: float, decay: float = 4.0) -> np.ndarray:
    """Nota tipo campana (mono)."""
    t = _t(dur_ms)
    onda = (np.sin(2 * np.pi * freq * t)
            + 0.40 * np.sin(2 * np.pi * freq * 2 * t)
            + 0.20 * np.sin(2 * np.pi * freq * 3 * t))
    env = np.exp(-decay * t)
    ataque = min(int(SR * 0.005), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    return onda * env * 0.35


def _pluck(freq: float, dur_ms: float, decay: float = 7.0) -> np.ndarray:
    """Nota 'pluck' brillante tipo sintetizador (mono)."""
    t = _t(dur_ms)
    onda = sum(np.sin(2 * np.pi * freq * k * t) / k for k in range(1, 7))
    env = np.exp(-decay * t)
    ataque = min(int(SR * 0.003), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    return onda * env * 0.22


def _impacto(dur_ms: float = 1400) -> np.ndarray:
    """Golpe grave con caída de tono + ráfaga de ruido (estéreo)."""
    t = _t(dur_ms)
    fase = 2 * np.pi * np.cumsum(45 + 80 * np.exp(-t * 12)) / SR
    sub = np.sin(fase) * np.exp(-t * 2.4)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 1)), pasa_bajos=3500)[:, 0] * np.exp(-t * 14)
    return _fade(_est(sub * 0.9 + ruido * 0.5) * 0.8, 0, 80)


def _riser(dur_ms: float) -> np.ndarray:
    """Subida de tensión: ruido que crece + barrido de tono ascendente (estéreo)."""
    t = _t(dur_ms)
    prog = t / (dur_ms / 1000.0)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=500, pasa_bajos=9000)
    fase = 2 * np.pi * np.cumsum(200 * (2 ** (3.2 * prog))) / SR
    tono = np.sin(fase) * 0.25
    x = (ruido * 0.5 + tono[:, None]) * (prog ** 2.2)[:, None]
    corte = int(SR * 0.015)
    x[-corte:] *= np.linspace(1, 0, corte)[:, None]
    return x * 0.7


def _barrido_ruido(dur_ms: float, subida: bool = True) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    r = _filtrar(np.random.uniform(-1, 1, (n, 2)), pasa_altos=400, pasa_bajos=7000)
    prog = np.linspace(0, 1, n)
    env = prog ** 2 if subida else (1 - prog) ** 2
    return _pico(r * env[:, None], 0.8)


# ---------------------------------------------------------------------------
# Sweepers sintetizados
# ---------------------------------------------------------------------------

def sintetizar_intro() -> np.ndarray:
    pista = _pista(2200)
    _sumar(pista, _riser(800), 0, 0.9)
    _sumar(pista, _impacto(1400), 800, 1.0)
    for i, f in enumerate([523.25, 659.25, 783.99, 1046.50, 1318.51]):
        _sumar(pista, _est(_nota(f, 1000)), 800 + i * 110, 0.7)
    return _fade(_reverb(pista, seg=0.9, mezcla=0.5), 0, 300)


def sintetizar_transicion() -> np.ndarray:
    pista = _pista(1800)
    _sumar(pista, _barrido_ruido(600, subida=False), 0, 0.5)
    _sumar(pista, _impacto(1400), 0, 0.8)
    for i, f in enumerate([440.00, 554.37, 659.25, 880.00]):
        _sumar(pista, _est(_pluck(f, 500)), 120 + i * 90, 0.9)
    return _fade(_reverb(pista, seg=0.8, mezcla=0.45), 0, 250)


def sintetizar_cierre() -> np.ndarray:
    pista = _pista(1800)
    for i, f in enumerate([1046.50, 783.99, 659.25, 523.25]):
        _sumar(pista, _est(_nota(f, 1400 if i < 3 else 1800)), i * 150, 0.8)
    _sumar(pista, _impacto(1200), 450, 0.5)
    return _fade(_reverb(pista, seg=1.0, mezcla=0.5), 0, 900)


def cargar_efecto(nombre: str, sintetizador) -> np.ndarray:
    seg = buscar_asset(nombre)
    x = seg_a_array(seg) if seg is not None else sintetizador()
    return _pico(x, 0.9)


# ---------------------------------------------------------------------------
# Ambientes sintetizados (si no hay archivos en assets/clima/)
# ---------------------------------------------------------------------------

def _ruido_est(dur_ms: float) -> np.ndarray:
    return np.random.uniform(-1, 1, (int(SR * dur_ms / 1000), 2))


def amb_lluvia(dur_ms: float) -> np.ndarray:
    x = _filtrar(_ruido_est(dur_ms), pasa_altos=900, pasa_bajos=9000)
    t = _t(dur_ms)
    mod = 1 + 0.25 * np.sin(2 * np.pi * 0.23 * t) + 0.10 * np.sin(2 * np.pi * 0.7 * t + 1)
    return x * mod[:, None]


def _trueno(dur_ms: float = 3500) -> np.ndarray:
    t = _t(dur_ms)
    grave = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=25, pasa_bajos=160)
    grave = _pico(grave, 1.0) * (np.minimum(t / 0.25, 1.0) * np.exp(-t * 1.3))[:, None]
    chasquido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_bajos=2500) * np.exp(-t * 25)[:, None]
    return _pico(grave + chasquido * 0.6, 1.0)


def amb_tormenta(dur_ms: float) -> np.ndarray:
    lluvia = _rms(amb_lluvia(dur_ms), REFERENCIA_RMS_DB)
    for pos in (0.25, 0.65):
        _sumar(lluvia, _trueno() * 0.6, dur_ms * pos)
    return lluvia


def amb_viento(dur_ms: float) -> np.ndarray:
    x = _filtrar(_ruido_est(dur_ms), pasa_altos=80, pasa_bajos=700)
    t = _t(dur_ms)
    mod = (0.6 + 0.4 * np.sin(2 * np.pi * 0.15 * t)) * (0.8 + 0.2 * np.sin(2 * np.pi * 0.41 * t + 2))
    return x * mod[:, None]


def amb_pajaros(dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    x = np.zeros((n, 2))
    rng = np.random.default_rng()
    for _ in range(max(3, int(dur_ms / 1000 * 3))):
        m = int(rng.uniform(0.06, 0.14) * SR)
        f0 = rng.uniform(2500, 4200)
        f = np.linspace(f0, f0 * rng.uniform(0.7, 1.4), m)
        gorjeo = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.hanning(m) * rng.uniform(0.3, 1.0)
        ini = int(rng.integers(0, max(1, n - SR // 2)))
        pan = rng.uniform(0.2, 0.8)
        for k in range(int(rng.integers(1, 4))):
            p = ini + k * int(m * 1.3)
            if p + m < n:
                x[p:p + m, 0] += gorjeo * pan
                x[p:p + m, 1] += gorjeo * (1 - pan)
    aire = _filtrar(_ruido_est(dur_ms), pasa_bajos=600) * 0.03
    return x + aire


def amb_grillos(dur_ms: float) -> np.ndarray:
    t = _t(dur_ms)
    out = np.zeros((len(t), 2))
    for canal, (f, ritmo) in enumerate([(4300.0, 3.1), (4700.0, 2.7)]):
        portadora = np.sin(2 * np.pi * f * t)
        compuerta = ((t * ritmo) % 1.0 < 0.5) * (0.5 + 0.5 * np.sin(2 * np.pi * 28 * t))
        out[:, canal] = portadora * compuerta
    return out + out[:, ::-1] * 0.4


def amb_chicharras(dur_ms: float) -> np.ndarray:
    x = _filtrar(_ruido_est(dur_ms), pasa_altos=3500, pasa_bajos=8000)
    t = _t(dur_ms)
    vibracion = 0.5 + 0.5 * np.sin(2 * np.pi * 60 * t)
    oleada = 0.6 + 0.4 * np.sin(2 * np.pi * 0.12 * t) ** 2
    return x * (vibracion * oleada)[:, None]


AMBIENTES_SINT = {
    "lluvia": amb_lluvia,
    "tormenta": amb_tormenta,
    "viento": amb_viento,
    "pajaros": amb_pajaros,
    "grillos": amb_grillos,
    "chicharras": amb_chicharras,
}


def construir_ambiente(nombre: str, dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset(f"clima/{nombre}")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print(f"Sintetizando ambiente: {nombre}")
        base = AMBIENTES_SINT[nombre](dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 700, 1200)


# ---------------------------------------------------------------------------
# Voces (Google Cloud TTS) + procesado
# ---------------------------------------------------------------------------

def sintetizar_voz(client, texto: str, nombre_voz: str, audio_config) -> np.ndarray:
    entrada = texttospeech.SynthesisInput(text=texto)
    voz = texttospeech.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
    resp = client.synthesize_speech(input=entrada, voice=voz, audio_config=audio_config)
    seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
    return seg_a_array(seg)


def procesar_voz(x: np.ndarray) -> np.ndarray:
    """EQ de locutor (sin graves molestos, más presencia y calidez) + compresión."""
    x = _filtrar(x, pasa_altos=85)
    presencia = _filtrar(x, pasa_altos=2200, pasa_bajos=5500)
    calidez = _filtrar(x, pasa_altos=90, pasa_bajos=220)
    x = x + 0.22 * presencia + 0.18 * calidez
    x = _comprimir(x, thr_db=-24.0, ratio=3.0, ataque_ms=6.0, relajo_ms=140.0)
    return _pico(x, 0.85)


# ---------------------------------------------------------------------------
# Ensamblado y masterización
# ---------------------------------------------------------------------------

def ensamblar_spot(voz_clima, voz_noticias, intro, trans, cierre, ambiente, cama, cama_noticias) -> np.ndarray:
    """
    Línea de tiempo:
      intro (la voz entra sobre su cola) -> clima (+ambiente) -> transición -> noticias -> cierre
    """
    inicio_clima = max(1300.0, _dur_ms(intro) - 1700.0)
    fin_clima = inicio_clima + _dur_ms(voz_clima)

    capas = [(intro, 0.0, EFECTOS_DB), (voz_clima, inicio_clima, 0.0)]
    tramos_voz = [(inicio_clima, fin_clima)]
    if ambiente is not None:
        capas.append((ambiente, max(0.0, inicio_clima - 700.0), AMBIENTE_DB))

    fin_voces = fin_clima
    inicio_not = None
    if voz_noticias is not None:
        inicio_trans = fin_clima + 150.0
        inicio_not = inicio_trans + max(700.0, _dur_ms(trans) - 1500.0)
        fin_not = inicio_not + _dur_ms(voz_noticias)
        capas += [(trans, inicio_trans, EFECTOS_DB), (voz_noticias, inicio_not, 0.0)]
        tramos_voz.append((inicio_not, fin_not))
        fin_voces = fin_not

    inicio_cierre = fin_voces - 150.0
    capas.append((cierre, inicio_cierre, EFECTOS_DB))
    total_ms = inicio_cierre + _dur_ms(cierre)

    master = _pista(total_ms + 500)
    for x, ini, db in capas:
        _sumar(master, x, ini, _g(db))

    # Cama musical con ducking: baja sola cuando habla la voz y sube en los sweepers
    if cama is not None:
        puntos = [(0.0, CAMA_DB_LIBRE)]
        for ini, fin in tramos_voz:
            puntos += [(ini - 150, CAMA_DB_LIBRE), (ini + 350, CAMA_DB_VOZ),
                       (fin - 100, CAMA_DB_VOZ), (fin + 400, CAMA_DB_LIBRE)]
        puntos += [(total_ms - 1200, CAMA_DB_LIBRE), (total_ms, -60.0)]
        puntos.sort(key=lambda p: p[0])
        eje_ms = np.arange(len(master)) * 1000.0 / SR
        env_db = np.interp(eje_ms, [p[0] for p in puntos], [p[1] for p in puntos])
        master += _extender(cama, len(master)) * _g_array(env_db)[:, None]

    if cama_noticias is not None and inicio_not is not None:
        largo = int(SR * (_dur_ms(voz_noticias) + 1200) / 1000)
        x = _fade(_extender(cama_noticias, largo), 500, 900)
        _sumar(master, x, inicio_not - 400, _g(CAMA_NOTICIAS_DB))

    return master[: int(SR * total_ms / 1000)]


def masterizar(x: np.ndarray) -> np.ndarray:
    x = _comprimir(x, thr_db=-16.0, ratio=2.5, ataque_ms=15.0, relajo_ms=200.0)
    x = _filtrar(x, pasa_altos=40)
    return _pico(x, 0.89)


def generar_spot(texto_clima: str, texto_noticias: str, ambiente_nombre: str | None, path_salida: str) -> None:
    client = texttospeech.TextToSpeechClient()
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    ahora = datetime.now(ZONA_HORARIA)

    # Turnos: hora par -> mujer clima / hombre noticias; hora impar -> al revés
    if ahora.hour % 2 == 0:
        voz_clima_nombre, voz_noticias_nombre = "es-US-Neural2-A", "es-US-Neural2-B"
        print("Turno (hora par): Mujer (Clima) -> Hombre (Noticias)")
    else:
        voz_clima_nombre, voz_noticias_nombre = "es-US-Neural2-B", "es-US-Neural2-A"
        print("Turno (hora impar): Hombre (Clima) -> Mujer (Noticias)")

    # 1) Voces procesadas
    voz_clima = procesar_voz(sintetizar_voz(client, texto_clima, voz_clima_nombre, audio_config))
    voz_noticias = None
    if texto_noticias:
        voz_noticias = procesar_voz(sintetizar_voz(client, texto_noticias, voz_noticias_nombre, audio_config))

    # 2) Sweepers
    intro = cargar_efecto("intro", sintetizar_intro)
    trans = cargar_efecto("transicion", sintetizar_transicion)
    cierre = cargar_efecto("cierre", sintetizar_cierre)

    # 3) Ambiente del clima (dura lo que la voz del clima + colchón)
    ambiente = None
    if ambiente_nombre:
        print(f"Ambiente según el clima: {ambiente_nombre}")
        ambiente = construir_ambiente(ambiente_nombre, _dur_ms(voz_clima) + 2200)

    # 4) Camas musicales opcionales
    cama = cargar_pista_opcional("cama")
    cama_noticias = cargar_pista_opcional("noticias_cama")

    # 5) Mezcla + master + exportación
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

    print("Generando spot con voces, ambiente y efectos...")
    generar_spot(texto_clima, texto_noticias, ambiente_nombre, LOCAL_MP3)

    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
