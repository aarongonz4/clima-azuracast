#!/usr/bin/env python3
"""
Spot automático de clima y noticias para AzuraCast (Nogaradio) - VERSION V5 PRO FM.

Mejoras incluidas:
  - Voces 100% inteligibles: EQ con hueco vocal en las cortinas + Sidechain Ducking profundo (-6.5 dB).
  - Salida limpia (Fade-Out): La canción de fondo baja progresivamente al final antes de terminar el spot.
  - Vocal FX variables al final de las frases: Eco Ping-Pong, Efecto SLOW / Pitch-Down FM, Glitch Stutter y Megáfono.
  - Intros y Transiciones variables: 3 estilos de Intro FM y 3 Transiciones que rotan al azar.
  - Verificador inteligente de Noticias RSS: Evita titulares repetidos, elimina texto duplicado entre título y bajada,
    rompe el caché web y tiene fuentes de respaldo automáticas.
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

# Fuentes RSS principales + Respaldos para que nunca repita ni falle
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

# Niveles de mezcla calibrados para que la voz se entienda SIEMPRE clara al frente
REFERENCIA_RMS_DB = -19.0
EFECTOS_DB = -2.2          # Sweepers potentes pero sin saturar
AMBIENTE_DB = -12.0        # Cortina de clima clara pero debajo de la voz
TICKER_NOTICIAS_DB = -12.5 # Cortina de noticias controlada para máxima claridad de lectura
CAMA_DB_LIBRE = -5.0
CAMA_DB_VOZ = -13.5

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
# Clima y Frases Variadas de Nogaradio
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


FRASES_PASE_NOTICIAS = [
    "No te muevas, que ya llegan los titulares del momento.",
    "Quedate ahí, ahora actualizamos las noticias.",
    "Seguí con nosotros, pasamos directo al flash informativo.",
    "Subí el volumen, que ya te ponemos al día con la información.",
    "Seguimos conectados, vamos con las noticias de esta hora.",
]

PLANTILLAS_CLIMA = [
    "{saludo}. En Argentina son las {hora}. Nogaradio te cuenta cómo está el tiempo en {ciudad}: tenemos {descripcion}, con {temp} grados y una sensación térmica de {sensacion}. La humedad es del {humedad} por ciento. {pase}",
    "{saludo}, ¿cómo venís? Pasaron las {hora} en todo el país. Así está el clima ahora en {ciudad}: {descripcion}, {temp} grados de temperatura y {sensacion} de térmica. Humedad del {humedad} por ciento y viento de {viento} kilómetros por hora. {pase}",
    "Escuchás Nogaradio. Son las {hora} en Argentina. Actualizamos el estado del tiempo para {ciudad}: cielo con {descripcion}, temperatura actual de {temp} grados y térmica de {sensacion}. La humedad, del {humedad} por ciento. {pase}",
    "{saludo}. Cuando pasan las {hora}, repasamos el tiempo en {ciudad} con Nogaradio: tenemos {descripcion}, {temp} grados de temperatura, térmica de {sensacion} y vientos de {viento} kilómetros por hora. {pase}",
]


def armar_texto_clima(data: dict | None) -> str:
    ahora = datetime.now(ZONA_HORARIA)
    hora_str = hora_en_espanol(ahora.hour, ahora.minute)
    saludo = saludo_segun_hora(ahora.hour)
    pase = random.choice(FRASES_PASE_NOTICIAS)

    if not data or "current" not in data:
        return f"{saludo}. En Argentina son las {hora_str}. Nogaradio suena las veinticuatro horas para vos. {pase}"

    actual = data["current"]
    temp = round(actual["temperature_2m"])
    sensacion = round(actual["apparent_temperature"])
    humedad = round(actual["relative_humidity_2m"])
    viento = round(actual["wind_speed_10m"])
    descripcion = CODIGOS_CLIMA.get(actual["weather_code"], "condiciones variables")

    plantilla = random.choice(PLANTILLAS_CLIMA)
    return plantilla.format(
        saludo=saludo, hora=hora_str, ciudad=CIUDAD, descripcion=descripcion,
        temp=temp, sensacion=sensacion, humedad=humedad, viento=viento, pase=pase
    )


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
# Verificador Inteligente de Noticias RSS (Sin repeticiones ni duplicados)
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
    """
    Verifica que la descripción no repita el título y recorta a la primera oración clara
    para que la noticia se entienda perfecto y no suene repetitiva.
    """
    titulo = titulo.strip().rstrip(".")
    if not descripcion:
        return f"{titulo}."

    # Cortamos la descripción en la primera oración (máximo 180 caracteres)
    oraciones = re.split(r'(?<=[.!?])\s+', descripcion)
    primera_oracion = oraciones[0].strip().rstrip(".")

    norm_tit = normalizar_comparacion(titulo)
    norm_desc = normalizar_comparacion(primera_oracion)

    # Si la descripción empieza igual que el título, está contenida o es muy parecida, usamos solo el título
    if not norm_desc or norm_tit in norm_desc or norm_desc in norm_tit:
        return f"{titulo}."
    if len(norm_tit) > 15 and norm_desc[:18] == norm_tit[:18]:
        return f"{titulo}."
    if len(primera_oracion) > 190:
        return f"{titulo}."

    return f"{titulo}. {primera_oracion}."


def obtener_titular_verificado(lista_feeds: list[str], cantidad: int = 20) -> str | None:
    """
    Recorre las fuentes RSS con anti-caché, filtra titulares duplicados o muy cortos
    y elige uno combinando la hora + azar para que no repita siempre el mismo.
    """
    entradas_unicas = []
    vistos = set()

    for url_base in lista_feeds:
        try:
            # Parámetro anti-caché para obligar al servidor a dar el RSS fresco
            sep = "&" if "?" in url_base else "?"
            url_fresca = f"{url_base}{sep}_t={int(time.time() // 300)}"
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

    # Mezcla rotación por hora con selección aleatoria entre las noticias más recientes
    ahora = datetime.now(ZONA_HORARIA)
     candidatos_top = entradas_unicas[: min(10, len(entradas_unicas))]
    indice = (ahora.hour + random.randint(0, len(candidatos_top) - 1)) % len(candidatos_top)
    elegida = candidatos_top[indice]
    print(f"Noticia seleccionada ({len(entradas_unicas)} disponibles): {elegida[:80]}...")
    return elegida


FRASES_CIERRE_NOGARADIO = [
    "Ya estás al día. Subí el volumen que en Nogaradio, seguimos con vos.",
    "Hasta acá el flash informativo. No te muevas, en Nogaradio seguí conectado.",
    "La información al instante y los mejores hits. Escuchás Nogaradio, veinticuatro siete sonando para vos.",
    "Ya estás informado. Quedate del otro lado, porque en Nogaradio seguimos con vos.",
    "Toda la actualidad y la música que te mueve. En Nogaradio, siempre conectados con vos.",
    "Así actualizamos las noticias en tu radio. Seguí en Nogaradio, sonando para vos.",
]


def armar_texto_noticias(titular_mundo: str | None, titular_musica: str | None) -> str:
    cierre = random.choice(FRASES_CIERRE_NOGARADIO)
    intro_mundo = random.choice(["En las noticias internacionales:", "Actualidad internacional:", "En el plano internacional:"])
    intro_musica = random.choice(["Y en la música:", "Pasando a la música:", "En novedades musicales:"])

    if titular_mundo and titular_musica:
        return f"{intro_mundo} {titular_mundo} {intro_musica} {titular_musica} {cierre}"
    elif titular_mundo:
        return f"{intro_mundo} {titular_mundo} {cierre}"
    elif titular_musica:
        return f"{intro_musica} {titular_musica} {cierre}"
    return cierre


# ---------------------------------------------------------------------------
# Motor DSP + Efectos de Voz Finales (Eco, Slow/Pitch-Down FM, Glitch, Megáfono)
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
    """
    Atenúa las frecuencias medias (900 Hz a 3400 Hz) de la música de fondo
    para dejar un 'bolsillo' acústico donde la voz del locutor se entienda 100% nítida.
    """
    medios_voz = _filtrar(x, pasa_altos=900, pasa_bajos=3400, orden=2)
    return x - medios_voz * 0.48


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


def _recortar_silencio(x: np.ndarray, umbral: float = 0.015) -> np.ndarray:
    env = np.abs(x).max(axis=1)
    activos = np.where(env > umbral)[0]
    if len(activos) == 0:
        return x
    ini = max(0, activos[0] - int(SR * 0.025))
    fin = min(len(x), activos[-1] + int(SR * 0.045))
    return _fade(x[ini:fin], 10, 20)


def _estirar_pitch_slow(trozo: np.ndarray, factor: float = 1.32) -> np.ndarray:
    """Efecto Slow / Pitch-Down de FM: desacelera y engrosa la voz final."""
    n_orig = len(trozo)
    n_nuevo = int(n_orig * factor)
    idx_orig = np.arange(n_orig)
    idx_nuevo = np.linspace(0, n_orig - 1, n_nuevo)
    l = np.interp(idx_nuevo, idx_orig, trozo[:, 0])
    r = np.interp(idx_nuevo, idx_orig, trozo[:, 1])
    out = np.column_stack((l, r))
    return _filtrar(out, pasa_altos=75, pasa_bajos=3600)


def aplicar_fx_final_voz(x: np.ndarray, es_cierre_final: bool = True) -> tuple[np.ndarray, str]:
    """
    Aplica efectos de radio FM a la última frase del locutor:
      - 'slow_fm': Desacelera (slow/pitch-down) la repetición de las últimas palabras + eco.
      - 'eco_pingpong': Rebote estéreo izquierda-derecha clásico de FM.
      - 'glitch_stutter': Tartamudeo digital rítmico + cola de eco.
      - 'megafono_delay': Filtro de radio + delay espacial.
    """
    if es_cierre_final:
        opciones = ["slow_fm", "eco_pingpong", "glitch_stutter", "megafono_delay"]
    else:
        opciones = ["eco_pingpong", "megafono_delay", "limpio"]

    modo_fx = random.choice(opciones)

    if modo_fx == "limpio":
        cola = np.zeros((int(SR * 0.12), 2))
        return np.vstack((x, cola)), "Limpio"

    ventana = min(int(SR * 0.45), len(x) // 2)
    trozo_final = _fade(x[-ventana:].copy(), 25, 25)
    trozo_filtrado = _filtrar(trozo_final, pasa_altos=380, pasa_bajos=3200)

    if modo_fx == "slow_fm":
        # El locutor dice la frase normal e inmediatamente sale una réplica en "Slow Motion" grave en estéreo + eco
        trozo_slow = _fade(_estirar_pitch_slow(trozo_final, factor=1.30), 30, 60) * 0.78
        # Abrimos en estéreo el efecto slow
        d_s = int(SR * 0.015)
        trozo_slow[d_s:, 1] = trozo_slow[:-d_s, 1]
        out = np.pad(x, ((0, len(trozo_slow) + int(SR * 0.45)), (0, 0)))
        ini_slow = len(x) - int(SR * 0.06)
        _sumar(out, trozo_slow, ini_slow * 1000.0 / SR, 0.90)
        # Segundo eco más suave y grave
        _sumar(out, _filtrar(trozo_slow, pasa_bajos=2200), (ini_slow + int(SR * 0.32)) * 1000.0 / SR, 0.42)
        return out, "Slow Pitch-Down FM + Eco"

    elif modo_fx == "eco_pingpong":
        extra_ms = 1150
        out = np.pad(x, ((0, int(SR * extra_ms / 1000)), (0, 0)))
        paso_eco = int(SR * 0.24)
        inicio_eco = len(x) - int(SR * 0.10)
        for rep in range(1, 5):
            pos = inicio_eco + (rep - 1) * paso_eco
            gan = 0.54 ** rep
            fin = min(len(out), pos + len(trozo_filtrado))
            largo = fin - pos
            if largo > 0:
                c = rep % 2
                out[pos:fin, c] += trozo_filtrado[:largo, c] * gan * 0.95
                out[pos:fin, 1 - c] += trozo_filtrado[:largo, 1 - c] * gan * 0.28
        return out, "Eco Ping-Pong Estéreo"

    elif modo_fx == "glitch_stutter":
        stutter_len = min(int(SR * 0.105), len(x) // 4)
        silaba = _fade(x[-ventana : -ventana + stutter_len].copy(), 8, 8)
        silaba_eq = _filtrar(silaba, pasa_altos=450, pasa_bajos=4200) * 0.85
        s_l = np.column_stack((silaba_eq[:, 0], silaba_eq[:, 1] * 0.2))
        s_r = np.column_stack((silaba_eq[:, 0] * 0.2, silaba_eq[:, 1]))

        ensamble = np.vstack((x[:-ventana], s_l, s_r, x[-ventana:]))
        out = np.pad(ensamble, ((0, int(SR * 0.90)), (0, 0)))
        paso_eco = int(SR * 0.24)
        ini_eco = len(ensamble) - int(SR * 0.10)
        for rep in range(1, 4):
            pos = ini_eco + (rep - 1) * paso_eco
            gan = 0.50 ** rep
            fin = min(len(out), pos + len(trozo_filtrado))
            if fin > pos:
                c = rep % 2
                out[pos:fin, c] += trozo_filtrado[: fin - pos, c] * gan
        return out, "Glitch Stutter + Eco FM"

    else:
        v_meg = min(int(SR * 0.52), len(x) // 2)
        out = x.copy()
        tramo_radio = np.tanh(_filtrar(out[-v_meg:], pasa_altos=550, pasa_bajos=2600) * 2.2) * 0.65
        out[-v_meg:] = out[-v_meg:] * 0.35 + tramo_radio * 0.75
        out = np.pad(out, ((0, int(SR * 0.80)), (0, 0)))
        for rep, pos_ms in enumerate([0, 230, 460], start=1):
            pos = len(x) - int(SR * 0.08) + int(SR * pos_ms / 1000)
            fin = min(len(out), pos + len(trozo_filtrado))
            if fin > pos:
                out[pos:fin] += trozo_filtrado[: fin - pos] * (0.48 ** rep)
        return out, "Filtro Megáfono FM + Delay"


def _ducking_por_voz(fondo: np.ndarray, pista_voz: np.ndarray, reduccion_db: float = -6.5) -> np.ndarray:
    """Baja la cortina -6.5 dB cuando el locutor habla y la sube en las pausas."""
    n = min(len(fondo), len(pista_voz))
    blk = int(SR * 0.015)
    nb = n // blk + 1
    env = np.pad(np.abs(pista_voz[:n]).max(axis=1), (0, nb * blk - n)).reshape(nb, blk).max(axis=1)
    hablando = (env > 0.035).astype(np.float64)

    a = np.exp(-15.0 / 20.0)
    r = np.exp(-15.0 / 260.0)
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
    return np.tanh(out * 0.45) * 0.36


def _pop_pluck(freq: float, dur_ms: float = 240, pan: float = 0.5) -> np.ndarray:
    t = _t(dur_ms)
    mod = 2.2 * np.exp(-t * 28.0) * np.sin(2 * np.pi * (freq * 2.0) * t)
    onda = np.sin(2 * np.pi * freq * t + mod) + 0.35 * np.sin(2 * np.pi * (freq * 0.5) * t)
    env = np.exp(-t * 11.0)
    ataque = min(int(SR * 0.003), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    mono = onda * env * 0.32
    return np.column_stack((mono * pan, mono * (1.0 - pan)))


def _funky_bass(freq: float, dur_ms: float) -> np.ndarray:
    t = _t(dur_ms)
    mod = 1.6 * np.exp(-t * 22.0) * np.sin(2 * np.pi * (freq * 2.0) * t)
    sub = np.sin(2 * np.pi * freq * t + mod)
    arm = 0.35 * np.sin(2 * np.pi * (freq * 2.0) * t) * np.exp(-t * 10.0)
    env = np.exp(-4.0 * t)
    ataque = min(int(SR * 0.006), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    return _est(np.tanh((sub + arm) * env * 1.5) * 0.40)


def _kick_punch(dur_ms: float = 240) -> np.ndarray:
    t = _t(dur_ms)
    f_env = 48.0 + 140.0 * np.exp(-t * 42.0)
    fase = 2 * np.pi * np.cumsum(f_env) / SR
    cuerpo = np.tanh(np.sin(fase) * 1.8) * np.exp(-t * 11.0)
    click = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=1000, pasa_bajos=4500)
    click *= np.exp(-t * 110.0)[:, None] * 0.22
    return _est(cuerpo * 0.85) + click


def _snare_pop(dur_ms: float = 190) -> np.ndarray:
    t = _t(dur_ms)
    tono = np.sin(2 * np.pi * 185.0 * t) * np.exp(-t * 28.0) * 0.4
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=700, pasa_bajos=7500)
    ruido *= np.exp(-t * 20.0)[:, None] * 0.32
    return _est(tono) + ruido


def _hihat_crisp(dur_ms: float = 65, abierto: bool = False) -> np.ndarray:
    dur = 180 if abierto else dur_ms
    t = _t(dur)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=6500, pasa_bajos=13500)
    env = np.exp(-t * (14.0 if abierto else 50.0))
    return ruido * env[:, None] * (0.20 if abierto else 0.14)


def _whoosh_aerodinamico(dur_ms: float = 700, f_ini: float = 250.0, f_fin: float = 4800.0) -> np.ndarray:
    t = _t(dur_ms)
    prog = t / (dur_ms / 1000.0)
    ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=300, pasa_bajos=7500)
    f_curva = f_ini * ((f_fin / f_ini) ** (prog ** 1.6))
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    tono = np.sin(fase) * 0.24
    env = np.sin(np.pi * (prog ** 1.3)) ** 2
    pan_l = 0.15 + 0.85 * prog
    pan_r = 1.0 - 0.85 * prog
    out = (ruido * 0.60 + np.column_stack((tono * pan_l, tono * pan_r))) * env[:, None]
    return _pico(out, 0.85)


def _sub_drop_cine(dur_ms: float = 1200) -> np.ndarray:
    t = _t(dur_ms)
    f_curva = 32.0 + 80.0 * np.exp(-t * 7.5)
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    onda = np.tanh(np.sin(fase) * 1.7) * np.exp(-t * 2.5)
    return _est(onda * 0.80)


def _laser_drop(dur_ms: float = 420) -> np.ndarray:
    t = _t(dur_ms)
    f_curva = 65.0 + 3000.0 * np.exp(-t * 15.0)
    fase = 2 * np.pi * np.cumsum(f_curva) / SR
    onda = np.tanh(np.sin(fase) * 1.5) * np.exp(-t * 6.0)
    return _est(onda * 0.50)


# ---------------------------------------------------------------------------
# Sweepers Variables (3 Intros FM y 3 Transiciones que rotan al azar)
# ---------------------------------------------------------------------------

def sintetizar_intro() -> np.ndarray:
    """Elige al azar entre 3 estilos de Intro FM Pro para que siempre varíe la entrada."""
    estilo = random.choice(["top40_pips", "urban_drop", "cyber_riser"])
    print(f"Estilo de Intro FM elegido: [{estilo}]")
    pista = _pista(2800)

    if estilo == "top40_pips":
        # 1) Pips horarios modernos en Do6 + Whoosh + Acorde Am9 + Fanfarria ascendente
        _sumar(pista, _whoosh_aerodinamico(780, 250, 5000), 20, 0.85)
        for i, pos in enumerate([120, 340, 560, 780]):
            es_ultimo = (i == 3)
            dur = 300 if es_ultimo else 70
            freq = 1760.0 if es_ultimo else 880.0
            t = _t(dur)
            pip = (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t)) * np.exp(-t * (6.0 if es_ultimo else 22.0))
            _sumar(pista, _est(pip * 0.40), pos)

        _sumar(pista, _sub_drop_cine(1300), 780, 0.90)
        _sumar(pista, _kick_punch(260), 800, 1.0)
        _sumar(pista, _synth_brass([220.0, 261.63, 329.63, 392.00, 493.88], 1600), 800, 0.90)
        notas = [880.0, 1046.50, 1318.51, 1567.98, 1760.0]

    elif estilo == "urban_drop":
        # 2) Doble golpe síncopado Urbano + Sub-Drop + Acorde Fmaj9 + Plucks rítmicos
        _sumar(pista, _whoosh_aerodinamico(600, 4000, 300), 0, 0.80)
        _sumar(pista, _kick_punch(240), 180, 0.90)
        _sumar(pista, _snare_pop(180), 400, 0.85)
        _sumar(pista, _whoosh_aerodinamico(400, 300, 4500), 420, 0.85)
        _sumar(pista, _sub_drop_cine(1400), 780, 1.0)
        _sumar(pista, _kick_punch(280), 800, 1.0)
        _sumar(pista, _synth_brass([174.61, 220.0, 261.63, 329.63, 392.00], 1650), 800, 0.90)
        notas = [1046.50, 987.77, 783.99, 1046.50, 1318.51]

    else:
        # 3) Cyber Riser + Laser Drop + Acorde Em9 + Arpegio estéreo moderno
        _sumar(pista, _whoosh_aerodinamico(800, 180, 5800), 0, 0.90)
        _sumar(pista, _laser_drop(420), 760, 0.85)
        _sumar(pista, _sub_drop_cine(1300), 780, 0.95)
        _sumar(pista, _kick_punch(260), 800, 1.0)
        _sumar(pista, _synth_brass([164.81, 196.0, 246.94, 293.66, 369.99], 1600), 800, 0.90)
        notas = [659.25, 783.99, 987.77, 1174.66, 1318.51]

    logo = _pista(1800)
    for idx, f in enumerate(notas):
        pan = 0.2 + 0.6 * (idx / 4.0)
        _sumar(logo, _pop_pluck(f, 450 if idx < 4 else 900, pan=pan), idx * 100, 1.0)
    _sumar(pista, _stereo_delay(logo, 190, 285, 0.38, 0.35), 840, 0.95)

    return _fade(_reverb_estereo(pista, seg=1.25, mezcla=0.32), 10, 350)


def sintetizar_transicion() -> np.ndarray:
    """Elige al azar entre 3 separadores distintos para pasar del Clima a las Noticias."""
    estilo = random.choice(["flash_stinger", "swoosh_impact", "electro_pulse"])
    print(f"Estilo de Transición FM elegido: [{estilo}]")
    pista = _pista(1700)

    if estilo == "flash_stinger":
        _sumar(pista, _whoosh_aerodinamico(400, 3800, 300), 0, 0.85)
        _sumar(pista, _sub_drop_cine(1000), 320, 0.90)
        _sumar(pista, _kick_punch(250), 340, 1.0)
        notas = [587.33, 880.0, 1174.66, 1318.51]
    elif estilo == "swoosh_impact":
        _sumar(pista, _whoosh_aerodinamico(500, 250, 4800), 0, 0.90)
        _sumar(pista, _laser_drop(350), 320, 0.75)
        _sumar(pista, _kick_punch(250), 340, 1.0)
        _sumar(pista, _snare_pop(190), 520, 0.85)
        notas = [659.25, 987.77, 1318.51, 1567.98]
    else:
        _sumar(pista, _whoosh_aerodinamico(380, 2500, 500), 0, 0.80)
        _sumar(pista, _sub_drop_cine(1000), 300, 0.95)
        _sumar(pista, _kick_punch(240), 320, 1.0)
        _sumar(pista, _kick_punch(240), 500, 0.90)
        notas = [523.25, 783.99, 1046.50, 1174.66]

    stinger = _pista(1200)
    for i, f in enumerate(notas):
        _sumar(stinger, _pop_pluck(f, 400, pan=0.25 if i % 2 == 0 else 0.75), i * 85, 1.0)
    _sumar(stinger, _synth_brass([notas[0] * 0.5, notas[1] * 0.5, notas[2] * 0.5], 950), 220, 0.80)
    _sumar(pista, _stereo_delay(stinger, 160, 240, 0.35, 0.32), 340, 0.95)

    return _fade(_reverb_estereo(pista, seg=0.95, mezcla=0.28), 10, 250)


def sintetizar_cierre() -> np.ndarray:
    """Cierre final limpio con arpegio descendente + Impacto grave que se apaga suavemente."""
    pista = _pista(2200)
    notas_fin = [1760.0, 1567.98, 1318.51, 1046.50, 880.0]
    arp = _pista(1800)
    for i, f in enumerate(notas_fin):
        _sumar(arp, _pop_pluck(f, 500, pan=0.8 - i * 0.15), i * 95, 0.95)
    _sumar(pista, _stereo_delay(arp, 200, 300, 0.35, 0.30), 0, 0.90)

    _sumar(pista, _whoosh_aerodinamico(450, 350, 3800), 0, 0.70)
    _sumar(pista, _sub_drop_cine(1400), 380, 0.95)
    _sumar(pista, _kick_punch(320), 400, 1.0)
    _sumar(pista, _funky_bass(55.0, 1500), 400, 0.95)
    _sumar(pista, _synth_brass([220.0, 261.63, 329.63, 440.0, 523.25], 1550), 400, 0.90)

    return _fade(_reverb_estereo(pista, seg=1.4, mezcla=0.35), 10, 650)


# ---------------------------------------------------------------------------
# Cortinas Musicales (Con hueco de EQ vocal para no tapar nunca a los locutores)
# ---------------------------------------------------------------------------

def bed_clima_groove(modo: str, dur_ms: float) -> np.ndarray:
    pista = _pista(dur_ms)
    t = _t(dur_ms)
    beat_ms = 500.0   # 120 BPM
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
        _sumar(musica, _synth_brass(pad_f, compas_ms, corte_lp=1500), ini_c, 0.48)

        for b in range(8):
            f_b = bajo_f * 2.0 if b in (3, 7) else bajo_f
            _sumar(musica, _funky_bass(f_b, 230), ini_c + b * 250.0, 0.70)

        for s in range(16):
            nota_f = arp_f[s % len(arp_f)]
            pan = 0.20 if s % 2 == 0 else 0.80
            _sumar(musica, _pop_pluck(nota_f, 170, pan=pan), ini_c + s * semi_ms, 0.45)

    fase_beat = (t * 2.0) % 1.0
    sidechain = 0.45 + 0.55 * (1.0 - np.exp(-fase_beat * 5.0))
    musica *= sidechain[:, None]
    pista += _stereo_delay(musica, 250, 375, 0.28, 0.22)

    kick = _kick_punch(230)
    snare = _snare_pop(180)
    hh_c = _hihat_crisp(55, abierto=False)
    hh_o = _hihat_crisp(160, abierto=True)

    for b in range(int(dur_ms / beat_ms) + 1):
        pos_b = b * beat_ms
        _sumar(pista, kick, pos_b, 0.80)
        if b % 2 == 1:
            _sumar(pista, snare, pos_b, 0.65)
        _sumar(pista, hh_c, pos_b, 0.55)
        _sumar(pista, hh_o, pos_b + 250.0, 0.50)

    if modo in ("lluvia", "tormenta"):
        agua = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=1200, pasa_bajos=7200)
        oleaje = 0.75 + 0.25 * np.sin(2 * np.pi * 0.3 * t)
        pista += agua * oleaje[:, None] * 0.16
        if modo == "tormenta":
            for pos_tr in (dur_ms * 0.20, dur_ms * 0.65):
                t_tr = _t(2400)
                trueno = _filtrar(np.random.uniform(-1, 1, (len(t_tr), 2)), pasa_altos=30, pasa_bajos=150)
                env_tr = np.minimum(t_tr / 0.15, 1.0) * np.exp(-t_tr * 1.3)
                _sumar(pista, _pico(trueno * env_tr[:, None], 0.70), pos_tr, 0.60)

    pista = _hueco_vocal_cortina(pista)
    return _filtrar(pista, pasa_altos=40, pasa_bajos=10000)


def bed_noticias_groove(dur_ms: float) -> np.ndarray:
    pista = _pista(dur_ms)
    beat_ms = 468.75   # 128 BPM
    semi_ms = beat_ms / 4.0

    secuencia = [220.0, 220.0, 440.0, 220.0, 329.63, 220.0, 392.0, 329.63]
    for i, pos in enumerate(np.arange(0, dur_ms - semi_ms, semi_ms)):
        f = secuencia[i % len(secuencia)]
        pan = 0.25 if i % 2 == 0 else 0.75
        _sumar(pista, _pop_pluck(f, 110, pan=pan), pos, 0.48)
        if i % 4 == 0:
            _sumar(pista, _funky_bass(f * 0.5, 200), pos, 0.70)
        if i % 4 == 2:
            t_b = _t(30)
            bip = np.sin(2 * np.pi * 1567.98 * t_b) * np.hanning(len(t_b))
            _sumar(pista, _est(bip * 0.12), pos)

    kick = _kick_punch(210)
    snare = _snare_pop(160)
    hh = _hihat_crisp(50, abierto=False)

    for b, pos in enumerate(np.arange(0, dur_ms - beat_ms, beat_ms)):
        _sumar(pista, kick, pos, 0.80)
        if b % 2 == 1:
            _sumar(pista, snare, pos, 0.60)
        for s in range(4):
            _sumar(pista, hh, pos + s * semi_ms, 0.60 if s == 2 else 0.35)

    pista = _hueco_vocal_cortina(pista)
    return _stereo_delay(_filtrar(pista, pasa_altos=45, pasa_bajos=10000), 234, 351, 0.22, 0.18)


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
        print(f"Generando cortina V5 Pop/Dance ({nombre})...")
        base = bed_clima_groove(nombre, dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 300, 1200)


def construir_cama_noticias(dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset("noticias_cama")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print("Generando cortina V5 Breaking News (con salida fade-out limpia)...")
        base = bed_noticias_groove(dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    # Fade-out largo de 2.4 segundos al final para que la música baje de a poco y cierre limpio
    return _fade(x, 250, 2400)


def cargar_pista_opcional(base: str) -> np.ndarray | None:
    seg = buscar_asset(base)
    if seg is None:
        return None
    return _rms(seg_a_array(seg), REFERENCIA_RMS_DB)


# ---------------------------------------------------------------------------
# Voces (Google Cloud TTS con SSML) + Procesador Vocal de Alta Claridad
# ---------------------------------------------------------------------------

def texto_a_ssml(texto: str) -> str:
    seguro = texto.replace("&", " y ").replace("<", "").replace(">", "")
    seguro = re.sub(r'\.\s+', '. <break time="280ms"/> ', seguro)
    seguro = re.sub(r':\s+', ': <break time="200ms"/> ', seguro)
    return f"<speak><prosody rate='1.03'>{seguro}</prosody></speak>"


def sintetizar_voz(client, texto: str, nombre_voz: str, audio_config) -> np.ndarray:
    entrada = texttospeech.SynthesisInput(ssml=texto_a_ssml(texto))
    voz = texttospeech.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
    resp = client.synthesize_speech(input=entrada, voice=voz, audio_config=audio_config)
    seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
    return _recortar_silencio(seg_a_array(seg))


def procesar_voz(x: np.ndarray) -> np.ndarray:
    """
    EQ de locutor principal de FM: refuerza articulación (2.8kHz - 5.5kHz) y cuerpo (130Hz)
    con compresión niveladora para que ninguna palabra quede baja frente a la cortina.
    """
    x = _filtrar(x, pasa_altos=85)
    cuerpo = _filtrar(x, pasa_altos=110, pasa_bajos=240)
    claridad = _filtrar(x, pasa_altos=2200, pasa_bajos=5600)
    aire = np.tanh(_filtrar(x, pasa_altos=6500) * 2.0) * 0.12

    x = x + 0.22 * cuerpo + 0.34 * claridad + aire
    x = _comprimir(x, thr_db=-24.0, ratio=4.0, ataque_ms=3.5, relajo_ms=110.0)
    return _pico(x, 0.94)


# ---------------------------------------------------------------------------
# Ensamblado con Fade-Out Progresivo y Salida Limpia
# ---------------------------------------------------------------------------

def ensamblar_spot(voz_clima_cruda, voz_noticias_cruda, intro, trans, cierre, ambiente, cama, cama_noticias) -> np.ndarray:
    voz_clima, fx_clima = aplicar_fx_final_voz(voz_clima_cruda, es_cierre_final=(voz_noticias_cruda is None))
    print(f"Efecto vocal final Clima: [{fx_clima}]")

    inicio_clima = 1600.0
    fin_clima_seco = inicio_clima + _dur_ms(voz_clima_cruda)

    fin_voces_seco = fin_clima_seco
    fin_cola_vocal = inicio_clima + _dur_ms(voz_clima)
    inicio_trans = None
    inicio_not = None
    voz_noticias = None

    if voz_noticias_cruda is not None:
        voz_noticias, fx_not = aplicar_fx_final_voz(voz_noticias_cruda, es_cierre_final=True)
        print(f"Efecto vocal final Noticias: [{fx_not}]")
        inicio_trans = fin_clima_seco - 80.0
        inicio_not = inicio_trans + 920.0
        fin_voces_seco = inicio_not + _dur_ms(voz_noticias_cruda)
        fin_cola_vocal = inicio_not + _dur_ms(voz_noticias)

    inicio_cierre = fin_voces_seco - 120.0
    total_ms = max(inicio_cierre + _dur_ms(cierre), fin_cola_vocal + 450.0)

    # 1. Bus de Voces (al frente, sin atenuación)
    bus_voces = _pista(total_ms + 500)
    _sumar(bus_voces, voz_clima, inicio_clima, 1.0)
    if voz_noticias is not None and inicio_not is not None:
        _sumar(bus_voces, voz_noticias, inicio_not, 1.0)

    # 2. Bus de Cortinas (con Fade-Out progresivo en los últimos 2.5s para salida limpia)
    bus_cortinas = _pista(total_ms + 500)
    if ambiente is not None:
        _sumar(bus_cortinas, ambiente, 800.0, _g(AMBIENTE_DB))
    if cama_noticias is not None and inicio_trans is not None:
        _sumar(bus_cortinas, cama_noticias, inicio_trans + 340.0, _g(TICKER_NOTICIAS_DB))
    if cama is not None:
        _sumar(bus_cortinas, _extender(cama, len(bus_cortinas)), 0.0, _g(CAMA_DB_LIBRE - 4.0))

    bus_cortinas = _ducking_por_voz(bus_cortinas, bus_voces, reduccion_db=-6.5)

    # Curva maestra de salida limpia: va bajando la música de fondo suavemente cuando entra el cierre
    n_total = int(SR * total_ms / 1000)
    inicio_fade_out = max(0, int(SR * (fin_voces_seco - 600.0) / 1000))
    if n_total > inicio_fade_out:
        largo_fade = n_total - inicio_fade_out
        curva_salida = np.linspace(1.0, 0.0, largo_fade) ** 1.8
        bus_cortinas[inicio_fade_out:n_total] *= curva_salida[:, None]
        bus_cortinas[n_total:] = 0.0

    # 3. Bus de Sweepers (Intro, Transición y Cierre)
    bus_fx = _pista(total_ms + 500)
    _sumar(bus_fx, intro, 0.0, _g(EFECTOS_DB))
    if inicio_trans is not None:
        _sumar(bus_fx, trans, inicio_trans, _g(EFECTOS_DB))
    _sumar(bus_fx, cierre, inicio_cierre, _g(EFECTOS_DB - 1.0))

    master = (bus_voces + bus_cortinas + bus_fx)[:n_total]
    return _fade(master, 15, 550)


def masterizar(x: np.ndarray) -> np.ndarray:
    x = _filtrar(x, pasa_altos=35)

    graves = _comprimir(_filtrar(x, pasa_bajos=200), thr_db=-18.0, ratio=3.2, ataque_ms=10.0, relajo_ms=160.0)
    medios = _comprimir(_filtrar(x, pasa_altos=200, pasa_bajos=4200), thr_db=-17.0, ratio=2.6, ataque_ms=8.0, relajo_ms=140.0)
    agudos = _comprimir(_filtrar(x, pasa_altos=4200), thr_db=-19.0, ratio=2.4, ataque_ms=4.0, relajo_ms=90.0)

    x = graves * 1.06 + medios * 1.04 + agudos * 1.12

    mid = 0.5 * (x[:, 0] + x[:, 1])
    side = 0.5 * (x[:, 0] - x[:, 1])
    side_amplio = _filtrar(_est(side), pasa_altos=240)[:, 0] * 1.25
    x = np.column_stack((mid + side_amplio, mid - side_amplio))

    x = _comprimir(x, thr_db=-13.5, ratio=3.8, ataque_ms=5.0, relajo_ms=120.0)
    x = np.tanh(x * 1.14)
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

    ambiente = construir_ambiente(ambiente_nombre, _dur_ms(voz_clima) + 600)
    cama = cargar_pista_opcional("cama")
    cama_noticias = construir_cama_noticias(_dur_ms(voz_noticias) + 1400) if voz_noticias is not None else None

    mezcla = ensamblar_spot(voz_clima, voz_noticias, intro, trans, cierre, ambiente, cama, cama_noticias)
    final = masterizar(mezcla)
    array_a_seg(final).export(path_salida, format="mp3", bitrate="192k")
    print(f"¡Spot V5 Pro ({len(final) / SR:.1f} s) guardado exitosamente en {path_salida}!")


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
    print("=== EJECUTANDO NOGARADIO VERSION V5 PRO FM ===")
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

    print("Consultando y verificando noticias...")
    titular_mundo = obtener_titular_verificado(FEEDS_MUNDO, 20)
    titular_musica = obtener_titular_verificado(FEEDS_MUSICA, 20)
    texto_noticias = armar_texto_noticias(titular_mundo, titular_musica)

    print("Generando spot con procesador V5 Pro FM...")
    generar_spot(texto_clima, texto_noticias, ambiente_nombre, LOCAL_MP3)

    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
