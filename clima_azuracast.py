#!/usr/bin/env python3
"""
Spot automático de clima, noticias e imaging para AzuraCast (Nogaradio)
VERSION V11 PROFESSIONAL RADIO IMAGING.

Qué conserva de V10 Clean FM Pro:
  - Cero micro-cortes en entradas vocales y rampa coseno suave (_extraer_cola_suave_sin_cortes).
  - Lectura de datos de clima y titulares de noticias 100% SECA y clara al frente.
  - Los 5 efectos finales limpios de V10 (sin el antiguo "reverb slowed" sucio).
  - Las 6 intros instrumentales y 4 transiciones de V10 + Fade-out lento de 3 segundos.
  - Obtención de clima Open-Meteo, verificador RSS anti-repetición y subida SFTP a AzuraCast.

Qué amplía e incorpora en V11:
  - Intros Vocales + Instrumentales de 3, 6 y 10 segundos con Firma Sonora (Sonic Logo Nogaradio).
  - Voces Masculinas Graves y Femeninas Juveniles alternándose, interrumpiéndose y en Coro Doble.
  - Efectos en palabras/segmentos específicos: Glitch Vocal, Pitch Shift Armónico, Reverse Paralelo,
    Radio Filter, Echo y Stereo Delay.
  - Banco ampliado con +150 frases (26 IDs, 18 saludos, 16 climas, 20 noticias, 16 pases,
    26 remates, 20 frases musicales y 12 promos de la app Play Store / Apple Store).
  - 8 nuevos estilos de Radio Imaging: Electronic, Cinematic, Dance Club, Pop, Urban,
    Epic Power, Retro Synthwave y Emotional Imaging.
  - 4 Estructuras distintas de spot y motor anti-repetición consecutiva.
"""

import glob
import hashlib
import io
import json
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

CIUDAD = "Resistencia"
LAT = -27.4514
LON = -58.9867
ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")

LOCAL_MP3 = "clima.mp3"
REMOTE_FILENAME = "clima.mp3"
 HISTORIAL_JSON = ".nogaradio_v11_history.json"

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

REFERENCIA_RMS_DB = -18.5
EFECTOS_DB = -0.5          # Sweepers, Intros y Transiciones bien presentes
AMBIENTE_DB = -8.5         # Cortina principal debajo de la voz
TICKER_NOTICIAS_DB = -8.0  # Cortina informativa audible debajo de los titulares
CAMA_DB_LIBRE = -4.5
CAMA_DB_VOZ = -10.5

# ---------------------------------------------------------------------------
# Motor Anti-Repetición y Rotación Inteligente V11
# ---------------------------------------------------------------------------

class SelectorRotativoV11:
    """
    Garantiza que no se repitan frases, estilos de imaging, intros ni estructuras
    dentro del mismo spot ni en corridas consecutivas.
    """
    def __init__(self, path_historial: str):
        self.path = path_historial
        self.usados_en_sesion: dict[str, set[str]] = {}
        self.ultimo_estado: dict[str, str] = self._cargar()

    def _cargar(self) -> dict[str, str]:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def guardar(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.ultimo_estado, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def elegir(self, categoria: str, opciones: list[str]) -> str:
        if not opciones:
            return ""
        if categoria not in self.usados_en_sesion:
            self.usados_en_sesion[categoria] = set()

        ultimo = self.ultimo_estado.get(categoria)
        disponibles = [
            op for op in opciones
            if op not in self.usados_en_sesion[categoria] and op != ultimo
        ]
        if not disponibles:
            disponibles = [op for op in opciones if op != ultimo] or list(opciones)

        # Semilla combinada de tiempo real + entropía del sistema para rotación real en GitHub Actions
        elegido = random.choice(disponibles)
        self.usados_en_sesion[categoria].add(elegido)
        self.ultimo_estado[categoria] = elegido
        return elegido


SELECTOR = SelectorRotativoV11(HISTORIAL_JSON)

# ---------------------------------------------------------------------------
# BANCO DE FRASES AMPLIADO V11 (+150 Frases en 8 Categorías)
# ---------------------------------------------------------------------------

# 1) 26 Frases de Identificación (incluye las frases clave solicitadas)
BANCO_IDENTIFICACION = [
    "La música no se detiene.",
    "Estás conectado.",
    "Esto es Nogaradio.",
    "Escuchás Nogaradio.",
    "Conectados en Nogaradio.",
    "Suena Nogaradio.",
    "Estás en Nogaradio, tu radio.",
    "La radio que te mueve, Nogaradio.",
    "Siempre en tu sintonía, Nogaradio.",
    "Subí el volumen, esto es Nogaradio.",
    "Nogaradio, la energía que suena con vos.",
    "Sentí la música, estás en Nogaradio.",
    "Veinticuatro horas en movimiento, Nogaradio.",
    "Tu conexión directa con los mejores hits, Nogaradio.",
    "Donde estés, suena Nogaradio.",
    "En vivo las veinticuatro horas, esto es Nogaradio.",
    "La frecuencia que marca tu ritmo, Nogaradio.",
    "Prendete al aire de Nogaradio.",
    "Más música, más actitud, estás en Nogaradio.",
    "El sonido que nos une, Nogaradio.",
    "Sin pausas, la música no se detiene en Nogaradio.",
    "Viví el aire de Nogaradio.",
    "Tu lugar en el dial, esto es Nogaradio.",
    "Conectando tus sentidos, escuchás Nogaradio.",
    "Pura potencia musical, Nogaradio.",
    "Desde Resistencia para todo el mundo, Nogaradio.",
]

# 2) 18 Saludos clasificados por momento del día (6 mañana, 6 tarde, 6 noche)
BANCO_SALUDOS_MANANA = [
    "Hola, muy buenos días",
    "Buenos días, qué bueno que estés ahí",
    "Arriba esa mañana, muy buenos días",
    "Excelente mañana para vos",
    "Muy buenos días, arrancamos con toda la energía",
    "Hola, buen día, qué lindo empezar la mañana con vos",
]

BANCO_SALUDOS_TARDE = [
    "Hola, muy buenas tardes",
    "Buenas tardes, seguimos haciéndote el aguante",
    "Muy buenas tardes, qué lindo que estés del otro lado",
    "Excelente tarde para vos",
    "Hola, buenas tardes, poniéndole ritmo a tu jornada",
    "Muy buenas tardes, seguimos compartiendo el aire con vos",
]

BANCO_SALUDOS_NOCHE = [
    "Hola, muy buenas noches",
    "Buenas noches, gracias por estar del otro lado",
    "Muy buenas noches, seguimos acompañándote",
    "Excelente noche para vos",
    "Hola, buenas noches, la mejor compañía para cerrar tu día",
    "Muy buenas noches, conectados con vos bajo las estrellas",
]

# 3) 16 Frases de Presentación del Clima
BANCO_PRESENTACION_CLIMA = [
    "Repasamos al instante cómo está el tiempo en la ciudad.",
    "Salimos afuera y miramos los datos actuales del clima.",
    "Te contamos cómo se comporta el tiempo a esta hora.",
    "Actualizamos el reporte meteorológico para que salgas preparado.",
    "Así están las condiciones del tiempo en este preciso momento.",
    "Chequeamos juntos el termómetro y el estado del cielo.",
    "Vamos directo a los datos actualizados del tiempo.",
    "Miramos por la ventana y actualizamos el clima en la región.",
    "Ponemos al día la temperatura y el pronóstico actual.",
    "Si estás por salir, prestá atención a cómo está el tiempo.",
    "Actualización meteorológica al instante en tu radio.",
    "Estos son los registros actuales de temperatura y humedad.",
    "Te ponemos al tanto de cómo sigue el clima hoy.",
    "Momento de repasar el estado del tiempo en nuestra zona.",
    "Datos frescos del clima para acompañarte en esta hora.",
    "Así se presenta el panorama del tiempo ahora mismo.",
]

# 4) 20 Presentaciones de Noticias
BANCO_PRESENTACION_NOTICIAS = [
    "Flash internacional:",
    "Actualidad internacional:",
    "En el plano internacional:",
    "Mirada global en Nogaradio:",
    "Lo que pasa en el mundo a esta hora:",
    "Titulares internacionales del momento:",
    "Información destacada en el mundo:",
    "Noticias que marcan la agenda global:",
    "Conectamos con la actualidad internacional:",
    "En las noticias más importantes del mundo:",
    "Y en la música:",
    "Pasando a la música:",
    "En novedades musicales:",
    "En el universo de la música:",
    "Lo nuevo de tus artistas favoritos:",
    "Tendencias y lanzamientos musicales:",
    "En la escena musical de hoy:",
    "Noticias del mundo de la música:",
    "Actualidad musical en Nogaradio:",
    "Lo que suena y es noticia en la música:",
]

# 5) 16 Transiciones Conversacionales entre Locutores
BANCO_TRANSICIONES_LOCUTORES = [
    "No te muevas, ya llegan los titulares.",
    "Quedate ahí, vamos directo al flash informativo.",
    "Seguí con nosotros, actualizamos las noticias.",
    "Subí el volumen, ya llega toda la información.",
    "Ahora sí, pasamos a las noticias de esta hora.",
    "Cambiamos de frente y nos metemos en las noticias.",
    "Abrimos el compacto informativo del momento.",
    "Así está el tiempo, y ahora vamos directo a los titulares.",
    "Dejamos el clima y repasamos qué está pasando hoy.",
    "Tomá nota, porque ya compartimos la información clave.",
    "En segundos te ponemos al día con la actualidad.",
    "Pasamos la posta y actualizamos las noticias.",
    "Seguimos adelante, es momento de informarnos.",
    "Con el clima actualizado, abrimos la ventana informativa.",
    "A continuación, las noticias que tenés que saber.",
    "Ritmo e información al instante, vamos a los titulares.",
]

# 6) 26 Remates Finales
BANCO_REMATES_FINALES = [
    "Seguí con esta programación. En Nogaradio, seguimos con vos.",
    "Descargá la app en Play Store y Apple Store. Escuchás Nogaradio, sonando para vos.",
    "Veinticuatro siete con la mejor música. En Nogaradio, estamos siempre con vos.",
    "Estamos en Play Store y Apple Store. Esto es Nogaradio, conectados con vos.",
    "Seguí con esta programación. Viví la música en Nogaradio, siempre junto a vos.",
    "En todas partes y en tu celular. Nogaradio, veinticuatro siete sonando para vos.",
    "No cambies de sintonía, seguí con esta programación. En Nogaradio, seguimos con vos.",
    "La música no se detiene. Estás conectado a Nogaradio.",
    "Más hits, más energía. Esto es Nogaradio, siempre con vos.",
    "Subí el volumen y quedate acá. En Nogaradio, sonando para vos.",
    "Tu radio, tu música, tu momento. Escuchás Nogaradio.",
    "Todo el día, toda la noche. En Nogaradio, seguimos con vos.",
    "Donde hay buena música, estás vos. Esto es Nogaradio.",
    "Seguimos girando los mejores éxitos. Nogaradio, conectados con vos.",
    "Que no pare la música. Seguí en la sintonía de Nogaradio.",
    "Acompañando cada hora de tu vida. En Nogaradio, estamos con vos.",
    "Energía positiva y los mejores temas. Esto es Nogaradio.",
    "Ya estás informado, ahora sigue la música en Nogaradio.",
    "La radio que va con tu ritmo. Nogaradio, siempre junto a vos.",
    "Conectados las veinticuatro horas. Escuchás Nogaradio.",
    "El mejor sonido está en el aire. Seguimos con vos en Nogaradio.",
    "No te bajes de este viaje musical. Esto es Nogaradio.",
    "Pura música sin interrupciones. En Nogaradio, sonando para vos.",
    "Cada canción tiene una historia. Vivila en Nogaradio.",
    "Seguimos encendidos en tu dial. Nogaradio, siempre con vos.",
    "La combinación perfecta de noticias y música. Esto es Nogaradio.",
]

# 7) 20 Frases Musicales
BANCO_FRASES_MUSICALES = [
    "La música no se detiene ni un segundo.",
    "Todos los hits que te gustan suenan acá.",
    "Subí el volumen, que la música se siente más fuerte.",
    "Una selección exclusiva con las canciones que te mueven.",
    "Desde los clásicos hasta los lanzamientos de hoy, todo en tu radio.",
    "El ritmo que acompaña tu día está en el aire.",
    "Canciones elegidas para levantar tu energía al máximo.",
    "Acá viven los éxitos que no podés dejar de cantar.",
    "Pura música las veinticuatro horas, sin cortes.",
    "Sentí el bajo, sentí el ritmo, la música sigue sonando.",
    "La banda sonora de tu día pasa por esta sintonía.",
    "Ponete los auriculares y dejate llevar por la música.",
    "Los artistas que marcan tendencia suenan primero acá.",
    "Conecta con tus canciones favoritas a toda hora.",
    "Un viaje directo a los mejores sonidos del momento.",
    "No importa qué hora sea, acá siempre hay buena música.",
    "La playlist perfecta para acompañarte donde estés.",
    "Más canciones por hora, más energía en tus oídos.",
    "Vibrá con la música que hace latir la ciudad.",
    "El próximo tema te va a encantar, quedate escuchando.",
]

# 8) 12 Promociones de la Aplicación (Play Store y Apple Store)
BANCO_PROMOS_APP = [
    "Llevanos siempre en tu celu, estamos en Play Store y Apple Store.",
    "Descargá gratis nuestra aplicación oficial, disponible en Play Store y Apple Store.",
    "Buscanos como Nogaradio en Play Store y Apple Store y escuchanos a donde vayas.",
    "¿Todavía no tenés la app? Entrá ahora a Play Store o Apple Store y llevá a Nogaradio con vos.",
    "Con nuestra app en Play Store y Apple Store, la música de Nogaradio te acompaña en todo el mundo.",
    "Calidad digital en tu bolsillo: descargá Nogaradio desde Play Store y Apple Store.",
    "En Android y en iPhone, encontranos en Play Store y Apple Store.",
    "Conectate desde cualquier lugar bajando nuestra app en Play Store y Apple Store.",
    "Toda nuestra programación en vivo en tu teléfono, disponible en Play Store y Apple Store.",
    "Sumate a la experiencia móvil de Nogaradio, buscá nuestra app en Play Store y Apple Store.",
    "Un toque en tu pantalla y empieza la música: estamos en Play Store y Apple Store.",
    "Escuchanos sin límites desde la app oficial en Play Store y Apple Store.",
]

# ---------------------------------------------------------------------------
# Diccionarios de Clima y Hora
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
        return SELECTOR.elegir("saludo_m", BANCO_SALUDOS_MANANA)
    elif 12 <= hora_actual < 20:
        return SELECTOR.elegir("saludo_t", BANCO_SALUDOS_TARDE)
    return SELECTOR.elegir("saludo_n", BANCO_SALUDOS_NOCHE)


# ---------------------------------------------------------------------------
# Obtención de Clima y Noticias RSS (Conservado de V10 + Banco V11)
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


def obtener_titular_verificado(lista_feeds: list[str], categoria: str, cantidad: int = 20) -> str | None:
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
    elegida = SELECTOR.elegir(f"rss_{categoria}", candidatos_top)
    print(f"Noticia [{categoria}] seleccionada ({len(entradas_unicas)} disp.): {elegida[:75]}...")
    return elegida


# ---------------------------------------------------------------------------
# Motor DSP Base + Herramientas de Audio V10 y V11
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
    return np.zeros((max(1, int(SR * dur_ms / 1000)), 2))


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


def _recortar_silencio(x: np.ndarray, umbral: float = 0.015) -> np.ndarray:
    env = np.abs(x).max(axis=1)
    activos = np.where(env > umbral)[0]
    if len(activos) == 0:
        return x
    ini = max(0, activos[0] - int(SR * 0.025))
    fin = min(len(x), activos[-1] + int(SR * 0.040))
    return _fade(x[ini:fin], 10, 25)


def _extraer_cola_suave_sin_cortes(x: np.ndarray) -> np.ndarray:
    """Rampa coseno suave de V10 para extraer el final de una frase sin cortes ni clics."""
    n = len(x)
    min_m = int(SR * 0.34)
    max_m = min(int(SR * 0.68), int(n * 0.60))
    if max_m <= min_m:
        trozo = x[-min(n, int(SR * 0.45)):].copy()
    else:
        tramo = np.abs(x[n - max_m : n - min_m]).max(axis=1)
        win = max(1, int(SR * 0.012))
        env_s = np.convolve(tramo, np.ones(win) / win, mode="same")
        idx_corte = (n - max_m) + int(np.argmin(env_s))
        trozo = x[idx_corte:].copy()

    r_in = min(int(SR * 0.090), len(trozo) // 3)
    r_out = min(int(SR * 0.050), len(trozo) // 4)
    if r_in > 0:
        curva_in = 0.5 * (1.0 - np.cos(np.pi * np.linspace(0.0, 1.0, r_in)))
        trozo[:r_in] *= curva_in[:, None]
    if r_out > 0:
        curva_out = 0.5 * (1.0 + np.cos(np.pi * np.linspace(0.0, 1.0, r_out)))
        trozo[-r_out:] *= curva_out[:, None]
    return trozo


def _chorus_estereo_fm(x: np.ndarray) -> np.ndarray:
    n = len(x)
    t = np.arange(n) / SR
    idx = np.arange(n, dtype=np.float64)
    delay_l = 0.011 * SR + 0.0018 * SR * np.sin(2 * np.pi * 1.8 * t)
    delay_r = 0.016 * SR + 0.0018 * SR * np.cos(2 * np.pi * 1.6 * t)

    l_mod = np.interp(np.maximum(0.0, idx - delay_l), idx, x[:, 0])
    r_mod = np.interp(np.maximum(0.0, idx - delay_r), idx, x[:, 1])
    estereo_ancho = np.column_stack((l_mod, r_mod))
    estereo_ancho = _filtrar(estereo_ancho, pasa_altos=220, pasa_bajos=5500)
    return x * 0.78 + estereo_ancho * 0.42


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


# ---------------------------------------------------------------------------
# NUEVOS PROCESADORES VOCALES V11 (Palabras Específicas, Coros y Diálogos)
# ---------------------------------------------------------------------------

def aplicar_fx_palabra_especifica(x: np.ndarray, tipo_fx: str) -> np.ndarray:
    """
    Aplica efectos de Radio Imaging a palabras o frases puntuales (IDs, frases musicales,
    menciones de marca o promos) sin afectar los bloques secos de noticias/clima:
      - 'glitch_vocal': Micro-repetición rítmica de la sílaba inicial en estéreo + voz intacta.
      - 'pitch_shift_armonia': Armonía paralela octava/quinta limpia en estéreo + presencia.
      - 'reverse_tail_swell': Swell invertido suave en pista paralela que envuelve la frase.
      - 'radio_filter_band': Efecto megáfono/radio FM con apertura progresiva.
      - 'echo_stereo_delay': Delay Ping-Pong sincronizado en la cola de la palabra clave.
    """
    cola = _extraer_cola_suave_sin_cortes(x)
    cola_eq = _filtrar(cola, pasa_altos=380, pasa_bajos=3600)

    if tipo_fx == "glitch_vocal":
        # Tartamudeo digital limpio sobre los primeros 95ms antes de soltar la palabra entera
        n_g = min(int(SR * 0.095), len(x) // 4)
        trozo_g = _fade(_filtrar(x[:n_g].copy(), pasa_altos=450, pasa_bajos=4200), 8, 12) * 0.72
        g_l = np.column_stack((trozo_g[:, 0], trozo_g[:, 1] * 0.2))
        g_r = np.column_stack((trozo_g[:, 0] * 0.2, trozo_g[:, 1]))
        cuerpo = np.vstack((g_l, g_r, x))
        out = np.pad(cuerpo, ((0, int(SR * 0.55)), (0, 0)))
        _sumar(out, _stereo_delay(cola_eq, 190, 285, 0.35, 0.45), _dur_ms(cuerpo) - 20.0, 0.55)
        return out

    elif tipo_fx == "pitch_shift_armonia":
        # Agrega una capa doblada con micro-pitch (+12 cents L / -12 cents R) + sub-armónico suave
        n = len(x)
        idx = np.arange(n, dtype=np.float64)
        up = np.interp(np.clip(idx * 1.008, 0, n - 1), idx, x[:, 0])
        dn = np.interp(np.clip(idx * 0.992, 0, n - 1), idx, x[:, 1])
        armonia = _filtrar(np.column_stack((up, dn)), pasa_altos=200, pasa_bajos=4800)
        mezcla = x * 0.82 + armonia * 0.38
        out = np.pad(mezcla, ((0, int(SR * 0.60)), (0, 0)))
        _sumar(out, cola_eq, _dur_ms(mezcla) - 15.0, 0.48)
        return out

    elif tipo_fx == "reverse_tail_swell":
        # El locutor habla sin cortes, y por debajo en estéreo sube la cola invertida hacia el final
        rev = _filtrar(cola_eq[::-1].copy(), pasa_altos=400, pasa_bajos=3400)
        rev = _reverb_estereo(rev, seg=0.9, mezcla=0.65, brillo=4000)[: len(rev)]
        curva = (np.linspace(0.0, 1.0, len(rev)) ** 2.0)[:, None]
        rev_swell = _fade(rev * curva * 0.42, 25, 25)
        out = np.pad(x, ((0, int(SR * 0.70)), (0, 0)))
        pos_rev = max(0.0, _dur_ms(x) - _dur_ms(rev_swell))
        _sumar(out, rev_swell, pos_rev, 0.85)
        _sumar(out, _stereo_delay(cola_eq, 210, 315, 0.38, 0.45), _dur_ms(x) - 15.0, 0.52)
        return out

    elif tipo_fx == "radio_filter_band":
        # Filtra la primera mitad con efecto Radio Transmisor FM y abre limpio en la palabra final
        cuerpo = x.copy()
        mitad = int(len(cuerpo) * 0.55)
        radio = np.tanh(_filtrar(cuerpo[:mitad], pasa_altos=550, pasa_bajos=2700) * 2.1) * 0.78
        curva = np.linspace(0.9, 0.0, mitad)[:, None]
        cuerpo[:mitad] = cuerpo[:mitad] * (1.0 - curva * 0.70) + radio * (curva * 0.80)
        out = np.pad(cuerpo, ((0, int(SR * 0.65)), (0, 0)))
        _sumar(out, _stereo_delay(cola_eq, 220, 330, 0.35, 0.45), _dur_ms(cuerpo) - 15.0, 0.50)
        return out

    else:
        # "echo_stereo_delay": Voz ancha con Stereo Delay Ping-Pong marcado
        voz_ancha = _chorus_estereo_fm(x)
        out = np.pad(voz_ancha, ((0, int(SR * 0.85)), (0, 0)))
        base_ms = _dur_ms(voz_ancha) - 20.0
        for rep, c in [(1, 0), (2, 1), (3, 0)]:
            tramo = np.zeros_like(cola_eq)
            tramo[:, c] = cola_eq[:, c] * 0.95
            tramo[:, 1 - c] = cola_eq[:, 1 - c] * 0.25
            _sumar(out, tramo, base_ms + (rep - 1) * 235.0, 0.54 ** rep)
        return out


def crear_voz_doble_coro(voz_hombre: np.ndarray, voz_mujer: np.ndarray) -> np.ndarray:
    """
    Superpone la voz masculina grave y la voz femenina juvenil diciendo la misma frase clave
    (Coro / Stack Vocal de FM), alineando sus duraciones y abriéndolas en el campo estéreo.
    """
    n_target = max(len(voz_hombre), len(voz_mujer))
    idx_t = np.linspace(0, 1, n_target)

    def _ajustar_largo(v: np.ndarray) -> np.ndarray:
        idx_v = np.linspace(0, 1, len(v))
        l = np.interp(idx_t, idx_v, v[:, 0])
        r = np.interp(idx_t, idx_v, v[:, 1])
        return np.column_stack((l, r))

    h_sync = _ajustar_largo(voz_hombre)
    m_sync = _ajustar_largo(voz_mujer)

    # Hombre firme al centro-izquierda, Mujer brillante al centro-derecha + copia Chorus
    coro_l = h_sync[:, 0] * 0.68 + m_sync[:, 0] * 0.52
    coro_r = h_sync[:, 1] * 0.52 + m_sync[:, 1] * 0.68
    coro = _chorus_estereo_fm(np.column_stack((coro_l, coro_r)))
    return _pico(coro, 0.94)


def ensamblar_dialogo_interactivo(
    segmentos: list[tuple[np.ndarray, float]],
) -> np.ndarray:
    """
    Une intervenciones de Hombre y Mujer donde pueden responderse al instante,
    interrumpirse (solapamiento negativo, ej. -70ms) o complementarse.
    Cada elemento de 'segmentos' es (audio_array, offset_ms_respecto_al_anterior).
    """
    if not segmentos:
        return _pista(100)

    cursor_ms = 0.0
    posiciones = []
    max_fin_ms = 0.0
    for seg, gap_ms in segmentos:
        cursor_ms = max(0.0, cursor_ms + gap_ms)
        posiciones.append((seg, cursor_ms))
        fin_seg = cursor_ms + _dur_ms(seg)
        if fin_seg > max_fin_ms:
            max_fin_ms = fin_seg
        cursor_ms = fin_seg

    pista = _pista(max_fin_ms + 100.0)
    for seg, ini_ms in posiciones:
        _sumar(pista, seg, ini_ms, 1.0)
    return pista


# ---------------------------------------------------------------------------
# Efectos Vocales de Inicio y Remate Final Conservados de V10
# ---------------------------------------------------------------------------

def procesar_tag_inicio_fm(tag_crudo: np.ndarray) -> tuple[np.ndarray, str]:
    estilo = SELECTOR.elegir(
        "fx_tag_ini",
        ["stereo_widener_pingpong", "radio_fm_open_echo", "crystal_slap_stereo", "glitch_vocal_intro"]
    )
    if estilo == "glitch_vocal_intro":
        return aplicar_fx_palabra_especifica(tag_crudo, "glitch_vocal"), "Glitch Vocal + Stereo Delay"

    cola_pal = _extraer_cola_suave_sin_cortes(tag_crudo)
    eco_pal = _filtrar(cola_pal, pasa_altos=380, pasa_bajos=3500)
    eco_rev = _reverb_estereo(eco_pal, seg=0.95, mezcla=0.38, brillo=4200)[: len(eco_pal)]

    if estilo == "stereo_widener_pingpong":
        voz_ancha = _chorus_estereo_fm(tag_crudo)
        out = np.pad(voz_ancha, ((0, int(SR * 0.65)), (0, 0)))
        base_ms = _dur_ms(voz_ancha) - 20.0
        for rep, c in [(1, 0), (2, 1)]:
            tramo = np.zeros_like(eco_rev)
            tramo[:, c] = eco_rev[:, c] * 0.95
            tramo[:, 1 - c] = eco_rev[:, 1 - c] * 0.25
            _sumar(out, tramo, base_ms + (rep - 1) * 230.0, 0.50 ** rep)
        return out, "Stereo Widener + Ping-Pong Limpio"

    elif estilo == "radio_fm_open_echo":
        cuerpo = tag_crudo.copy()
        mitad = len(cuerpo) // 2
        radio = np.tanh(_filtrar(cuerpo[:mitad], pasa_altos=500, pasa_bajos=3000) * 1.9) * 0.78
        curva = np.linspace(0.85, 0.0, mitad)[:, None]
        cuerpo[:mitad] = cuerpo[:mitad] * (1.0 - curva * 0.65) + radio * (curva * 0.75)

        out = np.pad(cuerpo, ((0, int(SR * 0.60)), (0, 0)))
        _sumar(out, eco_rev, _dur_ms(cuerpo) - 15.0, 0.48)
        return out, "Filtro FM Open + Eco Estéreo"

    else:
        out = np.pad(tag_crudo, ((0, int(SR * 0.65)), (0, 0)))
        slap = _filtrar(tag_crudo, pasa_altos=400, pasa_bajos=3800) * 0.22
        _sumar(out, np.column_stack((slap[:, 0], slap[:, 1] * 0.3)), 95.0, 1.0)
        _sumar(out, eco_rev, _dur_ms(tag_crudo) - 15.0, 0.48)
        return out, "Crystal Slapback FM + Cola"


def procesar_remate_final_fm(remate_crudo: np.ndarray) -> tuple[np.ndarray, str]:
    """Los 5 efectos finales limpios de V10 (sin Reverb Slowed)."""
    estilo = SELECTOR.elegir("fx_remate_fin", [
        "pingpong_cristal_fm",
        "chorus_widener_eco",
        "radio_telefonico_throw",
        "multitap_arena_hall",
        "phaser_pingpong_fm",
    ])

    palabra_fin = _extraer_cola_suave_sin_cortes(remate_crudo)
    pal_eq = _filtrar(palabra_fin, pasa_altos=360, pasa_bajos=3600)
    pal_rev = _reverb_estereo(pal_eq, seg=1.20, mezcla=0.42, brillo=4500)[: len(pal_eq)]

    if estilo == "pingpong_cristal_fm":
        cola_ms = 1550
        out = _pista(_dur_ms(remate_crudo) + cola_ms)
        _sumar(out, remate_crudo, 0.0, 1.0)
        base_ms = _dur_ms(remate_crudo) - 20.0
        for rep in range(1, 5):
            pos_ms = base_ms + (rep - 1) * 260.0
            c = rep % 2
            tramo = np.zeros_like(pal_rev)
            tramo[:, c] = pal_rev[:, c] * 0.95
            tramo[:, 1 - c] = pal_rev[:, 1 - c] * 0.25
            _sumar(out, tramo, pos_ms, 0.58 ** rep)
        return out, "Eco Ping-Pong Cristal FM"

    elif estilo == "chorus_widener_eco":
        voz_ancha = _chorus_estereo_fm(remate_crudo)
        pal_ancha = _chorus_estereo_fm(pal_rev)
        cola_ms = 1550
        out = _pista(_dur_ms(voz_ancha) + cola_ms)
        _sumar(out, voz_ancha, 0.0, 1.0)
        base_ms = _dur_ms(voz_ancha) - 20.0
        for rep in range(1, 5):
            pos_ms = base_ms + (rep - 1) * 255.0
            c = (rep + 1) % 2
            tramo = np.zeros_like(pal_ancha)
            tramo[:, c] = pal_ancha[:, c] * 0.95
            tramo[:, 1 - c] = pal_ancha[:, 1 - c] * 0.30
            _sumar(out, tramo, pos_ms, 0.58 ** rep)
        return out, "Stereo Chorus Widener + Ping-Pong"

    elif estilo == "radio_telefonico_throw":
        pal_radio = np.tanh(_filtrar(palabra_fin, pasa_altos=550, pasa_bajos=2600) * 2.1) * 0.68
        pal_radio = _reverb_estereo(pal_radio, seg=1.1, mezcla=0.38, brillo=3200)[: len(pal_radio)]
        cola_ms = 1550
        out = _pista(_dur_ms(remate_crudo) + cola_ms)
        _sumar(out, remate_crudo, 0.0, 1.0)
        base_ms = _dur_ms(remate_crudo) - 15.0
        for rep in range(1, 5):
            pos_ms = base_ms + (rep - 1) * 250.0
            c = rep % 2
            tramo = np.zeros_like(pal_radio)
            tramo[:, c] = pal_radio[:, c] * 0.95
            tramo[:, 1 - c] = pal_radio[:, 1 - c] * 0.25
            _sumar(out, tramo, pos_ms, 0.60 ** rep)
        return out, "Filtro Radio Telefónico Throw + Eco"

    elif estilo == "multitap_arena_hall":
        pal_hall = _reverb_estereo(pal_eq, seg=1.45, mezcla=0.52, brillo=4800)[: len(pal_eq)]
        cola_ms = 1600
        out = _pista(_dur_ms(remate_crudo) + cola_ms)
        _sumar(out, remate_crudo, 0.0, 1.0)
        base_ms = _dur_ms(remate_crudo) - 15.0
        taps = [(0.0, 0, 0.58), (195.0, 1, 0.45), (390.0, 0, 0.34), (620.0, 1, 0.24), (860.0, 0, 0.16)]
        for offset_ms, c, gan in taps:
            tramo = np.zeros_like(pal_hall)
            tramo[:, c] = pal_hall[:, c] * 0.95
            tramo[:, 1 - c] = pal_hall[:, 1 - c] * 0.30
            _sumar(out, tramo, base_ms + offset_ms, gan)
        return out, "Multi-Tap Arena Hall Estéreo"

    else:
        pal_ph = _phaser_estereo_fx(pal_rev)
        cola_ms = 1550
        out = _pista(_dur_ms(remate_crudo) + cola_ms)
        _sumar(out, remate_crudo, 0.0, 1.0)
        base_ms = _dur_ms(remate_crudo) - 20.0
        for rep in range(1, 5):
            pos_ms = base_ms + (rep - 1) * 255.0
            c = rep % 2
            tramo = np.zeros_like(pal_ph)
            tramo[:, c] = pal_ph[:, c] * 0.95
            tramo[:, 1 - c] = pal_ph[:, 1 - c] * 0.28
            _sumar(out, tramo, pos_ms, 0.58 ** rep)
        return out, "Phaser Espacial Ping-Pong FM"


# ---------------------------------------------------------------------------
# Sintetizadores Musicales y Firma Sonora de Nogaradio (Sonic Logo V11)
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


def _sonic_logo_nogaradio() -> np.ndarray:
    """
    Firma Sonora Oficial y Reconocible de Nogaradio (4 sílabas musicales: NO-GA-RA-DIO):
    Motivo en Re Mayor 9 (D5 -> F#5 -> A5 -> D6) con campanas FM en estéreo + colchón cálido.
    """
    pista = _pista(1450)
    # 4 notas que cantan rítmicamente "No - ga - ra - dio"
    motivo = [
        (587.33, 0, 320, 0.25),     # NO (D5)
        (739.99, 130, 320, 0.75),   # GA (F#5)
        (880.00, 260, 380, 0.30),   # RA (A5)
        (1174.66, 410, 950, 0.65),  # DIO (D6 brillante sostenido)
    ]
    for freq, pos_ms, dur, pan in motivo:
        _sumar(pista, _pop_pluck(freq, dur, pan=pan), pos_ms, 0.95)
        _sumar(pista, _pop_pluck(freq * 2.0, dur * 0.7, pan=1.0 - pan), pos_ms, 0.35)

    # Acorde de respaldo Dmaj9 en la cuarta nota
    _sumar(pista, _synth_brass([293.66, 369.99, 440.00, 587.33, 659.25], 950, corte_lp=3200), 410, 0.65)
    pista = _stereo_delay(pista, 195, 290, 0.38, 0.34)
    return _fade(_reverb_estereo(pista, seg=1.1, mezcla=0.32)[: len(pista)], 10, 300)


# ---------------------------------------------------------------------------
# 8 NUEVOS ESTILOS DE RADIO IMAGING V11 (Cortinas + Sweepers)
# ---------------------------------------------------------------------------

ESTILOS_IMAGING_V11 = [
    "electronic_imaging",
    "cinematic_imaging",
    "dance_club_imaging",
    "pop_imaging",
    "urban_imaging",
    "epic_power_imaging",
    "retro_synthwave",
    "emotional_imaging",
]


def generar_bed_estilo_imaging(estilo: str, modo_clima: str, dur_ms: float) -> np.ndarray:
    """
    Genera la cortina musical según los 8 nuevos estilos de Radio Imaging profesional
    integrando sutilmente la atmósfera climática actual.
    """
    pista = _pista(dur_ms)
    t = _t(dur_ms)

    # Definición de BPM, progresión armónica y carácter según el estilo elegido
    perfiles = {
        "electronic_imaging": (124.0, [
            ([220.0, 261.63, 329.63, 392.0], [440.0, 659.25, 880.0, 659.25], 55.0),
            ([174.61, 220.0, 261.63, 349.23], [349.23, 523.25, 698.46, 523.25], 43.65),
        ], 2400.0),
        "cinematic_imaging": (100.0, [
            ([146.83, 220.0, 293.66, 349.23], [587.33, 440.0, 698.46, 587.33], 36.71),
            ([116.54, 174.61, 233.08, 293.66], [466.16, 349.23, 587.33, 466.16], 29.14),
        ], 1500.0),
        "dance_club_imaging": (128.0, [
            ([261.63, 311.13, 392.0, 466.16], [523.25, 622.25, 783.99, 1046.50], 65.41),
            ([207.65, 261.63, 311.13, 392.0], [415.30, 523.25, 622.25, 830.61], 51.91),
        ], 2800.0),
        "pop_imaging": (120.0, [
            ([293.66, 369.99, 440.0, 587.33], [587.33, 739.99, 880.0, 1174.66], 73.42),
            ([220.0, 277.18, 329.63, 440.0], [440.0, 554.37, 659.25, 880.0], 55.00),
            ([196.0, 246.94, 293.66, 392.0], [392.0, 493.88, 587.33, 783.99], 49.00),
        ], 2200.0),
        "urban_imaging": (98.0, [
            ([164.81, 196.0, 246.94, 329.63], [659.25, 493.88, 783.99, 659.25], 41.20),
            ([130.81, 164.81, 196.0, 246.94], [523.25, 392.0, 659.25, 493.88], 32.70),
        ], 1700.0),
        "epic_power_imaging": (116.0, [
            ([164.81, 246.94, 329.63, 392.0], [659.25, 783.99, 987.77, 1318.51], 41.20),
            ([130.81, 196.0, 261.63, 329.63], [523.25, 659.25, 783.99, 1046.50], 32.70),
            ([146.83, 220.0, 293.66, 369.99], [587.33, 739.99, 880.0, 1174.66], 36.71),
        ], 2500.0),
        "retro_synthwave": (110.0, [
            ([220.0, 261.63, 329.63, 493.88], [440.0, 523.25, 659.25, 987.77], 55.00),
            ([174.61, 220.0, 261.63, 392.00], [349.23, 440.0, 523.25, 783.99], 43.65),
        ], 2100.0),
        "emotional_imaging": (104.0, [
            ([261.63, 329.63, 392.0, 493.88], [523.25, 659.25, 783.99, 987.77], 65.41),
            ([220.0, 261.63, 329.63, 392.0], [440.0, 523.25, 659.25, 783.99], 55.00),
            ([174.61, 220.0, 261.63, 349.23], [349.23, 440.0, 523.25, 698.46], 43.65),
        ], 1400.0),
    }

    bpm, acordes, corte_filtro = perfiles.get(estilo, perfiles["pop_imaging"])
    beat_ms = 60000.0 / bpm
    semi_ms = beat_ms / 4.0
    compas_ms = beat_ms * 4.0

    musica = _pista(dur_ms)
    num_compases = int(dur_ms / compas_ms) + 2

    for c in range(num_compases):
        ini_c = c * compas_ms
        if ini_c >= dur_ms:
            break
        pad_f, arp_f, bajo_f = acordes[c % len(acordes)]
        _sumar(musica, _synth_brass(pad_f, compas_ms + 150, corte_lp=corte_filtro), ini_c, 0.56)

        if estilo == "retro_synthwave":
            # Bajo en corcheas estilo 80s Synthwave
            for k in range(8):
                _sumar(musica, _funky_bass(bajo_f if k % 2 == 0 else bajo_f * 2.0, beat_ms * 0.45), ini_c + k * (beat_ms * 0.5), 0.78)
        elif estilo == "urban_imaging":
            # Sub-808 largo sincopado estilo Urbano
            for pos_u in [0.0, beat_ms * 1.5, beat_ms * 2.5]:
                _sumar(musica, _funky_bass(bajo_f, beat_ms * 1.1), ini_c + pos_u, 0.85)
        else:
            for k in range(8):
                f_b = bajo_f * 2.0 if k in (3, 7) else bajo_f
                _sumar(musica, _funky_bass(f_b, beat_ms * 0.45), ini_c + k * (beat_ms * 0.5), 0.76)

        # Arpegio estéreo
        pasos_arp = 8 if estilo in ("cinematic_imaging", "emotional_imaging") else 16
        paso_ms = compas_ms / pasos_arp
        for s in range(pasos_arp):
            nota_f = arp_f[s % len(arp_f)]
            pan = 0.20 if s % 2 == 0 else 0.80
            _sumar(musica, _pop_pluck(nota_f, 200, pan=pan), ini_c + s * paso_ms, 0.52)

    fase_beat = (t * (1000.0 / beat_ms)) % 1.0
    sidechain = 0.48 + 0.52 * (1.0 - np.exp(-fase_beat * 5.0))
    musica *= sidechain[:, None]
    pista += _stereo_delay(musica, int(beat_ms * 0.5), int(beat_ms * 0.75), 0.30, 0.24)

    # Batería adaptada al estilo
    kick = _kick_punch(230)
    snare = _snare_pop(185)
    hh_c = _hihat_crisp(55, abierto=False)
    hh_o = _hihat_crisp(150, abierto=True)

    for b in range(int(dur_ms / beat_ms) + 1):
        pos_b = b * beat_ms
        _sumar(pista, kick, pos_b, 0.85)
        if b % 2 == 1:
            _sumar(pista, snare, pos_b, 0.72)
        _sumar(pista, hh_c, pos_b, 0.60)
        if estilo == "urban_imaging" and b % 2 == 1:
            # Trap hi-hat roll rápido
            for r in range(4):
                _sumar(pista, hh_c, pos_b + r * (semi_ms * 0.5), 0.50)
        else:
            _sumar(pista, hh_o, pos_b + beat_ms * 0.5, 0.55)

    # Textura climática si llueve o hay tormenta
    if modo_clima in ("lluvia", "tormenta"):
        agua = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=1100, pasa_bajos=7200)
        oleaje = 0.75 + 0.25 * np.sin(2 * np.pi * 0.3 * t)
        pista += agua * oleaje[:, None] * 0.18
        if modo_clima == "tormenta":
            for pos_tr in (dur_ms * 0.20, dur_ms * 0.65):
                t_tr = _t(2400)
                trueno = _filtrar(np.random.uniform(-1, 1, (len(t_tr), 2)), pasa_altos=30, pasa_bajos=150)
                env_tr = np.minimum(t_tr / 0.15, 1.0) * np.exp(-t_tr * 1.3)
                _sumar(pista, _pico(trueno * env_tr[:, None], 0.75), pos_tr, 0.65)

    pista = _hueco_vocal_cortina(pista)
    return _filtrar(pista, pasa_altos=40, pasa_bajos=10500)


# ---------------------------------------------------------------------------
# Intros Instrumentales V10 + Nuevas Intros Vocales de 3s, 6s y 10s (V11)
# ---------------------------------------------------------------------------

def sintetizar_intro_instrumental_v10() -> np.ndarray:
    """Conserva las 6 intros instrumentales de V10 y les suma el Sonic Logo de Nogaradio."""
    estilo = SELECTOR.elegir("intro_inst_v10", [
        "dial_tuning_pips",
        "urbana_wah_groove",
        "la100_crystal_bells",
        "pop_funk_slap_intro",
        "cyber_glitch_stutter",
        "stadium_arena_hit",
    ])
    print(f"Intro Instrumental V10 elegida: [{estilo}]")
    pista = _pista(2900)

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

    elif estilo == "urbana_wah_groove":
        acorde_wah = _synth_brass([174.61, 220.0, 261.63, 329.63, 392.00], 800, corte_lp=3800)
        acorde_wah = _phaser_estereo_fx(acorde_wah) * (np.linspace(0.2, 1.0, len(acorde_wah))[:, None])
        _sumar(pista, acorde_wah, 0, 0.85)
        for pos_k in [0, 260, 520, 800, 1050]:
            _sumar(pista, _kick_punch(220), pos_k, 0.95)
        _sumar(pista, _snare_pop(180), 400, 0.85)
        _sumar(pista, _sub_drop_cine(1300), 800, 0.95)

    elif estilo == "la100_crystal_bells":
        _sumar(pista, _whoosh_aerodinamico(780, 350, 5200), 0, 0.85)
        for idx_b, f_b in enumerate([523.25, 659.25, 783.99, 987.77, 1174.66, 1318.51]):
            _sumar(pista, _pop_pluck(f_b, 320, pan=0.15 + 0.7 * (idx_b / 5.0)), idx_b * 115, 0.85)
        _sumar(pista, _sub_drop_cine(1300), 780, 0.95)
        _sumar(pista, _kick_punch(280), 800, 1.0)

    elif estilo == "pop_funk_slap_intro":
        for pos_s, f_s in [(0, 55.0), (190, 110.0), (380, 55.0), (570, 98.0), (800, 65.41)]:
            _sumar(pista, _funky_bass(f_s, 200), pos_s, 0.95)
            _sumar(pista, _kick_punch(200), pos_s, 0.90)
        _sumar(pista, _snare_pop(180), 380, 0.90)
        _sumar(pista, _whoosh_aerodinamico(450, 400, 4800), 350, 0.85)
        _sumar(pista, _sub_drop_cine(1300), 800, 0.95)

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

    else:
        for idx_r, pos_r in enumerate([0, 180, 360, 480, 600, 700]):
            gan_r = 0.45 + 0.55 * (idx_r / 5.0)
            _sumar(pista, _snare_pop(190), pos_r, gan_r)
            _sumar(pista, _kick_punch(220), pos_r, gan_r * 0.8)
        _sumar(pista, _whoosh_aerodinamico(750, 300, 4600), 50, 0.85)
        _sumar(pista, _sub_drop_cine(1400), 800, 1.0)
        _sumar(pista, _kick_punch(300), 800, 1.0)

    # Sella con la firma sonora reconocible de Nogaradio en ms 820
    _sumar(pista, _sonic_logo_nogaradio(), 820, 0.98)
    return _fade(_reverb_estereo(pista, seg=1.25, mezcla=0.32)[: len(pista)], 15, 350)


def generar_intro_vocal_profesional(
    client,
    audio_config,
    duracion_seg: int,
    estilo_imaging: str,
) -> np.ndarray:
    """
    Crea las Intros Vocales Profesionales de 3, 6 o 10 segundos:
    Alterna voz masculina grave y voz femenina juvenil con frases clave
    ("La música no se detiene", "Estás conectado", "Esto es Nogaradio"),
    aplicando Glitch Vocal, Pitch Shift, Reverse Tail, Radio Filter, Echo y Sonic Logo.
    """
    dur_ms = float(duracion_seg * 1000)
    print(f"Generando Intro Vocal Profesional de {duracion_seg}s | Estilo: [{estilo_imaging}]")

    # Base instrumental de imaging + golpes de entrada
    pista_inst = _rms(generar_bed_estilo_imaging(estilo_imaging, "dia", dur_ms), REFERENCIA_RMS_DB)
    pista_inst = _fade(pista_inst, 50, 450)
    _sumar(pista_inst, _whoosh_aerodinamico(550, 280, 5200), 0, 0.85)
    _sumar(pista_inst, _sub_drop_cine(1100), 420, 0.90)
    _sumar(pista_inst, _kick_punch(260), 420, 0.95)

    pista_voces = _pista(dur_ms + 400)

    if duracion_seg == 3:
        # ID rápido de 3 segundos: Coro superpuesto Hombre Grave + Mujer Juvenil + Sonic Logo
        frase_id = SELECTOR.elegir("intro_3s_id", [
            "Esto es Nogaradio.",
            "Estás conectado. Nogaradio.",
            "La música no se detiene. Nogaradio.",
        ])
        v_h = sintetizar_voz_perfil(client, frase_id, "hombre_grave", audio_config)
        v_m = sintetizar_voz_perfil(client, frase_id, "mujer_juvenil", audio_config)
        coro = crear_voz_doble_coro(v_h, v_m)
        coro_fx = aplicar_fx_palabra_especifica(coro, "glitch_vocal")
        _sumar(pista_voces, coro_fx, 380.0, 1.0)
        _sumar(pista_inst, _sonic_logo_nogaradio(), 1650.0, 0.95)

    elif duracion_seg == 6:
        # Intro de 6 segundos: Alternancia rápida Mujer Juvenil -> Whoosh -> Hombre Grave -> Sonic Logo
        f1 = SELECTOR.elegir("intro_6s_f1", [
            "La música no se detiene.",
            "Estás conectado.",
            "Subí el volumen.",
            "Veinticuatro horas en movimiento.",
        ])
        f2 = SELECTOR.elegir("intro_6s_f2", [
            "Esto es Nogaradio.",
            "Escuchás Nogaradio.",
            "Siempre en tu sintonía, Nogaradio.",
        ])
        v1 = sintetizar_voz_perfil(client, f1, "mujer_juvenil", audio_config)
        v1_fx = aplicar_fx_palabra_especifica(v1, "radio_filter_band")

        v2_h = sintetizar_voz_perfil(client, f2, "hombre_grave", audio_config)
        v2_m = sintetizar_voz_perfil(client, f2, "mujer_juvenil", audio_config)
        v2_coro = aplicar_fx_palabra_especifica(crear_voz_doble_coro(v2_h, v2_m), "pitch_shift_armonia")

        _sumar(pista_voces, v1_fx, 450.0, 1.0)
        pos_v2 = min(2850.0, 450.0 + _dur_ms(v1) + 220.0)
        _sumar(pista_inst, _laser_drop(320), pos_v2 - 120.0, 0.80)
        _sumar(pista_voces, v2_coro, pos_v2, 1.0)
        _sumar(pista_inst, _sonic_logo_nogaradio(), 4400.0, 0.95)

    else:
        # Intro Power de 10 segundos: Montaje a 3 tiempos (Hombre Grave -> Mujer Juvenil -> Coro Doble)
        f1 = "La música no se detiene."
        f2 = "Estás conectado."
        f3 = "Esto es Nogaradio."

        v1 = aplicar_fx_palabra_especifica(
            sintetizar_voz_perfil(client, f1, "hombre_grave", audio_config),
            "reverse_tail_swell"
        )
        v2 = aplicar_fx_palabra_especifica(
            sintetizar_voz_perfil(client, f2, "mujer_juvenil", audio_config),
            "glitch_vocal"
        )
        v3_h = sintetizar_voz_perfil(client, f3, "hombre_grave", audio_config)
        v3_m = sintetizar_voz_perfil(client, f3, "mujer_juvenil", audio_config)
        v3_coro = aplicar_fx_palabra_especifica(crear_voz_doble_coro(v3_h, v3_m), "echo_stereo_delay")

        _sumar(pista_inst, _radio_tuning_dial(600), 0.0, 0.75)
        _sumar(pista_voces, v1, 650.0, 1.0)
        _sumar(pista_inst, _whoosh_aerodinamico(420, 400, 4800), 3100.0, 0.85)
        _sumar(pista_voces, v2, 3400.0, 1.0)
        _sumar(pista_inst, _sub_drop_cine(1000), 5800.0, 0.95)
        _sumar(pista_voces, v3_coro, 5950.0, 1.0)
        _sumar(pista_inst, _sonic_logo_nogaradio(), 8100.0, 0.98)

    pista_inst = _ducking_por_voz(pista_inst, pista_voces[: len(pista_inst)], reduccion_db=-4.5)
    mix = pista_inst * _g(-3.5) + pista_voces[: len(pista_inst)]
    return _fade(_pico(mix, 0.96), 20, 350)


# ---------------------------------------------------------------------------
# Transiciones y Cierre con Firma Sonora Nogaradio
# ---------------------------------------------------------------------------

def sintetizar_transicion() -> np.ndarray:
    estilo = SELECTOR.elegir("trans_v11", [
        "flash_news_radar",
        "double_laser_whip",
        "reverse_suction_drop",
        "tuning_stinger_hit",
    ])
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
    pista = _pista(750)
    _sumar(pista, _whoosh_aerodinamico(360, 600, 5200), 0, 0.85)
    _sumar(pista, _laser_drop(260), 280, 0.72)
    _sumar(pista, _kick_punch(240), 300, 0.95)
    _sumar(pista, _sub_drop_cine(650), 300, 0.80)
    _sumar(pista, _pop_pluck(1318.51, 350, pan=0.25), 300, 0.80)
    _sumar(pista, _pop_pluck(1760.00, 400, pan=0.75), 370, 0.85)
    return _fade(_reverb_estereo(pista, seg=0.7, mezcla=0.28)[: len(pista)], 10, 180)


def sintetizar_cierre() -> np.ndarray:
    """Cierre con Firma Sonora Nogaradio y caída sedosa."""
    pista = _pista(2900)
    _sumar(pista, _whoosh_aerodinamico(450, 350, 3400), 0, 0.70)
    _sumar(pista, _sub_drop_cine(1600), 360, 0.90)
    _sumar(pista, _kick_punch(300), 380, 0.95)
    _sumar(pista, _funky_bass(73.42, 1900), 380, 0.90)
    _sumar(pista, _sonic_logo_nogaradio(), 380, 0.95)

    out = _reverb_estereo(pista, seg=1.6, mezcla=0.40)[: len(pista)]
    return _fade(out, 15, 1200)


# ---------------------------------------------------------------------------
# Cortina de Noticias V10/V11
# ---------------------------------------------------------------------------

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
# Carga de Assets Opcionales
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


def construir_ambiente_v11(estilo_imaging: str, modo_clima: str, dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset(f"clima/{modo_clima}")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print(f"Generando cortina V11 Estilo [{estilo_imaging}] + Clima [{modo_clima}]...")
        base = generar_bed_estilo_imaging(estilo_imaging, modo_clima, dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 250, 450)


def construir_cama_noticias(dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset("noticias_cama")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print("Generando cortina V11 Breaking News...")
        base = bed_noticias_groove(dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 200, 2600)


def cargar_pista_opcional(base: str) -> np.ndarray | None:
    seg = buscar_asset(base)
    if seg is None:
        return None
    return _rms(seg_a_array(seg), REFERENCIA_RMS_DB)


# ---------------------------------------------------------------------------
# Voces Google Cloud TTS con Perfiles V11 (Hombre Grave y Mujer Juvenil)
# ---------------------------------------------------------------------------

def texto_a_ssml(texto: str, rate: str = "1.03", pitch: str = "+0.0st") -> str:
    seguro = texto.replace("&", " y ").replace("<", "").replace(">", "")
    seguro = re.sub(r'\.\s+', '. <break time="230ms"/> ', seguro)
    seguro = re.sub(r':\s+', ': <break time="170ms"/> ', seguro)
    return f"<speak><prosody rate='{rate}' pitch='{pitch}'>{seguro}</prosody></speak>"


def procesar_voz_seca(x: np.ndarray, perfil: str = "neutro") -> np.ndarray:
    """
    Procesamiento 100% SECO (sin eco ni reverb) para que los datos del clima y noticias
    se entiendan con máxima claridad, ajustando el timbre según sea voz masculina grave o femenina juvenil.
    """
    x = _filtrar(x, pasa_altos=80)
    if perfil == "hombre_grave":
        cuerpo = _filtrar(x, pasa_altos=90, pasa_bajos=220) * 0.30
    elif perfil == "mujer_juvenil":
        cuerpo = _filtrar(x, pasa_altos=130, pasa_bajos=260) * 0.16
    else:
        cuerpo = _filtrar(x, pasa_altos=110, pasa_bajos=240) * 0.20

    claridad = _filtrar(x, pasa_altos=2400, pasa_bajos=5800) * 0.32
    aire = np.tanh(_filtrar(x, pasa_altos=6500) * 1.8) * 0.11

    x = x + cuerpo + claridad + aire
    x = _comprimir(x, thr_db=-23.5, ratio=3.8, ataque_ms=3.5, relajo_ms=110.0)
    return _pico(x, 0.94)


def sintetizar_voz_perfil(client, texto: str, perfil: str, audio_config) -> np.ndarray:
    """
    Perfiles disponibles:
      - 'hombre_grave': Voz masculina profunda de Radio Imaging (Neural2-B con pitch grave).
      - 'mujer_juvenil': Voz femenina juvenil, fresca y brillante (Neural2-A con pitch ágil).
      - 'hombre_locutor': Voz masculina natural para lectura de noticias/clima.
      - 'mujer_locutora': Voz femenina natural para lectura de noticias/clima.
    """
    mapa = {
        "hombre_grave": ("es-US-Neural2-B", "0.99", "-2.2st"),
        "mujer_juvenil": ("es-US-Neural2-A", "1.05", "+1.8st"),
        "hombre_locutor": ("es-US-Neural2-B", "1.03", "-0.6st"),
        "mujer_locutora": ("es-US-Neural2-A", "1.04", "+0.8st"),
    }
    nombre_voz, rate, pitch = mapa.get(perfil, ("es-US-Neural2-A", "1.03", "+0.0st"))
    entrada = texttospeech.SynthesisInput(ssml=texto_a_ssml(texto, rate=rate, pitch=pitch))
    voz = texttospeech.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
    resp = client.synthesize_speech(input=entrada, voice=voz, audio_config=audio_config)
    seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
    return procesar_voz_seca(_recortar_silencio(seg_a_array(seg)), perfil=perfil)


# ---------------------------------------------------------------------------
# Construcción de Diálogos Interactivos y 4 Estructuras de Spot V11
# ---------------------------------------------------------------------------

def construir_bloque_clima_dialogado(
    client,
    audio_config,
    data_clima: dict | None,
    abre_mujer: bool,
) -> np.ndarray:
    """
    Crea un diálogo dinámico entre Locutor y Locutora donde se complementan y pisan
    levemente las colas (interrupción natural de radio FM) para dar la hora y el clima.
    """
    ahora = datetime.now(ZONA_HORARIA)
    hora_str = hora_en_espanol(ahora.hour, ahora.minute)
    saludo = saludo_segun_hora(ahora.hour)
    intro_clima = SELECTOR.elegir("pres_clima", BANCO_PRESENTACION_CLIMA)
    pase_not = SELECTOR.elegir("trans_loc", BANCO_TRANSICIONES_LOCUTORES)

    p1 = "mujer_locutora" if abre_mujer else "hombre_locutor"
    p2 = "hombre_locutor" if abre_mujer else "mujer_locutora"

    if not data_clima or "current" not in data_clima:
        v1 = sintetizar_voz_perfil(client, f"{saludo}. En Argentina son las {hora_str}.", p1, audio_config)
        v2 = sintetizar_voz_perfil(client, f"Seguimos juntos en Nogaradio. {pase_not}", p2, audio_config)
        return ensamblar_dialogo_interactivo([(v1, 0.0), (v2, -55.0)])

    actual = data_clima["current"]
    temp = round(actual["temperature_2m"])
    sensacion = round(actual["apparent_temperature"])
    humedad = round(actual["relative_humidity_2m"])
    viento = round(actual["wind_speed_10m"])
    descripcion = CODIGOS_CLIMA.get(actual["weather_code"], "condiciones variables")

    # Voz 1: Saludo, hora y pie del clima -> Voz 2: Interrumpe/responde con los datos exactos -> Voz 1: Pase a noticias
    texto_1 = f"{saludo}. En Argentina son las {hora_str}. {intro_clima}"
    texto_2 = f"Así es, en {CIUDAD} tenemos {descripcion}, con {temp} grados de temperatura y una sensación térmica de {sensacion}. La humedad es del {humedad} por ciento y vientos de {viento} kilómetros por hora."
    texto_3 = pase_not

    v1 = sintetizar_voz_perfil(client, texto_1, p1, audio_config)
    v2 = sintetizar_voz_perfil(client, texto_2, p2, audio_config)
    v3 = sintetizar_voz_perfil(client, texto_3, p1, audio_config)

    # Interrupción natural (-65ms en el primer pase y +90ms en el cierre del bloque)
    return ensamblar_dialogo_interactivo([(v1, 0.0), (v2, -65.0), (v3, 80.0)])


def construir_bloque_noticias_dialogado(
    client,
    audio_config,
    titular_mundo: str | None,
    titular_musica: str | None,
    abre_mujer: bool,
) -> np.ndarray:
    """
    Alterna las voces en las noticias: un locutor presenta y lee Internacionales,
    y el otro locutor entra dinámicamente con Música.
    """
    p_mundo = "hombre_locutor" if abre_mujer else "mujer_locutora"
    p_musica = "mujer_juvenil" if abre_mujer else "hombre_locutor"

    pres_m = SELECTOR.elegir("pres_not_mundo", BANCO_PRESENTACION_NOTICIAS[:10])
    pres_mus = SELECTOR.elegir("pres_not_mus", BANCO_PRESENTACION_NOTICIAS[10:])

    partes = []
    if titular_mundo:
        v_mundo = sintetizar_voz_perfil(client, f"{pres_m} {titular_mundo}", p_mundo, audio_config)
        partes.append((v_mundo, 0.0))
    if titular_musica:
        v_mus = sintetizar_voz_perfil(client, f"{pres_mus} {titular_musica}", p_musica, audio_config)
        partes.append((v_mus, 140.0 if partes else 0.0))

    if not partes:
        v_fallback = sintetizar_voz_perfil(client, "Ya estás al día con toda la actualidad en Nogaradio.", p_mundo, audio_config)
        partes.append((v_fallback, 0.0))

    return ensamblar_dialogo_interactivo(partes)


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


def generar_spot_v11(
    datos_clima: dict | None,
    titular_mundo: str | None,
    titular_musica: str | None,
    ambiente_nombre: str,
    path_salida: str,
) -> None:
    client = texttospeech.TextToSpeechClient()
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    ahora = datetime.now(ZONA_HORARIA)
    abre_mujer = (ahora.hour % 2 == 0)

    # 1. Selección sin repetición de Estilo de Imaging, Tipo/Duración de Intro y Estructura del Spot
    estilo_imaging = SELECTOR.elegir("estilo_imaging", ESTILOS_IMAGING_V11)
    modo_intro = SELECTOR.elegir("modo_intro", ["vocal_3s", "vocal_6s", "vocal_10s", "instrumental_v10"])
    estructura = SELECTOR.elegir("estructura_spot", [
        "estructura_1_clasica_duo",
        "estructura_2_musical_delante",
        "estructura_3_noticias_primero",
        "estructura_4_show_interactivo",
    ])
    print(f"Configuración V11 -> Estructura: [{estructura}] | Intro: [{modo_intro}] | Imaging: [{estilo_imaging}]")

    # 2. Generación de la Intro (Vocal de 3s/6s/10s o Instrumental V10 con Tag)
    if modo_intro == "vocal_3s":
        intro = generar_intro_vocal_profesional(client, audio_config, 3, estilo_imaging)
        usa_tag_extra = False
    elif modo_intro == "vocal_6s":
        intro = generar_intro_vocal_profesional(client, audio_config, 6, estilo_imaging)
        usa_tag_extra = False
    elif modo_intro == "vocal_10s":
        intro = generar_intro_vocal_profesional(client, audio_config, 10, estilo_imaging)
        usa_tag_extra = False
    else:
        intro = cargar_efecto("intro", sintetizar_intro_instrumental_v10)
        usa_tag_extra = True

    # 3. Generación de los bloques vocales según la estructura sorteada
    frase_id = SELECTOR.elegir("id_frase", BANCO_IDENTIFICACION)
    frase_mus = SELECTOR.elegir("mus_frase", BANCO_FRASES_MUSICALES)
    frase_app = SELECTOR.elegir("app_frase", BANCO_PROMOS_APP)
    frase_remate = SELECTOR.elegir("remate_frase", BANCO_REMATES_FINALES)

    voz_clima_duo = construir_bloque_clima_dialogado(client, audio_config, datos_clima, abre_mujer)
    voz_noticias_duo = construir_bloque_noticias_dialogado(client, audio_config, titular_mundo, titular_musica, abre_mujer)

    # Segmento específico con FX (Frase musical o Promo App con efecto en palabras clave)
    fx_intermedio = SELECTOR.elegir("fx_palabra_media", ["pitch_shift_armonia", "radio_filter_band", "echo_stereo_delay", "glitch_vocal"])
    voz_mus_fx = aplicar_fx_palabra_especifica(
        sintetizar_voz_perfil(client, frase_mus, "mujer_juvenil" if abre_mujer else "hombre_grave", audio_config),
        fx_intermedio
    )
    voz_app_fx = aplicar_fx_palabra_especifica(
        sintetizar_voz_perfil(client, frase_app, "hombre_grave" if abre_mujer else "mujer_juvenil", audio_config),
        "echo_stereo_delay"
    )

    # Remate final (alternando entre Coro Doble H+M o Voz solista con los 5 efectos limpios de V10)
    if random.random() < 0.55:
        rem_h = sintetizar_voz_perfil(client, frase_remate, "hombre_grave", audio_config)
        rem_m = sintetizar_voz_perfil(client, frase_remate, "mujer_juvenil", audio_config)
        remate_base = crear_voz_doble_coro(rem_h, rem_m)
    else:
        remate_base = sintetizar_voz_perfil(client, frase_remate, "mujer_juvenil" if not abre_mujer else "hombre_grave", audio_config)
    remate_final_fx, nombre_fx_fin = procesar_remate_final_fm(remate_base)
    print(f"FX Remate Final: [{nombre_fx_fin}]")

    # Armado de los dos grandes bloques del spot según la estructura elegida
    if estructura == "estructura_1_clasica_duo":
        # Clima Dúo -> Gran Transición -> Noticias Dúo + Promo App -> Whip -> Remate
        if usa_tag_extra:
            tag_arr, _ = procesar_tag_inicio_fm(sintetizar_voz_perfil(client, frase_id, "mujer_juvenil" if abre_mujer else "hombre_grave", audio_config))
            bloque_1 = ensamblar_dialogo_interactivo([(tag_arr, 0.0), (voz_clima_duo, 120.0)])
        else:
            bloque_1 = voz_clima_duo
        bloque_2 = ensamblar_dialogo_interactivo([(voz_noticias_duo, 0.0), (voz_app_fx, 180.0)])

    elif estructura == "estructura_2_musical_delante":
        # Hook Musical con FX + Clima Dúo -> Gran Transición -> Noticias Dúo -> Whip -> Remate
        bloque_1 = ensamblar_dialogo_interactivo([(voz_mus_fx, 0.0), (voz_clima_duo, 100.0)])
        bloque_2 = voz_noticias_duo

    elif estructura == "estructura_3_noticias_primero":
        # Arranca directo con el Flash de Noticias -> Gran Transición -> Clima Dúo + Promo App -> Whip -> Remate
        bloque_1 = voz_noticias_duo
        bloque_2 = ensamblar_dialogo_interactivo([(voz_clima_duo, 0.0), (voz_app_fx, 150.0)])

    else:
        # Show Interactivo: Clima Dúo -> Gran Transición -> Noticias Dúo -> Hook Musical + App -> Whip -> Remate
        bloque_1 = voz_clima_duo
        bloque_2 = ensamblar_dialogo_interactivo([(voz_noticias_duo, 0.0), (voz_mus_fx, 140.0), (voz_app_fx, 100.0)])

    trans = cargar_efecto("transicion", sintetizar_transicion)
    whip_duo = sintetizar_whip_pase_voces()
    cierre = cargar_efecto("cierre", sintetizar_cierre)

    # Cálculo de tiempos de ensamblado sin micro-cortes
    inicio_b1 = _dur_ms(intro) - 750.0 if usa_tag_extra else _dur_ms(intro) - 350.0
    inicio_b1 = max(900.0, inicio_b1)
    fin_b1 = inicio_b1 + _dur_ms(bloque_1)

    inicio_trans = fin_b1 + 120.0
    inicio_b2 = inicio_trans + 1450.0
    fin_b2 = inicio_b2 + _dur_ms(bloque_2)

    inicio_whip = fin_b2 - 60.0
    inicio_remate = fin_b2 + 360.0
    fin_remate_seco = inicio_remate + _dur_ms(remate_base)
    fin_cola_remate = inicio_remate + _dur_ms(remate_final_fx)

    inicio_cierre = fin_remate_seco + 100.0
    total_ms = max(inicio_cierre + _dur_ms(cierre), fin_cola_remate + 1400.0)

    # 1. Bus de Voces
    bus_voces = _pista(total_ms + 800)
    _sumar(bus_voces, bloque_1, inicio_b1, 1.0)
    _sumar(bus_voces, bloque_2, inicio_b2, 1.0)
    _sumar(bus_voces, remate_final_fx, inicio_remate, 1.0)

    # 2. Bus de Cortinas (Estilo Imaging V11 + Cortina Informativa)
    ambiente = construir_ambiente_v11(estilo_imaging, ambiente_nombre, (inicio_trans - inicio_b1) + 1200.0)
    cama_noticias = construir_cama_noticias((total_ms - inicio_trans) + 1500.0)
    cama_ext = cargar_pista_opcional("cama")

    bus_cortinas = _pista(total_ms + 800)
    dur_tramo_1 = int(SR * (inicio_trans + 150.0 - inicio_b1) / 1000)
    if dur_tramo_1 > 0:
        amb_rec = _fade(ambiente[: min(len(ambiente), dur_tramo_1)], 250, 350)
        _sumar(bus_cortinas, amb_rec, inicio_b1, _g(AMBIENTE_DB))

    _sumar(bus_cortinas, cama_noticias, inicio_trans + 520.0, _g(TICKER_NOTICIAS_DB))
    if cama_ext is not None:
        _sumar(bus_cortinas, _extender(cama_ext, len(bus_cortinas)), 0.0, _g(CAMA_DB_LIBRE - 3.0))

    bus_cortinas = _ducking_por_voz(bus_cortinas, bus_voces, reduccion_db=-3.5)

    # Fade-out progresivo lento de 3 segundos al final (salida limpia de V10)
    n_total = int(SR * total_ms / 1000)
    ini_fade_cama = max(0, int(SR * (fin_remate_seco + 150.0) / 1000))
    if n_total > ini_fade_cama:
        curva_cama = np.linspace(1.0, 0.0, n_total - ini_fade_cama) ** 1.5
        bus_cortinas[ini_fade_cama:n_total] *= curva_cama[:, None]
        bus_cortinas[n_total:] = 0.0

    # 3. Bus de Sweepers e Intros
    bus_fx = _pista(total_ms + 800)
    _sumar(bus_fx, intro, 0.0, _g(EFECTOS_DB))
    _sumar(bus_fx, trans, inicio_trans, _g(EFECTOS_DB))
    _sumar(bus_fx, whip_duo, inicio_whip, _g(EFECTOS_DB - 1.0))
    _sumar(bus_fx, cierre, inicio_cierre, _g(EFECTOS_DB - 1.5))

    mezcla = (bus_voces + bus_cortinas + bus_fx)[:n_total]
    final = masterizar(_fade(mezcla, 15, 950))
    SELECTOR.guardar()

    array_a_seg(final).export(path_salida, format="mp3", bitrate="192k")
    print(f"¡Spot V11 Professional Radio Imaging ({len(final) / SR:.1f} s) guardado exitosamente en {path_salida}!")


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
    print("=== EJECUTANDO NOGARADIO VERSION V11 PROFESSIONAL RADIO IMAGING ===")
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
    ambiente_nombre = elegir_ambiente(datos_clima)

    print("Consultando y verificando noticias...")
    titular_mundo = obtener_titular_verificado(FEEDS_MUNDO, "mundo", 20)
    titular_musica = obtener_titular_verificado(FEEDS_MUSICA, "musica", 20)

    print("Generando spot con motor V11 Professional Radio Imaging...")
    generar_spot_v11(datos_clima, titular_mundo, titular_musica, ambiente_nombre, LOCAL_MP3)

    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
