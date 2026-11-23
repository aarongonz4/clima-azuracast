#!/usr/bin/env python3
"""
Spot automático de clima, noticias e imaging para AzuraCast (Nogaradio)
VERSION V14 PROFESSIONAL RADIO IMAGING + EDITOR DE VOZ POR PALABRAS.

Mejoras de V14 sobre V13:
  1) EDITOR DE VOZ PROFESIONAL (edicion_voz_v14.py):
     - Corte por palabras con timepoints de Google Cloud TTS v1beta1 (SSML_MARK),
       con fallback por frases y fallback transparente a V13.
     - Detección de palabras clave (MARCA, PROMO, ENERGIA, DATO_CRITICO) con regla
       estricta de inteligibilidad en clima y noticias.
     - Partitura de efectos planificada por intensidad, cuantizada al BPM del estilo
       (entrada en beat) y aplicada con crossfades de 5-10 ms + ducking en colas.
     - Activado por defecto con EDITOR_VOZ_V14="1" (poner "0" para modo V13 clásico).
  2) CLIMA PRECISO EN RESISTENCIA (ESTACIÓN OFICIAL METAR SARE + OPEN-METEO):
     - Consulta en tiempo real la estación oficial del Aeropuerto de Resistencia (SARE)
       para temperatura, humedad real (Magnus), viento real y sensación térmica, con
       respaldo y cruce automático contra Open-Meteo.
  3) OPTIMIZACIÓN DE VELOCIDAD:
     - Síntesis de cortinas por ciclo armónico + caché de respuestas de reverb y caché TTS
       en memoria para reducir drásticamente el tiempo en GitHub Actions.
  4) MODO PRUEBA LOCAL:
     - Permite probar sin subir por SFTP pasando `--local` o `DRY_RUN_LOCAL=1`.
"""

import glob
import io
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import feedparser
import numpy as np
import paramiko
import requests
from google.cloud import texttospeech
from pydub import AudioSegment

try:
    import edicion_voz_v14 as editor_v14
except Exception as _err_v14:
    editor_v14 = None
    print(f"Aviso: No se pudo cargar edicion_voz_v14 ({_err_v14}). Se usará modo V13.", file=sys.stderr)

# ---------------------------------------------------------------------------
# Configuración General y Zona Horaria UTC-3 Garantizada
# ---------------------------------------------------------------------------

CIUDAD = "Resistencia"
LAT = -27.4514
LON = -58.9867
ESTACION_METAR_ICAO = "SARE"  # Aeropuerto Internacional de Resistencia (SMN)

try:
    ZONA_HORARIA = ZoneInfo("America/Argentina/Buenos_Aires")
except Exception:
    ZONA_HORARIA = timezone(timedelta(hours=-3))

LOCAL_MP3 = "clima.mp3"
REMOTE_FILENAME = "clima.mp3"
HISTORIAL_JSON = ".nogaradio_v14_history.json"
EDITOR_VOZ_V14 = os.environ.get("EDITOR_VOZ_V14", "1").strip() == "1"

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
EFECTOS_DB = -0.8
AMBIENTE_DB = -8.5
TICKER_NOTICIAS_DB = -8.0
CAMA_DB_LIBRE = -4.5
CAMA_DB_VOZ = -10.5

# Caché en memoria de respuestas de impulso de reverb para acelerar el renderizado
_CACHE_REVERB_IR: dict[tuple[float, float], np.ndarray] = {}

# ---------------------------------------------------------------------------
# Motor Anti-Repetición y Rotación Inteligente
# ---------------------------------------------------------------------------

class SelectorRotativoV13:
    """Evita repeticiones consecutivas de frases, intensidades, estilos y estructuras."""

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

        elegido = random.choice(disponibles)
        self.usados_en_sesion[categoria].add(elegido)
        self.ultimo_estado[categoria] = elegido
        return elegido


SELECTOR = SelectorRotativoV13(HISTORIAL_JSON)

# ---------------------------------------------------------------------------
# Motor de Contexto Horario Argentino (5 Franjas)
# ---------------------------------------------------------------------------

PALABRAS_PROHIBIDAS_DIA = (
    "estrella", "estrellas", "nocturna", "nocturno", "noche", "noches",
    "oscuridad", "madrugada", "luna", "dormir", "trasnoche", "desvelado",
)

PALABRAS_PROHIBIDAS_NOCHE = (
    "amanecer", "despertar", "mañana", "mediodía", "tarde", "atardecer",
    "almuerzo", "siesta", "sol brillante",
)


def ahora_argentina() -> datetime:
    dt_utc = datetime.now(timezone.utc)
    return dt_utc.astimezone(ZONA_HORARIA)


def obtener_contexto_horario_argentina(dt: datetime | None = None) -> str:
    if dt is None:
        dt = ahora_argentina()
    minutos_dia = dt.hour * 60 + dt.minute

    if 360 <= minutos_dia < 720:
        return "manana"
    elif 720 <= minutos_dia < 870:
        return "mediodia"
    elif 870 <= minutos_dia < 1110:
        return "tarde"
    elif 1110 <= minutos_dia < 1230:
        return "atardecer"
    return "noche"


def validar_coherencia_horaria(frases: list[str], franja: str) -> list[str]:
    filtradas = []
    for f in frases:
        low = f.lower()
        if franja != "noche":
            if any(p in low for p in PALABRAS_PROHIBIDAS_DIA):
                continue
        else:
            if any(p in low for p in PALABRAS_PROHIBIDAS_NOCHE):
                continue
        filtradas.append(f)
    return filtradas if filtradas else frases


# ---------------------------------------------------------------------------
# BANCO DE FRASES CONTEXTUALES AMPLIADO V14
# ---------------------------------------------------------------------------

BANCO_FRASES_ENTRADA_IMPACTO = [
    "¡Llegó el momento!",
    "¡Ya estamos en el aire!",
    "¡Subí el volumen ahora!",
    "¡Sentí toda la energía!",
    "¡Empezamos una nueva hora!",
    "¡Conectate con tu música!",
    "¡Prendete a la sintonía!",
    "¡Viví el mejor sonido!",
    "¡Activamos tu radio!",
    "¡La música suena fuerte!",
    "¡Arranca una nueva hora en vivo!",
    "¡Ponete cómodo, sube el ritmo!",
]

BANCO_PROTAGONISMO_NOGARADIO = [
    "Esto es Nogaradio.",
    "Escuchás Nogaradio.",
    "Estás en Nogaradio.",
    "Suena Nogaradio, tu radio.",
    "Conectados en Nogaradio.",
    "Nogaradio, la música no se detiene.",
    "Siempre en tu sintonía, Nogaradio.",
    "Pura potencia musical, Nogaradio.",
    "Nogaradio, en vivo las veinticuatro horas.",
    "Tu compañía de todos los días, Nogaradio.",
]

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

SALUDOS_POR_FRANJA = {
    "manana": [
        "Hola, muy buenos días, qué lindo despertar y empezar el día con vos",
        "Buenos días, arriba esa mañana, arrancamos la jornada con toda la energía",
        "Excelente mañana para vos, te acompañamos desde temprano con buena música",
        "Hola, buen día, comenzamos un nuevo día conectados en la radio",
        "Muy buenos días, poniéndole actitud y buen sonido a tu mañana",
        "Buen día, ya estamos en vivo para ponerle ritmo a tu mañana",
        "Hola, qué bueno arrancar la mañana juntos en el aire de Nogaradio",
    ],
    "mediodia": [
        "Hola, muy buen mediodía, te acompañamos con toda la energía a esta hora",
        "Buenas tardes, llegamos a la mitad de la jornada haciéndote la mejor compañía",
        "Excelente mediodía para vos, seguimos en movimiento con buena música",
        "Hola, qué bueno que estés ahí en pleno mediodía, compartiendo la radio",
        "Muy buen mediodía, le ponemos ritmo y buena onda a tu actividad de hoy",
        "Hola, excelente mediodía, la mejor música acompaña tu pausa de hoy",
    ],
    "tarde": [
        "Hola, muy buenas tardes, seguimos poniéndole ritmo y entretenimiento a tu día",
        "Buenas tardes, qué lindo que estés del otro lado compartiendo esta tarde con nosotros",
        "Excelente tarde para vos, la mejor compañía musical para este momento del día",
        "Hola, buenas tardes, seguimos haciéndote el aguante con todos los hits",
        "Muy buenas tardes, conectados a pleno ritmo en el aire de la radio",
        "Buenas tardes, subí el volumen que seguimos en vivo acompañando tu tarde",
        "Hola, excelente tarde para vos, qué lindo tenerte del otro lado",
    ],
    "atardecer": [
        "Hola, muy buenas tardes, acompañándote mientras empieza a caer el sol",
        "Buenas tardes, vivimos juntos este atardecer con la mejor selección musical",
        "Qué lindo compartir el cierre de la tarde con vos, bajando un cambio con buena música",
        "Hola, buenas tardes, te acompañamos en el regreso a casa y en esta transición del día",
        "Excelente atardecer para vos, el ambiente perfecto suena ahora en tu sintonía",
        "Buenas tardes, el mejor sonido te acompaña mientras termina la jornada",
    ],
    "noche": [
        "Hola, muy buenas noches, gracias por dejarnos acompañarte en esta noche",
        "Buenas noches, la mejor música nocturna suena ahora para vos",
        "Excelente noche para vos, conectados bajo las estrellas con el mejor sonido",
        "Hola, buenas noches, cerramos el día juntos con una selección especial",
        "Muy buenas noches, seguimos despiertos y en vivo haciéndote compañía",
        "Buenas noches, la música sigue encendida para acompañarte a esta hora",
    ],
}

FRASES_MUSICALES_POR_FRANJA = {
    "manana": [
        "La música ideal para despertar y activar tu mañana suena acá.",
        "Empezá el día bien arriba con las canciones que más te gustan.",
        "Todos los hits para ponerle energía a tu mañana sin pausas.",
        "Subí el volumen, que la mañana se vive con mucha música.",
        "El ritmo que necesitás para arrancar el día con todo.",
        "Más hits y más energía para empezar la mañana en vivo.",
    ],
    "mediodia": [
        "Pura energía musical para acompañarte en la mitad del día.",
        "La música no se detiene y le pone ritmo a tu mediodía.",
        "Canciones bien arriba para mantener la buena onda en tu jornada.",
        "Los éxitos que suenan fuerte para acompañarte a esta hora.",
        "Conectá con la mejor música mientras seguís en actividad.",
        "Ritmo sin pausas para recargar energía este mediodía.",
    ],
    "tarde": [
        "El mejor entretenimiento y todas las canciones que mueven tu tarde.",
        "Subí el volumen, que la tarde suena increíble en tu radio.",
        "Una canción tras otra para hacerte la mejor compañía esta tarde.",
        "Los artistas que marcan tendencia le ponen ritmo a tu tarde.",
        "Sentí el bajo, sentí el ritmo, la música sigue sonando toda la tarde.",
        "Todos los hits del momento suenan en vivo para vos esta tarde.",
    ],
    "atardecer": [
        "El sonido perfecto para acompañar la caída de la tarde.",
        "Buena música y el mejor ambiente mientras termina la jornada.",
        "Canciones que te acompañan en la vuelta a casa con la mejor onda.",
        "Bajamos las revoluciones pero subimos la calidad musical en este atardecer.",
        "La banda sonora ideal para este momento del atardecer.",
        "Música elegida para disfrutar la transición del día con el mejor ritmo.",
    ],
    "noche": [
        "La música nocturna que te acompaña en cada rincón.",
        "Sonidos envolventes y los mejores temas para disfrutar la noche.",
        "En la tranquilidad de la noche, la música suena más cerca tuyo.",
        "Una selección pensada especialmente para tus horas nocturnas.",
        "Los grandes éxitos siguen girando durante toda la noche.",
        "Ritmo y buena compañía en vivo para tus horas nocturnas.",
    ],
}

REMATES_POR_FRANJA = {
    "manana": [
        "Arriba esta mañana. En Nogaradio, seguimos con vos.",
        "Empezá bien el día con nosotros. Escuchás Nogaradio, sonando para vos.",
        "Toda la energía para tu mañana. Esto es Nogaradio, siempre con vos.",
        "Seguí con esta programación matutina. En Nogaradio, conectados con vos.",
        "La música no se detiene. Estás conectado a Nogaradio.",
        "Cada mañana suena mejor acá. En Nogaradio, siempre junto a vos.",
    ],
    "mediodia": [
        "Pura energía en tu día. En Nogaradio, seguimos con vos.",
        "Acompañando cada momento de tu jornada. Escuchás Nogaradio, sonando para vos.",
        "Seguí con esta programación. Esto es Nogaradio, conectados con vos.",
        "Más ritmo y buena música. En Nogaradio, estamos siempre con vos.",
        "La música no se detiene. Estás conectado a Nogaradio.",
        "Tu mediodía tiene el mejor ritmo. Esto es Nogaradio.",
    ],
    "tarde": [
        "Poniéndole ritmo a tu tarde. En Nogaradio, seguimos con vos.",
        "La mejor compañía para esta tarde. Escuchás Nogaradio, sonando para vos.",
        "Seguí con esta programación toda la tarde. Esto es Nogaradio, siempre con vos.",
        "Descargá la app en Play Store y Apple Store. En Nogaradio, conectados con vos.",
        "Más hits, más energía esta tarde. Esto es Nogaradio, siempre junto a vos.",
        "Subí el volumen y quedate acá. En Nogaradio, sonando para vos.",
        "La tarde suena más fuerte en Nogaradio, siempre con vos.",
    ],
    "atardecer": [
        "Acompañando tu atardecer con buena música. En Nogaradio, seguimos con vos.",
        "El mejor ambiente para esta hora. Escuchás Nogaradio, sonando para vos.",
        "Seguí con esta programación al caer la tarde. Esto es Nogaradio, siempre con vos.",
        "En tu casa o en el camino. Nogaradio, conectados con vos.",
        "Viví la música en Nogaradio, siempre junto a vos.",
        "El cierre perfecto de la tarde suena en Nogaradio.",
    ],
    "noche": [
        "La mejor compañía para tu noche. En Nogaradio, seguimos con vos.",
        "Sonando en vivo toda la noche. Escuchás Nogaradio, siempre con vos.",
        "Seguí con nuestra programación nocturna. En Nogaradio, conectados con vos.",
        "Música sin pausas en tu noche. Esto es Nogaradio, sonando para vos.",
        "En todas partes y en tu celular. Nogaradio, siempre junto a vos.",
        "Buenas canciones para cerrar el día. Esto es Nogaradio.",
    ],
}

BANCO_PRESENTACION_CLIMA = [
    "Repasamos al instante cómo está el tiempo en la ciudad.",
    "Salimos afuera y miramos los datos actuales del clima.",
    "Te contamos cómo se comporta el tiempo a esta hora.",
    "Actualizamos el reporte meteorológico para que estés al tanto.",
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
    "Consultamos los datos en tiempo real del tiempo en nuestra ciudad.",
    "Veamos qué marca el termómetro ahora mismo en la región.",
]

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
    "Seguimos en vivo, momento de repasar las noticias del día.",
]

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


def saludo_segun_franja(franja: str) -> str:
    candidatos = validar_coherencia_horaria(
        SALUDOS_POR_FRANJA.get(franja, SALUDOS_POR_FRANJA["tarde"]),
        franja,
    )
    return SELECTOR.elegir(f"saludo_{franja}", candidatos)


def frase_musical_segun_franja(franja: str) -> str:
    candidatos = validar_coherencia_horaria(
        FRASES_MUSICALES_POR_FRANJA.get(franja, FRASES_MUSICALES_POR_FRANJA["tarde"]),
        franja,
    )
    return SELECTOR.elegir(f"mus_{franja}", candidatos)


def remate_segun_franja(franja: str) -> str:
    candidatos = validar_coherencia_horaria(
        REMATES_POR_FRANJA.get(franja, REMATES_POR_FRANJA["tarde"]),
        franja,
    )
    return SELECTOR.elegir(f"remate_{franja}", candidatos)


# ---------------------------------------------------------------------------
# Obtención de Clima PRECISO (METAR Oficial SARE Resistencia + Open-Meteo)
# ---------------------------------------------------------------------------

def _calcular_humedad_magnus(temp_c: float, dewpoint_c: float) -> float:
    """Calcula humedad relativa (%) exacta desde temperatura y punto de rocío."""
    a, b = 17.625, 243.04
    num = math.exp((a * dewpoint_c) / (b + dewpoint_c))
    den = math.exp((a * temp_c) / (b + temp_c))
    rh = 100.0 * (num / den)
    return max(5.0, min(100.0, rh))


def _calcular_sensacion_termica(temp_c: float, rh: float, viento_kmh: float) -> float:
    """Calcula sensación térmica real (Heat Index para calor, Wind Chill para frío)."""
    if temp_c >= 26.0 and rh >= 40.0:
        t_f = temp_c * 9.0 / 5.0 + 32.0
        hi_f = (
            -42.379 + 2.04901523 * t_f + 10.14333127 * rh
            - 0.22475541 * t_f * rh - 0.00683783 * t_f * t_f
            - 0.05481717 * rh * rh + 0.00122874 * t_f * t_f * rh
            + 0.00085282 * t_f * rh * rh - 0.00000199 * t_f * t_f * rh * rh
        )
        return (hi_f - 32.0) * 5.0 / 9.0
    if temp_c <= 10.0 and viento_kmh > 4.8:
        return (
            13.12 + 0.6215 * temp_c
            - 11.37 * (viento_kmh ** 0.16)
            + 0.3965 * temp_c * (viento_kmh ** 0.16)
        )
    return temp_c


def _mapear_metar_a_wmo(wx_string: str, cover: str) -> int:
    """Convierte código METAR (wxString + cobertura nubosa) a código WMO estándar."""
    wx = (wx_string or "").upper()
    cov = (cover or "").upper()
    if "TS" in wx:
        return 96 if "GR" in wx else 95
    if "SHRA" in wx:
        return 82 if "+SHRA" in wx else (80 if "-SHRA" in wx else 81)
    if "RA" in wx:
        return 65 if "+RA" in wx else (61 if "-RA" in wx else 63)
    if "DZ" in wx:
        return 55 if "+DZ" in wx else (51 if "-DZ" in wx else 53)
    if "FG" in wx or "BR" in wx:
        return 45
    if cov in ("OVC", "BKN"):
        return 3
    if cov == "SCT":
        return 2
    if cov == "FEW":
        return 1
    return 0


def _obtener_metar_resistencia() -> dict | None:
    """
    Consulta datos reales medidos por la estación meteorológica oficial del
    Aeropuerto Internacional de Resistencia (SARE) vía AviationWeather (NOAA/ICAO).
    """
    url = f"https://aviationweather.gov/api/data/metar?ids={ESTACION_METAR_ICAO}&format=json"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "NogaradioWeatherBot/14.0", "Cache-Control": "no-cache"},
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json()
        if not isinstance(items, list) or not items:
            return None

        metar = items[0]
        temp = metar.get("temp")
        dewp = metar.get("dewp")
        if temp is None or dewp is None:
            return None

        # Verificamos que la medición no tenga más de 2.5 horas de antigüedad
        obs_time = metar.get("obsTime")
        if obs_time and (time.time() - float(obs_time)) > 9000:
            return None

        temp_c = float(temp)
        dewp_c = float(dewp)
        wspd_kt = float(metar.get("wspd") or 0.0)
        viento_kmh = round(wspd_kt * 1.852, 1)
        humedad = round(_calcular_humedad_magnus(temp_c, dewp_c), 1)
        sensacion = round(_calcular_sensacion_termica(temp_c, humedad, viento_kmh), 1)

        wx_str = str(metar.get("wxString") or "")
        cover = str(metar.get("cover") or "")
        if not cover and isinstance(metar.get("clouds"), list) and metar["clouds"]:
            cover = str(metar["clouds"][-1].get("cover") or "")
        wmo = _mapear_metar_a_wmo(wx_str, cover)

        print(
            f"Clima REAL Estación {ESTACION_METAR_ICAO} (Resistencia): "
            f"{temp_c:.1f}°C | ST {sensacion:.1f}°C | Hum {humedad:.0f}% | "
            f"Viento {viento_kmh:.0f} km/h | WMO {wmo} ({CODIGOS_CLIMA.get(wmo)})"
        )
        return {
            "source": f"METAR-{ESTACION_METAR_ICAO}",
            "current": {
                "temperature_2m": temp_c,
                "apparent_temperature": sensacion,
                "relative_humidity_2m": humedad,
                "wind_speed_10m": viento_kmh,
                "weather_code": wmo,
            },
        }
    except Exception as e:
        print(f"Aviso: Estación METAR {ESTACION_METAR_ICAO} no disponible ({e}).", file=sys.stderr)
        return None


def _obtener_open_meteo(reintentos: int = 3, espera: int = 3) -> dict | None:
    """Consulta Open-Meteo con modelo GFS/BestMatch y parámetro anti-caché."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
        "timezone": "America/Argentina/Buenos_Aires",
        "models": "best_match",
    }
    for intento in range(1, reintentos + 1):
        try:
            resp = requests.get(
                url,
                params=params,
                headers={"Cache-Control": "no-cache"},
                timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()
            if "current" in data:
                c = data["current"]
                print(
                    f"Clima Open-Meteo (Resistencia): {c.get('temperature_2m')}°C | "
                    f"ST {c.get('apparent_temperature')}°C | Hum {c.get('relative_humidity_2m')}% | "
                    f"WMO {c.get('weather_code')}"
                )
                return data
        except Exception as e:
            print(f"Intento {intento}/{reintentos} falló al consultar Open-Meteo: {e}", file=sys.stderr)
            if intento < reintentos:
                time.sleep(espera)
    return None


def obtener_clima(reintentos: int = 3, espera: int = 3) -> dict | None:
    """
    Obtiene el clima más preciso posible para Resistencia:
      1) Prioriza la estación meteorológica oficial del Aeropuerto de Resistencia (METAR SARE).
      2) Si falla o no responde, usa Open-Meteo con reintentos.
    """
    datos_metar = _obtener_metar_resistencia()
    if datos_metar is not None:
        return datos_metar
    return _obtener_open_meteo(reintentos=reintentos, espera=espera)


def elegir_ambiente(data: dict | None, franja: str) -> str:
    es_dia = franja in ("manana", "mediodia", "tarde", "atardecer")

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
# Motor DSP Base y Carga de Assets
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
    return np.arange(max(1, int(SR * dur_ms / 1000))) / SR


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
    clave_ir = (round(seg, 2), round(brillo, 0))
    ir = _CACHE_REVERB_IR.get(clave_ir)
    if ir is None:
        ir_n = int(SR * seg)
        t = np.arange(ir_n) / SR
        rng = np.random.default_rng(99)
        ir = rng.standard_normal((ir_n, 2)) * np.exp(-6.0 * t / seg)[:, None]
        ir = _filtrar(ir, pasa_altos=180, pasa_bajos=brillo)
        predelay = int(SR * 0.022)
        ir = np.pad(ir, ((predelay, 0), (0, 0)))[:ir_n]
        ir /= (np.sqrt(np.sum(ir ** 2, axis=0, keepdims=True)) + 1e-9)
        _CACHE_REVERB_IR[clave_ir] = ir

    ir_n = len(ir)
    largo = x.shape[0] + ir_n
    nfft = 1 << (largo - 1).bit_length()
    humedo = np.fft.irfft(np.fft.rfft(x, nfft, axis=0) * np.fft.rfft(ir, nfft, axis=0), nfft, axis=0)[:largo]
    seco = np.pad(x, ((0, ir_n), (0, 0)))
    return seco + humedo * mezcla


def buscar_asset(base: str) -> AudioSegment | None:
    candidatos = []
    for ext in EXTENSIONES:
        candidatos += glob.glob(os.path.join(ASSETS_DIR, f"{base}*{ext}"))
    if not candidatos:
        return None
    ruta = random.choice(sorted(candidatos))
    print(f"Usando archivo propio de assets: {ruta}")
    return AudioSegment.from_file(ruta)


# ---------------------------------------------------------------------------
# CAPAS DE DISEÑO SONORO (Sweeps, Risers, Impacts, Bass Drops, Whooshes, Stingers, Texturas)
# ---------------------------------------------------------------------------

class MotorCapasSoundDesign:
    @staticmethod
    def _cargar_o_sintetizar(nombre_fx: str, fn_sintesis) -> np.ndarray:
        seg = buscar_asset(f"fx/{nombre_fx}")
        if seg is not None:
            return _pico(seg_a_array(seg), 0.92)
        return fn_sintesis()

    @classmethod
    def sweep(cls, dur_ms: float = 950.0, ascendente: bool = True) -> np.ndarray:
        def _sint():
            t = _t(dur_ms)
            prog = t / (dur_ms / 1000.0)
            p_curva = prog if ascendente else (1.0 - prog)
            f_bar = 180.0 * ((26.0) ** (p_curva ** 1.7))
            fase = 2 * np.pi * np.cumsum(f_bar) / SR
            res = np.sin(fase) * 0.30 + np.sin(fase * 1.5) * 0.15
            ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=250, pasa_bajos=7800)
            env = np.sin(np.pi * (prog ** 1.2)) ** 2
            pan_l = 0.15 + 0.70 * p_curva
            pan_r = 0.85 - 0.70 * p_curva
            out = (ruido * 0.55 + np.column_stack((res * pan_l, res * pan_r))) * env[:, None]
            return _fade(_pico(out, 0.88), 15, 40)
        return cls._cargar_o_sintetizar("sweep", _sint)

    @classmethod
    def riser(cls, dur_ms: float = 1100.0) -> np.ndarray:
        def _sint():
            t = _t(dur_ms)
            prog = t / (dur_ms / 1000.0)
            f1 = 220.0 * (2.0 ** (2.5 * (prog ** 2.0)))
            f2 = 330.0 * (2.0 ** (2.5 * (prog ** 2.0)))
            tono_l = np.sin(2 * np.pi * np.cumsum(f1) / SR)
            tono_r = np.sin(2 * np.pi * np.cumsum(f2) / SR)
            ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=600, pasa_bajos=9500)
            pulso = 0.65 + 0.35 * np.sin(2 * np.pi * (6.0 + 18.0 * prog) * t)
            env = (prog ** 2.3) * pulso
            out = (np.column_stack((tono_l, tono_r)) * 0.45 + ruido * 0.55) * env[:, None]
            return _fade(_pico(out, 0.90), 20, 12)
        return cls._cargar_o_sintetizar("riser", _sint)

    @classmethod
    def impact(cls, dur_ms: float = 1350.0) -> np.ndarray:
        def _sint():
            t = _t(dur_ms)
            f_golpe = 38.0 + 160.0 * np.exp(-t * 38.0)
            cuerpo = np.tanh(np.sin(2 * np.pi * np.cumsum(f_golpe) / SR) * 2.2) * np.exp(-t * 4.2)
            crac = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=400, pasa_bajos=6800)
            crac *= np.exp(-t * 14.0)[:, None] * 0.65
            metal = (np.sin(2 * np.pi * 523.25 * t) + np.sin(2 * np.pi * 783.99 * t)) * np.exp(-t * 11.0) * 0.22
            bruto = _est(cuerpo * 0.90 + metal) + crac
            rev = _reverb_estereo(bruto, seg=1.25, mezcla=0.45, brillo=4800)[: len(t)]
            return _fade(_pico(rev, 0.94), 2, 180)
        return cls._cargar_o_sintetizar("impact", _sint)

    @classmethod
    def bass_drop(cls, dur_ms: float = 1250.0) -> np.ndarray:
        def _sint():
            t = _t(dur_ms)
            f_curva = 28.0 + 72.0 * np.exp(-t * 6.2)
            fase = 2 * np.pi * np.cumsum(f_curva) / SR
            sub = np.tanh(np.sin(fase) * 1.9) * np.exp(-t * 2.3)
            click = np.sin(2 * np.pi * 190.0 * t) * np.exp(-t * 55.0) * 0.45
            return _fade(_est((sub * 0.90 + click) * 0.92), 3, 150)
        return cls._cargar_o_sintetizar("bass_drop", _sint)

    @classmethod
    def whoosh(cls, dur_ms: float = 520.0, izq_a_der: bool = True) -> np.ndarray:
        def _sint():
            t = _t(dur_ms)
            prog = t / (dur_ms / 1000.0)
            ruido = _filtrar(np.random.uniform(-1, 1, (len(t), 2)), pasa_altos=380, pasa_bajos=8200)
            f_c = 320.0 + 3600.0 * np.sin(np.pi * prog)
            silbido = np.sin(2 * np.pi * np.cumsum(f_c) / SR) * 0.24
            env = np.sin(np.pi * (prog ** 1.25)) ** 2.2
            p_l = (1.0 - prog) if not izq_a_der else prog
            p_r = 1.0 - p_l
            out = (ruido * 0.65 + np.column_stack((silbido * p_l, silbido * p_r))) * env[:, None]
            return _fade(_pico(out, 0.88), 10, 20)
        return cls._cargar_o_sintetizar("whoosh", _sint)

    @classmethod
    def stinger(cls) -> np.ndarray:
        def _sint():
            pista = _pista(950)
            notas = [(587.33, 0, 0.2), (880.0, 75, 0.8), (1174.66, 150, 0.5)]
            for f, pos, pan in notas:
                t = _t(420)
                mod = 2.5 * np.exp(-t * 24.0) * np.sin(2 * np.pi * f * 2.0 * t)
                onda = (np.sin(2 * np.pi * f * t + mod) + 0.3 * np.sin(2 * np.pi * f * 0.5 * t)) * np.exp(-t * 9.0)
                est = np.column_stack((onda * pan, onda * (1.0 - pan))) * 0.45
                _sumar(pista, est, pos)
            pista = _stereo_delay(pista, 140, 210, 0.35, 0.35)
            return _fade(_pico(pista, 0.88), 5, 160)
        return cls._cargar_o_sintetizar("stinger", _sint)

    @classmethod
    def textura_digital(cls, dur_ms: float = 850.0) -> np.ndarray:
        def _sint():
            pista = _pista(dur_ms)
            freqs = [1760.0, 2349.32, 2637.02, 3520.0, 1975.53, 3135.96]
            paso = 68
            for idx, pos in enumerate(range(0, int(dur_ms) - paso, paso)):
                f = freqs[idx % len(freqs)]
                t = _t(42)
                gota = np.sin(2 * np.pi * f * t) * np.hanning(len(t)) * 0.22
                pan = 0.15 if idx % 2 == 0 else 0.85
                _sumar(pista, np.column_stack((gota * pan, gota * (1.0 - pan))), pos)
            pista = _reverb_estereo(pista, seg=0.9, mezcla=0.45, brillo=6500)[: len(pista)]
            return _fade(_pico(pista, 0.65), 20, 120)
        return cls._cargar_o_sintetizar("texture", _sint)


# ---------------------------------------------------------------------------
# MÓDULO RADIO IMAGING FX
# ---------------------------------------------------------------------------

def preparar_ssml_con_pausa_cola(texto: str, rate: str = "1.03", pitch: str = "+0.0st") -> str:
    seguro = texto.replace("&", " y ").replace("<", "").replace(">", "").strip()
    palabras = seguro.split()

    if len(palabras) >= 4:
        corte = 2 if len(palabras) <= 5 else 3
        parte_inicial = " ".join(palabras[:-corte])
        parte_final = " ".join(palabras[-corte:])
        seguro = f"{parte_inicial} <break time='55ms'/> {parte_final}"

    seguro = re.sub(r'\.\s+', '. <break time="210ms"/> ', seguro)
    seguro = re.sub(r':\s+', ': <break time="150ms"/> ', seguro)
    return f"<speak><prosody rate='{rate}' pitch='{pitch}'>{seguro}</prosody></speak>"


def extraer_fragmento_vocal_original(voz_original: np.ndarray) -> np.ndarray:
    n = len(voz_original)
    min_muestras = int(SR * 0.38)
    max_muestras = min(int(SR * 1.15), int(n * 0.72))

    if max_muestras <= min_muestras:
        frag = voz_original[-min(n, int(SR * 0.55)):].copy()
    else:
        zona = np.abs(voz_original[n - max_muestras : n - min_muestras]).max(axis=1)
        ventana = max(1, int(SR * 0.015))
        env_suave = np.convolve(zona, np.ones(ventana) / ventana, mode="same")
        idx_valle = (n - max_muestras) + int(np.argmin(env_suave))
        frag = voz_original[idx_valle:].copy()

    r_in = min(int(SR * 0.035), len(frag) // 4)
    r_out = min(int(SR * 0.045), len(frag) // 4)
    if r_in > 0:
        c_in = 0.5 * (1.0 - np.cos(np.pi * np.linspace(0.0, 1.0, r_in)))
        frag[:r_in] *= c_in[:, None]
    if r_out > 0:
        c_out = 0.5 * (1.0 + np.cos(np.pi * np.linspace(0.0, 1.0, r_out)))
        frag[-r_out:] *= c_out[:, None]
    return frag


class ModuloRadioImagingFX:
    @staticmethod
    def fx_radio_filter(x: np.ndarray, mezcla: float = 1.0) -> np.ndarray:
        filtrado = _filtrar(x, pasa_altos=540, pasa_bajos=2400, orden=3)
        saturado = np.tanh(filtrado * 2.3) * 0.80
        comprimido = _comprimir(saturado, thr_db=-24.0, ratio=4.8, ataque_ms=2.5, relajo_ms=75.0)
        return _fade(x * (1.0 - mezcla) + comprimido * mezcla, 8, 18)

    @classmethod
    def fx_radio_filter_selectivo(cls, x: np.ndarray, modo: str = "apertura_con_whoosh") -> np.ndarray:
        n = len(x)
        if modo == "frase_completa" or n < int(SR * 0.6):
            return cls.fx_radio_filter(x, mezcla=0.92)

        out = x.copy()
        mitad = int(n * 0.48)
        tramo_rad = cls.fx_radio_filter(out, mezcla=0.95)
        trans = min(int(SR * 0.18), mitad // 2)

        if modo == "apertura_con_whoosh":
            curva = np.zeros(n)
            curva[: mitad - trans] = 1.0
            curva[mitad - trans : mitad + trans] = np.linspace(1.0, 0.0, 2 * trans)
            out = out * (1.0 - curva[:, None]) + tramo_rad * curva[:, None]
            wh = MotorCapasSoundDesign.whoosh(280.0) * 0.38
            _sumar(out, wh, (mitad * 1000.0 / SR) - 140.0, 1.0)
            return out
        else:
            curva = np.zeros(n)
            curva[mitad - trans : mitad + trans] = np.linspace(0.0, 1.0, 2 * trans)
            curva[mitad + trans :] = 1.0
            return out * (1.0 - curva[:, None]) + tramo_rad * curva[:, None]

    @staticmethod
    def fx_tape_stop(x: np.ndarray, dur_frenado_ms: float = 340.0) -> np.ndarray:
        n = len(x)
        n_freno = min(int(SR * dur_frenado_ms / 1000), n // 2)
        if n_freno < 100:
            return x
        parte_fija = x[:-n_freno].copy()
        tramo_final = x[-n_freno:].copy()

        n_salida = int(n_freno * 1.55)
        u = np.linspace(0.0, 1.0, n_salida)
        curva_tiempo = (1.0 - (1.0 - u) ** 2.2) * (n_freno - 1)
        idx_orig = np.arange(n_freno, dtype=np.float64)
        frenado_l = np.interp(curva_tiempo, idx_orig, tramo_final[:, 0])
        frenado_r = np.interp(curva_tiempo, idx_orig, tramo_final[:, 1])
        frenado = np.column_stack((frenado_l, frenado_r))

        env_caida = np.linspace(1.0, 0.0, n_salida) ** 1.4
        frenado *= env_caida[:, None]
        return np.vstack((parte_fija, frenado))

    @staticmethod
    def fx_pitch_shift(x: np.ndarray, semitonos: float = 3.0) -> np.ndarray:
        n = len(x)
        if n == 0:
            return x
        factor = 2.0 ** (semitonos / 12.0)
        n_nuevo = max(1, int(n / factor))
        idx_orig = np.arange(n, dtype=np.float64)
        idx_nuevo = np.linspace(0, n - 1, n_nuevo)
        l = np.interp(idx_nuevo, idx_orig, x[:, 0])
        r = np.interp(idx_nuevo, idx_orig, x[:, 1])
        out = np.column_stack((l, r))
        return _fade(_filtrar(out, pasa_altos=220, pasa_bajos=4400), 14, 38)

    @classmethod
    def fx_stutter_glitch_creativo(cls, frag: np.ndarray) -> np.ndarray:
        n_base = min(int(SR * 0.115), max(1, len(frag) // 3))
        silaba_base = _fade(frag[:n_base].copy(), 6, 12)

        patron = [
            (1.0, -2.0, 1400.0, 0.85, 0.20, 0.55),
            (1.0, -1.0, 2400.0, 0.20, 0.85, 0.68),
            (0.52, 0.0, 3800.0, 0.80, 0.30, 0.78),
            (0.52, 1.0, 5500.0, 0.30, 0.80, 0.88),
        ]
        piezas = []
        for frac_dur, st_pitch, lp_freq, pan_l, pan_r, gan in patron:
            recorte = silaba_base[: max(1, int(len(silaba_base) * frac_dur))].copy()
            pitched = cls.fx_pitch_shift(recorte, st_pitch) if st_pitch != 0.0 else recorte
            filtrado = _fade(_filtrar(pitched, pasa_altos=350, pasa_bajos=lp_freq), 5, 10)
            estereo = np.column_stack((filtrado[:, 0] * pan_l, filtrado[:, 1] * pan_r)) * gan
            piezas.append(estereo)

        piezas.append(frag)
        return np.vstack(piezas)

    @classmethod
    def fx_vocal_chop_melodico(cls, frag: np.ndarray, bpm: float = 122.0) -> np.ndarray:
        semi_ms = (60000.0 / bpm) / 4.0
        n_celda = max(1, int(SR * semi_ms / 1000))
        semilla = _fade(frag[: min(len(frag), int(n_celda * 1.4))].copy(), 8, 18)

        secuencia_chop = [
            (0.0, 0.85, 0.25, 0.85),
            (3.0, 0.25, 0.85, 0.72),
            (0.0, 0.50, 0.50, 0.25),
            (-2.0, 0.80, 0.30, 0.78),
            (5.0, 0.30, 0.80, 0.65),
            (0.0, 0.60, 0.40, 0.90),
        ]
        celdas = []
        for st, p_l, p_r, gan in secuencia_chop:
            tono = cls.fx_pitch_shift(semilla, st) if st != 0.0 else semilla.copy()
            ajustada = np.pad(tono, ((0, max(0, n_celda - len(tono))), (0, 0)))[:n_celda]
            env_gate = np.hanning(len(ajustada)) ** 0.7
            ajustada *= env_gate[:, None] * gan
            celda_est = np.column_stack((ajustada[:, 0] * p_l, ajustada[:, 1] * p_r))
            celdas.append(celda_est)

        chop_seq = _filtrar(np.vstack(celdas), pasa_altos=320, pasa_bajos=4800)
        return _fade(chop_seq, 10, 45)

    @staticmethod
    def fx_reverse_reverb(voz_original: np.ndarray, dur_swell_ms: float = 340.0) -> tuple[np.ndarray, float]:
        n_ini = min(int(SR * 0.28), len(voz_original) // 3)
        semilla = _fade(voz_original[:n_ini].copy(), 15, 30)
        rev = _reverb_estereo(semilla[::-1].copy(), seg=0.85, mezcla=0.85, brillo=3800)
        n_sw = min(len(rev), int(SR * dur_swell_ms / 1000))
        swell = rev[:n_sw][::-1].copy()
        swell = _filtrar(swell, pasa_altos=350, pasa_bajos=3500)

        curva = (np.linspace(0.0, 1.0, len(swell)) ** 2.4)[:, None]
        swell = _fade(_pico(swell * curva, 0.48), 25, 8)
        return np.vstack((swell, voz_original)), _dur_ms(swell)

    @classmethod
    def fx_echo_delay(
        cls,
        frag_original: np.ndarray,
        usar_pitch_shift: bool = False,
        usar_radio_filter: bool = False,
        usar_tape_stop: bool = False,
    ) -> np.ndarray:
        base = _filtrar(frag_original.copy(), pasa_altos=300, pasa_bajos=3800)
        if usar_radio_filter:
            base = cls.fx_radio_filter(base, mezcla=0.88)
        else:
            base = _chorus_estereo_fm(base)

        base_rev = _reverb_estereo(base, seg=1.15, mezcla=0.38, brillo=4200)[: len(base)]
        dur_frag_ms = _dur_ms(base_rev)
        pista_eco = _pista(dur_frag_ms + 1300.0)

        _sumar(pista_eco, base_rev, 0.0, _g(-2.6))

        rebote_1 = cls.fx_pitch_shift(base_rev, 2.0) if usar_pitch_shift else _filtrar(base_rev, pasa_altos=380, pasa_bajos=3200)
        rebote_2 = cls.fx_pitch_shift(base_rev, -2.0) if usar_pitch_shift else _filtrar(base_rev, pasa_altos=420, pasa_bajos=2800)
        rebote_3 = cls.fx_tape_stop(rebote_2, 280.0) if usar_tape_stop else _filtrar(rebote_2, pasa_bajos=2400)

        for offset_ms, clip, canal, db_nivel in [
            (265.0, rebote_1, 0, -7.2),
            (530.0, rebote_2, 1, -11.8),
            (795.0, rebote_3, 0, -16.0),
        ]:
            tramo = np.zeros_like(clip)
            tramo[:, canal] = clip[:, canal] * 0.92
            tramo[:, 1 - canal] = clip[:, 1 - canal] * 0.35
            _sumar(pista_eco, tramo, offset_ms, _g(db_nivel))

        n = len(pista_eco)
        curva_fade = np.linspace(1.0, 0.0, n) ** 1.35
        pista_eco *= curva_fade[:, None]
        return _fade(pista_eco, 16, 120)


# ---------------------------------------------------------------------------
# 5 INTENSIDADES DE PRODUCCIÓN
# ---------------------------------------------------------------------------

INTENSIDADES_IMAGING_V13 = [
    "clean_imaging",
    "modern_imaging",
    "high_energy_imaging",
    "emotional_imaging",
    "fm_classic_imaging",
]


def seleccionar_intensidad_imaging(franja: str, modo_clima: str) -> str:
    if modo_clima in ("lluvia", "tormenta") or franja in ("atardecer", "noche"):
        candidatos = ["emotional_imaging", "clean_imaging", "modern_imaging", "fm_classic_imaging"]
    elif franja in ("manana", "mediodia", "tarde"):
        candidatos = ["high_energy_imaging", "modern_imaging", "fm_classic_imaging", "clean_imaging"]
    else:
        candidatos = INTENSIDADES_IMAGING_V13
    return SELECTOR.elegir("intensidad_imaging_v13", candidatos)


def procesar_frase_segun_intensidad_v13(
    voz_original_seca: np.ndarray,
    intensidad: str,
    activar_repeticion: bool = True,
    bpm: float = 122.0,
) -> tuple[np.ndarray, float, float, str]:
    dur_seca_ms = _dur_ms(voz_original_seca)
    if not activar_repeticion:
        cola_corta = np.zeros((int(SR * 0.12), 2))
        return np.vstack((voz_original_seca, cola_corta)), 0.0, dur_seca_ms, "Voz Limpia Directa"

    frag_original = extraer_fragmento_vocal_original(voz_original_seca)
    pausa_sin_solapar = np.zeros((int(SR * 0.080), 2))
    offset_pre_ms = 0.0
    voz_base = voz_original_seca.copy()

    if intensidad == "high_energy_imaging":
        frag_stutter = ModuloRadioImagingFX.fx_stutter_glitch_creativo(frag_original)
        chop_cola = ModuloRadioImagingFX.fx_vocal_chop_melodico(frag_original, bpm=bpm)
        eco_cola = ModuloRadioImagingFX.fx_echo_delay(frag_stutter, usar_pitch_shift=True, usar_tape_stop=True)
        cola_combinada = _pista(max(_dur_ms(eco_cola), _dur_ms(chop_cola) + 300.0))
        _sumar(cola_combinada, eco_cola, 0.0, 0.90)
        _sumar(cola_combinada, chop_cola, 240.0, 0.55)
        cola_final = cola_combinada
        desc = "High Energy (Creative Stutter + Melodic Vocal Chop + Pitch/Tape Stop)"

    elif intensidad == "modern_imaging":
        voz_base, offset_pre_ms = ModuloRadioImagingFX.fx_reverse_reverb(voz_base, dur_swell_ms=260.0)
        chop_inicio = ModuloRadioImagingFX.fx_vocal_chop_melodico(frag_original, bpm=bpm)
        eco_cola = ModuloRadioImagingFX.fx_echo_delay(frag_original, usar_pitch_shift=True)
        cola_final = _pista(max(_dur_ms(eco_cola), _dur_ms(chop_inicio) + 250.0))
        _sumar(cola_final, eco_cola, 0.0, 0.92)
        _sumar(cola_final, chop_inicio, 260.0, 0.48)
        desc = "Modern Imaging (Reverse Swell + Vocal Chop + Pitch Echo)"

    elif intensidad == "fm_classic_imaging":
        voz_base = ModuloRadioImagingFX.fx_radio_filter_selectivo(voz_base, modo="apertura_con_whoosh")
        cola_final = ModuloRadioImagingFX.fx_echo_delay(frag_original, usar_radio_filter=True)
        desc = "FM Classic (Selective Radio Filter + Warm Ping-Pong Delay)"

    elif intensidad == "emotional_imaging":
        voz_base, offset_pre_ms = ModuloRadioImagingFX.fx_reverse_reverb(voz_base, dur_swell_ms=360.0)
        cola_final = ModuloRadioImagingFX.fx_echo_delay(frag_original)
        desc = "Emotional Imaging (Deep Reverse Reverb + Lush Hall Echo)"

    else:
        cola_final = ModuloRadioImagingFX.fx_echo_delay(frag_original)
        desc = "Clean Imaging (Minimalist Crystal Echo Delay)"

    audio_completo = np.vstack((voz_base, pausa_sin_solapar, cola_final))
    return audio_completo, offset_pre_ms, dur_seca_ms, desc


# ---------------------------------------------------------------------------
# PROTAGONISMO SONORO DE LA MARCA "NOGARADIO" (Hero Brand Moment + Sonic Logo)
# ---------------------------------------------------------------------------

def _pop_pluck(freq: float, dur_ms: float = 240, pan: float = 0.5) -> np.ndarray:
    t = _t(dur_ms)
    mod = 2.4 * np.exp(-t * 26.0) * np.sin(2 * np.pi * (freq * 2.0) * t)
    onda = np.sin(2 * np.pi * freq * t + mod) + 0.38 * np.sin(2 * np.pi * (freq * 0.5) * t)
    env = np.exp(-t * 10.0)
    ataque = min(int(SR * 0.003), len(t))
    env[:ataque] *= np.linspace(0, 1, ataque)
    mono = onda * env * 0.36
    return np.column_stack((mono * pan, mono * (1.0 - pan)))


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


def _sonic_logo_nogaradio() -> np.ndarray:
    pista = _pista(1450)
    motivo = [
        (587.33, 0, 320, 0.25),
        (739.99, 130, 320, 0.75),
        (880.00, 260, 380, 0.30),
        (1174.66, 410, 950, 0.65),
    ]
    for freq, pos_ms, dur, pan in motivo:
        _sumar(pista, _pop_pluck(freq, dur, pan=pan), pos_ms, 0.95)
        _sumar(pista, _pop_pluck(freq * 2.0, dur * 0.7, pan=1.0 - pan), pos_ms, 0.35)

    _sumar(pista, _synth_brass([293.66, 369.99, 440.00, 587.33, 659.25], 950, corte_lp=3200), 410, 0.65)
    pista = _stereo_delay(pista, 195, 290, 0.38, 0.34)
    return _fade(_reverb_estereo(pista, seg=1.1, mezcla=0.32)[: len(pista)], 10, 300)


def producir_momento_marca_nogaradio(
    client,
    audio_config,
    perfil_voz: str,
    intensidad: str,
    estilo: str = "pop_imaging",
) -> np.ndarray:
    """
    Crea la cápsula protagonista de marca NOGARADIO.
    Si EDITOR_VOZ_V14 está activo, pasa la frase por el Editor de Voz V14 (con pausa dramática
    antes de 'Nogaradio', reverse reverb o stutter) y suma el Sonic Logo.
    """
    frase_marca = SELECTOR.elegir("marca_protagonista", BANCO_PROTAGONISMO_NOGARADIO)

    if EDITOR_VOZ_V14 and editor_v14 is not None:
        voz_v14, fx_v14, _ = editor_v14.producir_locucion_editada_v14(
            client=client,
            texto=frase_marca,
            perfil=perfil_voz,
            audio_config=audio_config,
            core=sys.modules[__name__],
            intensidad=intensidad,
            estilo=estilo,
            es_noticia=False,
            es_remate=False,
            nombre_bloque="marca_nogaradio",
        )
        dur_total_ms = 320.0 + _dur_ms(voz_v14) + 950.0
        pista_marca = _pista(dur_total_ms)
        _sumar(pista_marca, MotorCapasSoundDesign.riser(320.0), 0.0, 0.65)
        _sumar(pista_marca, MotorCapasSoundDesign.bass_drop(850.0), 300.0, 0.78)
        _sumar(pista_marca, voz_v14, 320.0, 1.0)
        _sumar(pista_marca, fx_v14, 320.0, 0.90)
        _sumar(pista_marca, _sonic_logo_nogaradio(), 320.0 + _dur_ms(voz_v14) - 220.0, 0.72)
        return _fade(_pico(pista_marca, 0.95), 10, 180)

    # Comportamiento V13 clásico
    voz_seca = sintetizar_voz_perfil(client, frase_marca, perfil_voz, audio_config)
    voz_ancha = _chorus_estereo_fm(voz_seca)

    if intensidad == "high_energy_imaging":
        voz_marca = ModuloRadioImagingFX.fx_stutter_glitch_creativo(voz_ancha)
    elif intensidad == "fm_classic_imaging":
        voz_marca = ModuloRadioImagingFX.fx_radio_filter_selectivo(voz_ancha, modo="apertura_con_whoosh")
    else:
        voz_marca, _ = ModuloRadioImagingFX.fx_reverse_reverb(voz_ancha, dur_swell_ms=240.0)

    frag_eco = extraer_fragmento_vocal_original(voz_seca)
    cola_eco = ModuloRadioImagingFX.fx_echo_delay(frag_eco)

    dur_total_ms = 320.0 + _dur_ms(voz_marca) + 1100.0
    pista_marca = _pista(dur_total_ms)

    _sumar(pista_marca, MotorCapasSoundDesign.riser(340.0), 0.0, 0.65)
    _sumar(pista_marca, MotorCapasSoundDesign.bass_drop(900.0), 310.0, 0.78)
    _sumar(pista_marca, voz_marca, 320.0, 1.0)
    _sumar(pista_marca, cola_eco, 320.0 + _dur_ms(voz_marca) + 60.0, 0.75)
    _sumar(pista_marca, _sonic_logo_nogaradio(), 320.0 + _dur_ms(voz_marca) - 120.0, 0.72)
    return _fade(_pico(pista_marca, 0.95), 10, 180)


# ---------------------------------------------------------------------------
# Sintetizadores Musicales y Automatización de Cortes Rítmicos
# ---------------------------------------------------------------------------

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


def aplicar_cortes_ritmicos_bed(
    bus_cortinas: np.ndarray,
    bus_fx: np.ndarray,
    puntos_corte_ms: list[float],
    intensidad: str,
) -> None:
    """
    Aplica cortes rítmicos ("Beat Cuts") tanto en límites de bloque como en las
    palabras clave marcadas por la partitura V14. Evita duplicar cortes a menos de 600 ms.
    """
    dur_corte_ms = 220.0 if intensidad == "clean_imaging" else 270.0
    puntos_ordenados = sorted(set(round(p, 1) for p in puntos_corte_ms if p > 400.0))
    ultimo_corte = -1000.0

    for pt_ms in puntos_ordenados:
        if (pt_ms - ultimo_corte) < 650.0:
            continue
        ultimo_corte = pt_ms

        i_ini = max(0, int(SR * (pt_ms - dur_corte_ms) / 1000))
        i_fin = min(len(bus_cortinas), int(SR * (pt_ms + 40.0) / 1000))
        if i_fin - i_ini > 200:
            n_ventana = i_fin - i_ini
            curva_v = np.ones(n_ventana)
            f_out = min(int(SR * 0.045), n_ventana // 3)
            f_in = min(int(SR * 0.055), n_ventana // 3)
            curva_v[:f_out] = np.linspace(1.0, 0.06, f_out)
            curva_v[f_out : n_ventana - f_in] = 0.06
            curva_v[n_ventana - f_in :] = np.linspace(0.06, 1.0, f_in)
            bus_cortinas[i_ini:i_fin] *= curva_v[:, None]

            _sumar(bus_fx, MotorCapasSoundDesign.whoosh(260.0), pt_ms - 210.0, _g(-4.8))
            if intensidad in ("high_energy_imaging", "modern_imaging", "fm_classic_imaging"):
                _sumar(bus_fx, MotorCapasSoundDesign.bass_drop(720.0), pt_ms, _g(-4.2))


# ---------------------------------------------------------------------------
# 8 Estilos de Radio Imaging Musicales (Optimizados por Ciclo para Rapidez)
# ---------------------------------------------------------------------------

ESTILOS_IMAGING_V13 = [
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
    Genera la cortina musical del estilo elegido sintetizando 1 ciclo armónico completo
    y extendiéndolo limpiamente hasta dur_ms (hasta 6x más rápido en GitHub Actions).
    """
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

    # Sintetizamos exactamente 1 ciclo de la progresión y luego repetimos sin costuras
    ciclo_ms = compas_ms * len(acordes)
    ciclo_pista = _pista(ciclo_ms)
    musica_ciclo = _pista(ciclo_ms)
    t_ciclo = _t(ciclo_ms)

    for c, (pad_f, arp_f, bajo_f) in enumerate(acordes):
        ini_c = c * compas_ms
        _sumar(musica_ciclo, _synth_brass(pad_f, compas_ms + 120, corte_lp=corte_filtro), ini_c, 0.56)

        bajo_base = _funky_bass(bajo_f, beat_ms * (1.1 if estilo == "urban_imaging" else 0.45))
        bajo_oct = _funky_bass(bajo_f * 2.0, beat_ms * 0.45)

        if estilo == "retro_synthwave":
            for k in range(8):
                _sumar(musica_ciclo, bajo_base if k % 2 == 0 else bajo_oct, ini_c + k * (beat_ms * 0.5), 0.78)
        elif estilo == "urban_imaging":
            for pos_u in [0.0, beat_ms * 1.5, beat_ms * 2.5]:
                _sumar(musica_ciclo, bajo_base, ini_c + pos_u, 0.85)
        else:
            for k in range(8):
                _sumar(musica_ciclo, bajo_oct if k in (3, 7) else bajo_base, ini_c + k * (beat_ms * 0.5), 0.76)

        pasos_arp = 8 if estilo in ("cinematic_imaging", "emotional_imaging") else 16
        paso_ms = compas_ms / pasos_arp
        for s in range(pasos_arp):
            nota_f = arp_f[s % len(arp_f)]
            pan = 0.20 if s % 2 == 0 else 0.80
            _sumar(musica_ciclo, _pop_pluck(nota_f, 200, pan=pan), ini_c + s * paso_ms, 0.52)

    fase_beat = (t_ciclo * (1000.0 / beat_ms)) % 1.0
    sidechain = 0.48 + 0.52 * (1.0 - np.exp(-fase_beat * 5.0))
    musica_ciclo *= sidechain[:, None]
    ciclo_pista += _stereo_delay(musica_ciclo, int(beat_ms * 0.5), int(beat_ms * 0.75), 0.30, 0.24)

    kick = _kick_punch(230)
    snare = _snare_pop(185)
    hh_c = _hihat_crisp(55, abierto=False)
    hh_o = _hihat_crisp(150, abierto=True)

    for b in range(int(ciclo_ms / beat_ms)):
        pos_b = b * beat_ms
        _sumar(ciclo_pista, kick, pos_b, 0.85)
        if b % 2 == 1:
            _sumar(ciclo_pista, snare, pos_b, 0.72)
        _sumar(ciclo_pista, hh_c, pos_b, 0.60)
        if estilo == "urban_imaging" and b % 2 == 1:
            for r in range(4):
                _sumar(ciclo_pista, hh_c, pos_b + r * (semi_ms * 0.5), 0.50)
        else:
            _sumar(ciclo_pista, hh_o, pos_b + beat_ms * 0.5, 0.55)

    n_obj = max(1, int(SR * dur_ms / 1000))
    reps = (n_obj // len(ciclo_pista)) + 1
    pista = np.tile(ciclo_pista, (reps, 1))[:n_obj]

    if modo_clima in ("lluvia", "tormenta"):
        t = _t(dur_ms)[:n_obj]
        agua = _filtrar(np.random.uniform(-1, 1, (n_obj, 2)), pasa_altos=1100, pasa_bajos=7200)
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
# Intros Instrumentales y Vocales V13/V14
# ---------------------------------------------------------------------------

def sintetizar_intro_instrumental_v13() -> np.ndarray:
    estilo = SELECTOR.elegir("intro_inst_v13", [
        "dial_tuning_pips",
        "urbana_wah_groove",
        "la100_crystal_bells",
        "pop_funk_slap_intro",
        "cyber_glitch_stutter",
        "stadium_arena_hit",
    ])
    print(f"Intro Instrumental elegida: [{estilo}]")
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
        _sumar(pista, MotorCapasSoundDesign.impact(1200), 800, 0.92)

    elif estilo == "urbana_wah_groove":
        acorde_wah = _synth_brass([174.61, 220.0, 261.63, 329.63, 392.00], 800, corte_lp=3800)
        acorde_wah = _phaser_estereo_fx(acorde_wah) * (np.linspace(0.2, 1.0, len(acorde_wah))[:, None])
        _sumar(pista, acorde_wah, 0, 0.85)
        for pos_k in [0, 260, 520, 800, 1050]:
            _sumar(pista, _kick_punch(220), pos_k, 0.95)
        _sumar(pista, _snare_pop(180), 400, 0.85)
        _sumar(pista, MotorCapasSoundDesign.bass_drop(1250), 800, 0.95)

    elif estilo == "la100_crystal_bells":
        _sumar(pista, MotorCapasSoundDesign.sweep(780, ascendente=True), 0, 0.85)
        for idx_b, f_b in enumerate([523.25, 659.25, 783.99, 987.77, 1174.66, 1318.51]):
            _sumar(pista, _pop_pluck(f_b, 320, pan=0.15 + 0.7 * (idx_b / 5.0)), idx_b * 115, 0.85)
        _sumar(pista, MotorCapasSoundDesign.impact(1300), 780, 0.95)

    elif estilo == "pop_funk_slap_intro":
        for pos_s, f_s in [(0, 55.0), (190, 110.0), (380, 55.0), (570, 98.0), (800, 65.41)]:
            _sumar(pista, _funky_bass(f_s, 200), pos_s, 0.95)
            _sumar(pista, _kick_punch(200), pos_s, 0.90)
        _sumar(pista, _snare_pop(180), 380, 0.90)
        _sumar(pista, MotorCapasSoundDesign.riser(450), 350, 0.85)
        _sumar(pista, MotorCapasSoundDesign.bass_drop(1250), 800, 0.95)

    elif estilo == "cyber_glitch_stutter":
        _sumar(pista, MotorCapasSoundDesign.textura_digital(750), 0, 0.85)
        _sumar(pista, MotorCapasSoundDesign.riser(780), 20, 0.85)
        _sumar(pista, MotorCapasSoundDesign.impact(1350), 780, 1.0)
        _sumar(pista, _laser_drop(380), 780, 0.80)

    else:
        for idx_r, pos_r in enumerate([0, 180, 360, 480, 600, 700]):
            gan_r = 0.45 + 0.55 * (idx_r / 5.0)
            _sumar(pista, _snare_pop(190), pos_r, gan_r)
            _sumar(pista, _kick_punch(220), pos_r, gan_r * 0.8)
        _sumar(pista, MotorCapasSoundDesign.riser(750), 50, 0.85)
        _sumar(pista, MotorCapasSoundDesign.impact(1400), 800, 1.0)

    _sumar(pista, _sonic_logo_nogaradio(), 820, 0.98)
    return _fade(_reverb_estereo(pista, seg=1.25, mezcla=0.32)[: len(pista)], 15, 350)


def generar_intro_vocal_profesional_v13(
    client,
    audio_config,
    duracion_seg: int,
    estilo_imaging: str,
    intensidad: str,
    abre_mujer: bool,
    activar_eco_intro: bool,
) -> np.ndarray:
    voz_inicial = "mujer_juvenil" if abre_mujer else "hombre_grave"
    voz_alterna = "hombre_grave" if abre_mujer else "mujer_juvenil"

    if duracion_seg == 3:
        frase_1 = SELECTOR.elegir("intro_3s_v13", BANCO_FRASES_ENTRADA_IMPACTO)
        v1_seca = sintetizar_voz_perfil(client, frase_1, voz_inicial, audio_config)
        v1_audio, _, _, desc_1 = procesar_frase_segun_intensidad_v13(
            v1_seca, intensidad, activar_repeticion=activar_eco_intro
        )
        print(f"Intro 3s ({voz_inicial}) -> {desc_1}")

        dur_real_ms = max(3200.0, _dur_ms(v1_audio) + 950.0)
        pista_inst = _fade(_rms(generar_bed_estilo_imaging(estilo_imaging, "dia", dur_real_ms), REFERENCIA_RMS_DB), 40, 400)
        _sumar(pista_inst, MotorCapasSoundDesign.sweep(480, ascendente=True), 0, 0.85)
        _sumar(pista_inst, MotorCapasSoundDesign.impact(1100), 320, 0.90)
        _sumar(pista_inst, _sonic_logo_nogaradio(), dur_real_ms - 1350.0, 0.95)

        pista_voces = _pista(dur_real_ms)
        _sumar(pista_voces, v1_audio, 320.0, 1.0)

    elif duracion_seg == 6:
        f_entrada = SELECTOR.elegir("intro_6s_f1", BANCO_FRASES_ENTRADA_IMPACTO)
        v1_seca = sintetizar_voz_perfil(client, f_entrada, voz_inicial, audio_config)
        v1_audio, _, _, desc_1 = procesar_frase_segun_intensidad_v13(
            v1_seca, intensidad, activar_repeticion=True
        )
        v2_marca = producir_momento_marca_nogaradio(client, audio_config, voz_alterna, intensidad, estilo_imaging)
        print(f"Intro 6s -> P1 ({voz_inicial}): {desc_1} | P2 ({voz_alterna}): Protagonismo Nogaradio")

        pos_v1 = 350.0
        pos_v2 = pos_v1 + _dur_ms(v1_audio) + 120.0
        dur_real_ms = max(6000.0, pos_v2 + _dur_ms(v2_marca) + 450.0)

        pista_inst = _fade(_rms(generar_bed_estilo_imaging(estilo_imaging, "dia", dur_real_ms), REFERENCIA_RMS_DB), 50, 450)
        _sumar(pista_inst, MotorCapasSoundDesign.riser(450), 0, 0.80)
        _sumar(pista_inst, MotorCapasSoundDesign.bass_drop(1000), 350, 0.90)

        pista_voces = _pista(dur_real_ms)
        _sumar(pista_voces, v1_audio, pos_v1, 1.0)
        _sumar(pista_voces, v2_marca, pos_v2, 1.0)

    else:
        f1 = SELECTOR.elegir("intro_10s_f1", BANCO_FRASES_ENTRADA_IMPACTO)
        f2 = "La música no se detiene. Estás conectado."

        v1_seca = sintetizar_voz_perfil(client, f1, voz_inicial, audio_config)
        v1_audio, _, _, desc_1 = procesar_frase_segun_intensidad_v13(
            v1_seca, intensidad, activar_repeticion=True
        )
        v2_seca = sintetizar_voz_perfil(client, f2, voz_alterna, audio_config)
        v2_audio = ModuloRadioImagingFX.fx_radio_filter_selectivo(v2_seca, modo="apertura_con_whoosh")
        v3_marca = producir_momento_marca_nogaradio(client, audio_config, voz_inicial, intensidad, estilo_imaging)
        print(f"Intro 10s -> P1 ({voz_inicial}): {desc_1} | P3: Protagonismo Nogaradio")

        pos_v1 = 450.0
        pos_v2 = pos_v1 + _dur_ms(v1_audio) + 180.0
        pos_v3 = pos_v2 + _dur_ms(v2_audio) + 160.0
        dur_real_ms = max(10000.0, pos_v3 + _dur_ms(v3_marca) + 500.0)

        pista_inst = _fade(_rms(generar_bed_estilo_imaging(estilo_imaging, "dia", dur_real_ms), REFERENCIA_RMS_DB), 50, 500)
        _sumar(pista_inst, MotorCapasSoundDesign.textura_digital(650), 0.0, 0.70)
        _sumar(pista_inst, MotorCapasSoundDesign.impact(1100), 420.0, 0.90)
        _sumar(pista_inst, MotorCapasSoundDesign.stinger(), pos_v2 - 140.0, 0.85)

        pista_voces = _pista(dur_real_ms)
        _sumar(pista_voces, v1_audio, pos_v1, 1.0)
        _sumar(pista_voces, v2_audio, pos_v2, 1.0)
        _sumar(pista_voces, v3_marca, pos_v3, 1.0)

    pista_inst = _ducking_por_voz(pista_inst, pista_voces[: len(pista_inst)], reduccion_db=-4.5)
    mix = pista_inst * _g(-3.5) + pista_voces[: len(pista_inst)]
    return _fade(_pico(mix, 0.96), 20, 300)


# ---------------------------------------------------------------------------
# Transiciones y Cierre
# ---------------------------------------------------------------------------

def sintetizar_transicion() -> np.ndarray:
    estilo = SELECTOR.elegir("trans_v13", [
        "flash_news_radar",
        "double_laser_whip",
        "reverse_suction_drop",
        "tuning_stinger_hit",
    ])
    print(f"Estilo de Transición Clima->Noticias elegido: [{estilo}]")
    pista = _pista(2050)

    if estilo == "flash_news_radar":
        _sumar(pista, MotorCapasSoundDesign.textura_digital(500), 0, 0.80)
        for i, pos_b in enumerate([0, 150, 300, 450]):
            t_b = _t(70)
            f_s = 1760.0 if i == 3 else 1318.51
            sonar = (np.sin(2 * np.pi * f_s * t_b) + 0.3 * np.sin(2 * np.pi * f_s * 2 * t_b)) * np.hanning(len(t_b)) * 0.50
            _sumar(pista, _est(sonar), pos_b)
        _sumar(pista, MotorCapasSoundDesign.riser(500), 40, 0.90)
        _sumar(pista, MotorCapasSoundDesign.impact(1250), 500, 1.0)
        _sumar(pista, MotorCapasSoundDesign.bass_drop(1200), 500, 0.95)
        notas = [587.33, 880.0, 1174.66, 1318.51, 1760.0]
        ini_st = 500

    elif estilo == "double_laser_whip":
        _sumar(pista, _laser_drop(320), 0, 0.90)
        _sumar(pista, _laser_drop(380), 220, 1.0)
        _sumar(pista, MotorCapasSoundDesign.sweep(480, ascendente=False), 40, 0.92)
        _sumar(pista, MotorCapasSoundDesign.impact(1200), 460, 1.0)
        notas = [659.25, 987.77, 1318.51, 1567.98, 1975.53]
        ini_st = 460

    elif estilo == "reverse_suction_drop":
        _sumar(pista, MotorCapasSoundDesign.riser(550), 0, 1.0)
        _sumar(pista, _laser_drop(350), 520, 0.85)
        _sumar(pista, MotorCapasSoundDesign.bass_drop(1300), 520, 1.0)
        _sumar(pista, _kick_punch(280), 520, 1.0)
        notas = [880.0, 659.25, 987.77, 1318.51, 1760.0]
        ini_st = 520

    else:
        _sumar(pista, _radio_tuning_dial(480), 0, 0.88)
        _sumar(pista, MotorCapasSoundDesign.whoosh(480), 20, 0.90)
        _sumar(pista, MotorCapasSoundDesign.impact(1200), 460, 1.0)
        notas = [523.25, 659.25, 783.99, 1046.50, 1318.51]
        ini_st = 460

    stinger = _pista(1400)
    for i, f in enumerate(notas):
        _sumar(stinger, _pop_pluck(f, 450, pan=0.15 if i % 2 == 0 else 0.85), i * 85, 1.0)
    _sumar(stinger, _synth_brass([notas[0] * 0.5, notas[1] * 0.5, notas[2] * 0.5], 1050), 150, 0.90)
    _sumar(pista, _stereo_delay(stinger, 165, 250, 0.40, 0.36), ini_st, 1.0)

    return _fade(_reverb_estereo(pista, seg=1.05, mezcla=0.32), 10, 250)


def sintetizar_whip_pase_voces() -> np.ndarray:
    pista = _pista(780)
    _sumar(pista, MotorCapasSoundDesign.whoosh(360), 0, 0.88)
    _sumar(pista, _laser_drop(260), 280, 0.72)
    _sumar(pista, MotorCapasSoundDesign.bass_drop(650), 300, 0.85)
    _sumar(pista, MotorCapasSoundDesign.stinger(), 290, 0.82)
    return _fade(_reverb_estereo(pista, seg=0.7, mezcla=0.28)[: len(pista)], 10, 180)


def sintetizar_cierre() -> np.ndarray:
    pista = _pista(2900)
    _sumar(pista, MotorCapasSoundDesign.sweep(450, ascendente=True), 0, 0.72)
    _sumar(pista, MotorCapasSoundDesign.bass_drop(1600), 360, 0.92)
    _sumar(pista, MotorCapasSoundDesign.impact(1400), 380, 0.88)
    _sumar(pista, _funky_bass(73.42, 1900), 380, 0.90)
    _sumar(pista, _sonic_logo_nogaradio(), 380, 0.98)

    out = _reverb_estereo(pista, seg=1.6, mezcla=0.40)[: len(pista)]
    return _fade(out, 15, 1200)


# ---------------------------------------------------------------------------
# Cortina de Noticias (Optimizada por Ciclo) y Carga de Cortinas
# ---------------------------------------------------------------------------

def bed_noticias_groove(dur_ms: float) -> np.ndarray:
    beat_ms = 476.19
    semi_ms = beat_ms / 4.0
    compas_ms = beat_ms * 4.0

    progresion_not = [
        [220.0, 261.63, 329.63, 493.88],
        [174.61, 220.0, 261.63, 329.63],
        [146.83, 220.0, 261.63, 349.23],
        [164.81, 220.0, 246.94, 329.63],
    ]
    ciclo_ms = compas_ms * len(progresion_not)
    ciclo = _pista(ciclo_ms)

    for c, ac in enumerate(progresion_not):
        _sumar(ciclo, _synth_brass(ac, compas_ms, corte_lp=1650), c * compas_ms, 0.54)

    secuencia = [220.0, 220.0, 440.0, 220.0, 329.63, 220.0, 392.0, 329.63]
    for i, pos in enumerate(np.arange(0, ciclo_ms - semi_ms, semi_ms)):
        f = secuencia[i % len(secuencia)]
        pan = 0.22 if i % 2 == 0 else 0.78
        _sumar(ciclo, _pop_pluck(f, 125, pan=pan), pos, 0.64)
        if i % 2 == 0:
            _sumar(ciclo, _funky_bass(f * 0.5, 210), pos, 0.82)
        if i % 8 == 0:
            t_b = _t(55)
            bip = (np.sin(2 * np.pi * 1318.51 * t_b) + 0.3 * np.sin(2 * np.pi * 2637.02 * t_b)) * np.hanning(len(t_b))
            c_sonar = 0 if (i // 8) % 2 == 0 else 1
            sonar_est = np.zeros((len(t_b), 2))
            sonar_est[:, c_sonar] = bip * 0.28
            sonar_est[:, 1 - c_sonar] = bip * 0.10
            _sumar(ciclo, sonar_est, pos)

    kick = _kick_punch(210)
    snare = _snare_pop(165)
    hh_c = _hihat_crisp(50, abierto=False)
    hh_o = _hihat_crisp(140, abierto=True)

    for b, pos in enumerate(np.arange(0, ciclo_ms - beat_ms, beat_ms)):
        _sumar(ciclo, kick, pos, 0.88)
        if b % 2 == 1:
            _sumar(ciclo, snare, pos, 0.72)
        _sumar(ciclo, hh_c, pos, 0.60)
        _sumar(ciclo, hh_o, pos + beat_ms * 0.5, 0.55)

    n_obj = max(1, int(SR * dur_ms / 1000))
    reps = (n_obj // len(ciclo)) + 1
    pista = np.tile(ciclo, (reps, 1))[:n_obj]
    pista = _hueco_vocal_cortina(pista)
    return _stereo_delay(_filtrar(pista, pasa_altos=45, pasa_bajos=10500), 238, 357, 0.26, 0.22)


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


def construir_ambiente_v13(estilo_imaging: str, modo_clima: str, dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset(f"clima/{modo_clima}")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print(f"Generando cortina V14 Estilo [{estilo_imaging}] + Clima [{modo_clima}]...")
        base = generar_bed_estilo_imaging(estilo_imaging, modo_clima, dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 250, 450)


def construir_cama_noticias(dur_ms: float) -> np.ndarray:
    n = int(SR * dur_ms / 1000)
    seg = buscar_asset("noticias_cama")
    if seg is not None:
        base = seg_a_array(seg)
    else:
        print("Generando cortina V14 Breaking News...")
        base = bed_noticias_groove(dur_ms)
    x = _rms(_extender(base, n), REFERENCIA_RMS_DB)
    return _fade(x, 200, 2600)


def cargar_pista_opcional(base: str) -> np.ndarray | None:
    seg = buscar_asset(base)
    if seg is None:
        return None
    return _rms(seg_a_array(seg), REFERENCIA_RMS_DB)


# ---------------------------------------------------------------------------
# Cadena Vocal Broadcast V13
# ---------------------------------------------------------------------------

def procesar_voz_broadcast_v13(x: np.ndarray, perfil: str = "neutro") -> np.ndarray:
    x = _filtrar(x, pasa_altos=82)
    lodo = _filtrar(x, pasa_altos=270, pasa_bajos=380, orden=2)
    x = x - lodo * 0.22

    if perfil == "hombre_grave":
        cuerpo = _filtrar(x, pasa_altos=90, pasa_bajos=215) * 0.32
    elif perfil == "mujer_juvenil":
        cuerpo = _filtrar(x, pasa_altos=130, pasa_bajos=260) * 0.18
    else:
        cuerpo = _filtrar(x, pasa_altos=105, pasa_bajos=235) * 0.22

    medios_arm = _filtrar(x, pasa_altos=700, pasa_bajos=4200)
    arm_saturado = np.tanh((medios_arm + 0.04) * 1.65) - np.tanh(0.04 * 1.65)

    presencia = _filtrar(x, pasa_altos=2500, pasa_bajos=5800) * 0.34
    aire = np.tanh(_filtrar(x, pasa_altos=6800) * 1.9) * 0.12

    pre_comp = x + cuerpo + arm_saturado * 0.18 + presencia + aire
    voz_comp = _comprimir(pre_comp, thr_db=-22.5, ratio=3.5, ataque_ms=4.0, relajo_ms=110.0)
    ny_crush = _comprimir(pre_comp, thr_db=-31.0, ratio=8.0, ataque_ms=1.5, relajo_ms=65.0)
    final_voz = voz_comp + ny_crush * _g(-11.0)
    return _pico(final_voz, 0.94)


def sintetizar_voz_perfil(client, texto: str, perfil: str, audio_config) -> np.ndarray:
    mapa = {
        "hombre_grave": ("es-US-Neural2-B", "0.99", "-2.2st"),
        "mujer_juvenil": ("es-US-Neural2-A", "1.05", "+1.8st"),
        "hombre_locutor": ("es-US-Neural2-B", "1.03", "-0.6st"),
        "mujer_locutora": ("es-US-Neural2-A", "1.04", "+0.8st"),
    }
    nombre_voz, rate, pitch = mapa.get(perfil, ("es-US-Neural2-A", "1.03", "+0.0st"))
    ssml = preparar_ssml_con_pausa_cola(texto, rate=rate, pitch=pitch)
    entrada = texttospeech.SynthesisInput(ssml=ssml)
    voz = texttospeech.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
    resp = client.synthesize_speech(input=entrada, voice=voz, audio_config=audio_config)
    seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
    return procesar_voz_broadcast_v13(_recortar_silencio(seg_a_array(seg)), perfil=perfil)


# ---------------------------------------------------------------------------
# Construcción de Bloques Integrados con Editor de Voz V14
# ---------------------------------------------------------------------------

def ensamblar_bloque_con_micro_fx(
    partes: list[tuple[np.ndarray, float, str | None]],
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    cursor_ms = 0.0
    eventos_voz = []
    eventos_fx = []
    cortes_relativos = []
    max_fin_ms = 0.0

    for idx, (seg_voz, gap_ms, tipo_fx_puente) in enumerate(partes):
        gap_seguro = max(55.0, gap_ms) if cursor_ms > 0 else max(0.0, gap_ms)
        if idx > 0 and tipo_fx_puente:
            cortes_relativos.append(cursor_ms + gap_seguro)
            if tipo_fx_puente == "whoosh":
                eventos_fx.append((MotorCapasSoundDesign.whoosh(420.0), cursor_ms - 120.0, 0.65))
            elif tipo_fx_puente == "stinger":
                eventos_fx.append((MotorCapasSoundDesign.stinger(), cursor_ms - 60.0, 0.62))
            elif tipo_fx_puente == "textura":
                eventos_fx.append((MotorCapasSoundDesign.textura_digital(600.0), cursor_ms - 80.0, 0.55))

        cursor_ms = cursor_ms + gap_seguro
        eventos_voz.append((seg_voz, cursor_ms))
        fin_seg = cursor_ms + _dur_ms(seg_voz)
        if fin_seg > max_fin_ms:
            max_fin_ms = fin_seg
        cursor_ms = fin_seg

    pista_v = _pista(max_fin_ms + 150.0)
    pista_f = _pista(max_fin_ms + 150.0)
    for s_v, p_ms in eventos_voz:
        _sumar(pista_v, s_v, p_ms, 1.0)
    for s_f, p_ms, gan in eventos_fx:
        _sumar(pista_f, s_f, max(0.0, p_ms), gan)

    return pista_v, pista_f, cortes_relativos


def _sintetizar_con_editor_o_v13(
    client,
    texto: str,
    perfil: str,
    audio_config,
    intensidad: str,
    estilo: str,
    es_noticia: bool = False,
    es_remate: bool = False,
    nombre_bloque: str = "bloque",
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """Helper centralizado: usa Editor V14 si está activo y disponible; si falla, cae a V13."""
    if EDITOR_VOZ_V14 and editor_v14 is not None:
        return editor_v14.producir_locucion_editada_v14(
            client=client,
            texto=texto,
            perfil=perfil,
            audio_config=audio_config,
            core=sys.modules[__name__],
            intensidad=intensidad,
            estilo=estilo,
            es_noticia=es_noticia,
            es_remate=es_remate,
            nombre_bloque=nombre_bloque,
        )
    voz = sintetizar_voz_perfil(client, texto, perfil, audio_config)
    return voz, np.zeros_like(voz), []


def construir_bloque_clima_v13(
    client,
    audio_config,
    data_clima: dict | None,
    franja: str,
    intensidad: str,
    abre_mujer: bool,
    estilo_imaging: str = "pop_imaging",
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """
    Construye el bloque de clima integrando el Editor de Voz V14:
    protege todos los datos críticos del clima (temperatura, sensación, humedad, viento)
    y aplica efectos solo en frases de saludo, marca o transición.
    """
    ahora = ahora_argentina()
    hora_str = hora_en_espanol(ahora.hour, ahora.minute)
    saludo = saludo_segun_franja(franja)
    intro_clima = SELECTOR.elegir("pres_clima", validar_coherencia_horaria(BANCO_PRESENTACION_CLIMA, franja))
    pase_not = SELECTOR.elegir("trans_loc", BANCO_TRANSICIONES_LOCUTORES)

    p1 = "mujer_locutora" if abre_mujer else "hombre_locutor"
    p2 = "hombre_locutor" if abre_mujer else "mujer_locutora"

    if not data_clima or "current" not in data_clima:
        v1, fx1, c1 = _sintetizar_con_editor_o_v13(
            client, f"{saludo}. En Argentina son las {hora_str}.", p1, audio_config,
            intensidad, estilo_imaging, es_noticia=False, nombre_bloque="clima_saludo"
        )
        v2, fx2, c2 = _sintetizar_con_editor_o_v13(
            client, f"Seguimos juntos en Nogaradio. {pase_not}", p2, audio_config,
            intensidad, estilo_imaging, es_noticia=False, nombre_bloque="clima_pase"
        )
        pv, pf, cortes = ensamblar_bloque_con_micro_fx([(v1, 0.0, None), (v2, 160.0, "whoosh")])
        _sumar(pf, fx1, 0.0, 1.0)
        offset_v2 = _dur_ms(v1) + 160.0
        _sumar(pf, fx2, offset_v2, 1.0)
        cortes_tot = c1 + [offset_v2 + c for c in c2] + cortes
        return pv, pf, cortes_tot

    actual = data_clima["current"]
    temp = round(actual["temperature_2m"])
    sensacion = round(actual["apparent_temperature"])
    humedad = round(actual["relative_humidity_2m"])
    viento = round(actual["wind_speed_10m"])
    descripcion = CODIGOS_CLIMA.get(actual["weather_code"], "condiciones variables")

    texto_1 = f"{saludo}. En Argentina son las {hora_str}. {intro_clima}"
    texto_2 = (
        f"En {CIUDAD} tenemos {descripcion}, con {temp} grados de temperatura y una sensación térmica "
        f"de {sensacion}. La humedad es del {humedad} por ciento y vientos de {viento} kilómetros por hora."
    )
    texto_3 = pase_not

    v1, fx1, c1 = _sintetizar_con_editor_o_v13(
        client, texto_1, p1, audio_config, intensidad, estilo_imaging, es_noticia=False, nombre_bloque="clima_saludo"
    )
    v2, fx2, c2 = _sintetizar_con_editor_o_v13(
        client, texto_2, p2, audio_config, intensidad, estilo_imaging, es_noticia=False, nombre_bloque="clima_datos"
    )
    v3, fx3, c3 = _sintetizar_con_editor_o_v13(
        client, texto_3, p1, audio_config, intensidad, estilo_imaging, es_noticia=False, nombre_bloque="clima_pase"
    )

    if not (EDITOR_VOZ_V14 and editor_v14 is not None):
        if intensidad in ("fm_classic_imaging", "high_energy_imaging"):
            v3 = ModuloRadioImagingFX.fx_radio_filter_selectivo(v3, modo="frase_completa")

    pv, pf, cortes = ensamblar_bloque_con_micro_fx([
        (v1, 0.0, None),
        (v2, 150.0, "whoosh"),
        (v3, 180.0, "stinger" if intensidad != "clean_imaging" else "whoosh"),
    ])

    off_v2 = _dur_ms(v1) + 150.0
    off_v3 = off_v2 + _dur_ms(v2) + 180.0
    _sumar(pf, fx1, 0.0, 1.0)
    _sumar(pf, fx2, off_v2, 1.0)
    _sumar(pf, fx3, off_v3, 1.0)

    cortes_tot = c1 + [off_v2 + c for c in c2] + [off_v3 + c for c in c3] + cortes
    return pv, pf, cortes_tot


def construir_bloque_noticias_v13(
    client,
    audio_config,
    titular_mundo: str | None,
    titular_musica: str | None,
    abre_mujer: bool,
    intensidad: str = "modern_imaging",
    estilo_imaging: str = "pop_imaging",
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """
    Construye el bloque de noticias pasando por el Editor V14 con `es_noticia=True`
    (regla estricta de inteligibilidad: nunca stutter, chop, pitch ni radio filter).
    """
    p_mundo = "hombre_locutor" if abre_mujer else "mujer_locutora"
    p_musica = "mujer_juvenil" if abre_mujer else "hombre_locutor"

    pres_m = SELECTOR.elegir("pres_not_mundo", BANCO_PRESENTACION_NOTICIAS[:10])
    pres_mus = SELECTOR.elegir("pres_not_mus", BANCO_PRESENTACION_NOTICIAS[10:])

    partes = []
    fx_extras = []
    cortes_extras = []
    cursor_est = 0.0

    if titular_mundo:
        v_mundo, fx_m, c_m = _sintetizar_con_editor_o_v13(
            client, f"{pres_m} {titular_mundo}", p_mundo, audio_config,
            intensidad, estilo_imaging, es_noticia=True, nombre_bloque="noticia_mundo"
        )
        partes.append((v_mundo, 0.0, None))
        fx_extras.append((fx_m, 0.0))
        cortes_extras.extend(c_m)
        cursor_est = _dur_ms(v_mundo)

    if titular_musica:
        gap_m = 220.0 if partes else 0.0
        v_mus, fx_mus, c_mus = _sintetizar_con_editor_o_v13(
            client, f"{pres_mus} {titular_musica}", p_musica, audio_config,
            intensidad, estilo_imaging, es_noticia=True, nombre_bloque="noticia_musica"
        )
        partes.append((v_mus, gap_m, "stinger" if partes else None))
        off_mus = cursor_est + gap_m
        fx_extras.append((fx_mus, off_mus))
        cortes_extras.extend([off_mus + c for c in c_mus])

    if not partes:
        v_fb, fx_fb, c_fb = _sintetizar_con_editor_o_v13(
            client, "Ya estás al día con toda la actualidad en Nogaradio.", p_mundo, audio_config,
            intensidad, estilo_imaging, es_noticia=True, nombre_bloque="noticia_fallback"
        )
        partes.append((v_fb, 0.0, None))
        fx_extras.append((fx_fb, 0.0))
        cortes_extras.extend(c_fb)

    pv, pf, cortes = ensamblar_bloque_con_micro_fx(partes)
    for fx_arr, off_ms in fx_extras:
        _sumar(pf, fx_arr, off_ms, 1.0)

    return pv, pf, cortes + cortes_extras


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

    x = _comprimir(x, thr_db=-13.5, ratio=4.0, ataque_ms=4.5, relajo_ms=115.0)
    x = np.tanh(x * 1.12)
    return _fade(_pico(x, 0.94), 12, 850)


# ---------------------------------------------------------------------------
# Generador Principal del Spot V14 (con Cuantización al Beat y Fallback Seguro)
# ---------------------------------------------------------------------------

def generar_spot_v13(
    datos_clima: dict | None,
    titular_mundo: str | None,
    titular_musica: str | None,
    ambiente_nombre: str,
    franja_horaria: str,
    path_salida: str,
) -> None:
    if editor_v14 is not None:
        editor_v14.reiniciar_estado_editor_v14()

    client = texttospeech.TextToSpeechClient()
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    ahora = ahora_argentina()
    abre_mujer = (ahora.hour % 2 == 0)

    intensidad = seleccionar_intensidad_imaging(franja_horaria, ambiente_nombre)
    estilo_imaging = SELECTOR.elegir("estilo_imaging", ESTILOS_IMAGING_V13)
    bpm_actual = editor_v14.bpm_de_estilo(estilo_imaging) if editor_v14 is not None else 120.0
    modo_intro = SELECTOR.elegir("modo_intro", ["vocal_3s", "vocal_6s", "vocal_10s", "instrumental_v13"])
    estructura = SELECTOR.elegir("estructura_spot", [
        "estructura_1_clasica_pro",
        "estructura_2_musical_impacto",
        "estructura_3_noticias_primero",
        "estructura_4_marca_central",
    ])
    ubicacion_eco = SELECTOR.elegir("ubicacion_eco_v13", [
        "inicio_y_final",
        "solo_final",
        "solo_inicio",
        "inicio_y_final",
    ])
    eco_en_inicio = ubicacion_eco in ("inicio_y_final", "solo_inicio")
    eco_en_final = ubicacion_eco in ("inicio_y_final", "solo_final")

    print(
        f"Config V14 -> Hora AR: [{ahora.strftime('%H:%M')} - {franja_horaria.upper()}] "
        f"| Editor V14: [{'ON' if (EDITOR_VOZ_V14 and editor_v14) else 'OFF (V13)'}] "
        f"| Intensidad: [{intensidad}] | Estilo: [{estilo_imaging} @ {bpm_actual:.0f} BPM] "
        f"| Intro: [{modo_intro}]"
    )

    if modo_intro == "vocal_3s":
        intro = generar_intro_vocal_profesional_v13(client, audio_config, 3, estilo_imaging, intensidad, abre_mujer, eco_en_inicio)
        usa_apertura_extra = False
    elif modo_intro == "vocal_6s":
        intro = generar_intro_vocal_profesional_v13(client, audio_config, 6, estilo_imaging, intensidad, abre_mujer, eco_en_inicio)
        usa_apertura_extra = False
    elif modo_intro == "vocal_10s":
        intro = generar_intro_vocal_profesional_v13(client, audio_config, 10, estilo_imaging, intensidad, abre_mujer, eco_en_inicio)
        usa_apertura_extra = False
    else:
        intro = cargar_efecto("intro", sintetizar_intro_instrumental_v13)
        usa_apertura_extra = True

    frase_entrada = SELECTOR.elegir("entrada_impacto", BANCO_FRASES_ENTRADA_IMPACTO)
    frase_mus = frase_musical_segun_franja(franja_horaria)
    frase_app = SELECTOR.elegir("app_frase", BANCO_PROMOS_APP)
    frase_remate = remate_segun_franja(franja_horaria)

    voz_clima_b, fx_clima_b, cortes_clima = construir_bloque_clima_v13(
        client, audio_config, datos_clima, franja_horaria, intensidad, abre_mujer, estilo_imaging
    )
    voz_not_b, fx_not_b, cortes_not = construir_bloque_noticias_v13(
        client, audio_config, titular_mundo, titular_musica, abre_mujer, intensidad, estilo_imaging
    )

    perfil_marca = "hombre_grave" if abre_mujer else "mujer_juvenil"
    capsula_marca_nogaradio = producir_momento_marca_nogaradio(
        client, audio_config, perfil_marca, intensidad, estilo_imaging
    )

    # Frase musical y promo app también pasan por el planificador V14
    voz_mus_fx, fx_mus_v14, cortes_mus_v14 = _sintetizar_con_editor_o_v13(
        client, frase_mus, "mujer_juvenil" if abre_mujer else "hombre_grave", audio_config,
        intensidad, estilo_imaging, es_noticia=False, nombre_bloque="frase_musical"
    )
    voz_app_fx, fx_app_v14, cortes_app_v14 = _sintetizar_con_editor_o_v13(
        client, frase_app, "hombre_locutor" if abre_mujer else "mujer_locutora", audio_config,
        intensidad, estilo_imaging, es_noticia=False, nombre_bloque="promo_app"
    )

    # Remate final con Editor V14 (riser pre-remate + delay/tape stop según intensidad)
    perfil_remate = SELECTOR.elegir("perfil_remate_v13", ["hombre_grave", "mujer_juvenil"])
    if EDITOR_VOZ_V14 and editor_v14 is not None:
        remate_final_fx, fx_remate_v14, cortes_remate_v14 = _sintetizar_con_editor_o_v13(
            client, frase_remate, perfil_remate, audio_config,
            intensidad, estilo_imaging, es_noticia=False, es_remate=True, nombre_bloque="remate_final"
        )
        dur_remate_seco_ms = max(600.0, _dur_ms(remate_final_fx) - 900.0)
    else:
        voz_remate_seca = sintetizar_voz_perfil(client, frase_remate, perfil_remate, audio_config)
        remate_final_fx, _, dur_remate_seco_ms, desc_remate = procesar_frase_segun_intensidad_v13(
            voz_remate_seca, intensidad, activar_repeticion=eco_en_final, bpm=bpm_actual
        )
        fx_remate_v14 = np.zeros_like(remate_final_fx)
        cortes_remate_v14 = []
        print(f"Remate Final V13 ({perfil_remate}) -> {desc_remate}")

    if usa_apertura_extra:
        perfil_ap = "hombre_grave" if not abre_mujer else "mujer_juvenil"
        ap_fx, fx_ap_v14, c_ap_v14 = _sintetizar_con_editor_o_v13(
            client, frase_entrada, perfil_ap, audio_config,
            intensidad, estilo_imaging, es_noticia=False, nombre_bloque="apertura_impacto"
        )
        voz_clima_b, fx_ap_puente, c_ap = ensamblar_bloque_con_micro_fx([(ap_fx, 0.0, None), (voz_clima_b, 160.0, "whoosh")])
        off_c = _dur_ms(ap_fx) + 160.0
        fx_nuevo = _pista(_dur_ms(voz_clima_b) + 150.0)
        _sumar(fx_nuevo, fx_ap_v14, 0.0, 1.0)
        _sumar(fx_nuevo, fx_ap_puente, 0.0, 1.0)
        _sumar(fx_nuevo, fx_clima_b, off_c, 1.0)
        fx_clima_b = fx_nuevo
        cortes_clima = c_ap_v14 + c_ap + [off_c + c for c in cortes_clima]

    # Armado de los bloques según las 4 estructuras dinámicas
    if estructura == "estructura_1_clasica_pro":
        b1_voz, b1_fx, b1_cortes = voz_clima_b, fx_clima_b, cortes_clima
        v_b2, f_b2, c_b2 = ensamblar_bloque_con_micro_fx([
            (voz_not_b, 0.0, None),
            (capsula_marca_nogaradio, 180.0, "whoosh"),
            (voz_app_fx, 160.0, "stinger"),
        ])
        off_app = _dur_ms(voz_not_b) + 180.0 + _dur_ms(capsula_marca_nogaradio) + 160.0
        b2_voz = v_b2
        b2_fx = _pista(_dur_ms(v_b2) + 200.0)
        _sumar(b2_fx, fx_not_b, 0.0, 1.0)
        _sumar(b2_fx, f_b2, 0.0, 1.0)
        _sumar(b2_fx, fx_app_v14, off_app, 1.0)
        b2_cortes = cortes_not + c_b2 + [off_app + c for c in cortes_app_v14]

    elif estructura == "estructura_2_musical_impacto":
        v_b1, f_b1, c_b1 = ensamblar_bloque_con_micro_fx([
            (voz_mus_fx, 0.0, None),
            (capsula_marca_nogaradio, 150.0, "whoosh"),
            (voz_clima_b, 160.0, "stinger"),
        ])
        off_cli = _dur_ms(voz_mus_fx) + 150.0 + _dur_ms(capsula_marca_nogaradio) + 160.0
        b1_voz = v_b1
        b1_fx = _pista(_dur_ms(v_b1) + 200.0)
        _sumar(b1_fx, fx_mus_v14, 0.0, 1.0)
        _sumar(b1_fx, f_b1, 0.0, 1.0)
        _sumar(b1_fx, fx_clima_b, off_cli, 1.0)
        b1_cortes = cortes_mus_v14 + c_b1 + [off_cli + c for c in cortes_clima]
        b2_voz, b2_fx, b2_cortes = voz_not_b, fx_not_b, cortes_not

    elif estructura == "estructura_3_noticias_primero":
        b1_voz, b1_fx, b1_cortes = voz_not_b, fx_not_b, cortes_not
        v_b2, f_b2, c_b2 = ensamblar_bloque_con_micro_fx([
            (capsula_marca_nogaradio, 0.0, None),
            (voz_clima_b, 160.0, "whoosh"),
            (voz_app_fx, 160.0, "stinger"),
        ])
        off_cli = _dur_ms(capsula_marca_nogaradio) + 160.0
        off_app = off_cli + _dur_ms(voz_clima_b) + 160.0
        b2_voz = v_b2
        b2_fx = _pista(_dur_ms(v_b2) + 200.0)
        _sumar(b2_fx, f_b2, 0.0, 1.0)
        _sumar(b2_fx, fx_clima_b, off_cli, 1.0)
        _sumar(b2_fx, fx_app_v14, off_app, 1.0)
        b2_cortes = c_b2 + [off_cli + c for c in cortes_clima] + [off_app + c for c in cortes_app_v14]

    else:
        b1_voz, b1_fx, b1_cortes = voz_clima_b, fx_clima_b, cortes_clima
        v_b2, f_b2, c_b2 = ensamblar_bloque_con_micro_fx([
            (voz_not_b, 0.0, None),
            (voz_mus_fx, 160.0, "textura"),
            (capsula_marca_nogaradio, 160.0, "whoosh"),
        ])
        off_mus = _dur_ms(voz_not_b) + 160.0
        b2_voz = v_b2
        b2_fx = _pista(_dur_ms(v_b2) + 200.0)
        _sumar(b2_fx, fx_not_b, 0.0, 1.0)
        _sumar(b2_fx, f_b2, 0.0, 1.0)
        _sumar(b2_fx, fx_mus_v14, off_mus, 1.0)
        b2_cortes = cortes_not + c_b2 + [off_mus + c for c in cortes_mus_v14]

    trans = cargar_efecto("transicion", sintetizar_transicion)
    whip_duo = sintetizar_whip_pase_voces()
    cierre = cargar_efecto("cierre", sintetizar_cierre)

    # Cuantización de las entradas de bloque al beat / semicorchea (<= 90 ms)
    def _cuantizar(t_ms: float) -> float:
        if EDITOR_VOZ_V14 and editor_v14 is not None:
            return editor_v14.cuantizar_al_beat(t_ms, bpm_actual, max_desplazamiento_ms=90.0)
        return t_ms

    inicio_b1_raw = _dur_ms(intro) - 600.0 if usa_apertura_extra else _dur_ms(intro) - 200.0
    inicio_b1 = _cuantizar(max(900.0, inicio_b1_raw))
    fin_b1 = inicio_b1 + _dur_ms(b1_voz)

    inicio_trans = _cuantizar(fin_b1 + 120.0)
    inicio_b2 = _cuantizar(inicio_trans + 1500.0)
    fin_b2 = inicio_b2 + _dur_ms(b2_voz)

    inicio_whip = fin_b2 - 40.0
    inicio_remate = _cuantizar(fin_b2 + 400.0)
    fin_remate_seco = inicio_remate + dur_remate_seco_ms
    fin_cola_remate = inicio_remate + _dur_ms(remate_final_fx)

    inicio_cierre = fin_cola_remate - 350.0 if eco_en_final else fin_remate_seco + 100.0
    total_ms = max(inicio_cierre + _dur_ms(cierre), fin_cola_remate + 1450.0)

    # Bus de Voces
    bus_voces = _pista(total_ms + 900)
    _sumar(bus_voces, b1_voz, inicio_b1, 1.0)
    _sumar(bus_voces, b2_voz, inicio_b2, 1.0)
    _sumar(bus_voces, remate_final_fx, inicio_remate, 1.0)

    # Bus de Efectos y Capas de Sound Design
    bus_fx = _pista(total_ms + 900)
    _sumar(bus_fx, intro, 0.0, _g(EFECTOS_DB))
    _sumar(bus_fx, b1_fx, inicio_b1, _g(-2.0))
    _sumar(bus_fx, trans, inicio_trans, _g(EFECTOS_DB))
    _sumar(bus_fx, b2_fx, inicio_b2, _g(-2.0))
    _sumar(bus_fx, whip_duo, inicio_whip, _g(EFECTOS_DB - 0.8))
    _sumar(bus_fx, fx_remate_v14, inicio_remate, _g(-1.5))
    _sumar(bus_fx, cierre, inicio_cierre, _g(EFECTOS_DB - 1.2))

    # Bus de Cortinas con Automatización de Cortes Rítmicos (incluye palabras clave V14)
    ambiente = construir_ambiente_v13(estilo_imaging, ambiente_nombre, (inicio_trans - inicio_b1) + 1200.0)
    cama_noticias = construir_cama_noticias((total_ms - inicio_trans) + 1600.0)
    cama_ext = cargar_pista_opcional("cama")

    bus_cortinas = _pista(total_ms + 900)
    dur_tramo_1 = int(SR * (inicio_trans + 120.0 - inicio_b1) / 1000)
    if dur_tramo_1 > 0:
        amb_rec = _fade(ambiente[: min(len(ambiente), dur_tramo_1)], 250, 350)
        _sumar(bus_cortinas, amb_rec, inicio_b1, _g(AMBIENTE_DB))

    _sumar(bus_cortinas, cama_noticias, inicio_trans + 520.0, _g(TICKER_NOTICIAS_DB))
    if cama_ext is not None:
        _sumar(bus_cortinas, _extender(cama_ext, len(bus_cortinas)), 0.0, _g(CAMA_DB_LIBRE - 3.0))

    bus_cortinas = _ducking_por_voz(bus_cortinas, bus_voces, reduccion_db=-3.5)

    puntos_corte_globales = (
        [inicio_b1 + c for c in b1_cortes]
        + [inicio_b2 + c for c in b2_cortes]
        + [inicio_remate + c for c in cortes_remate_v14]
        + [inicio_remate]
    )
    aplicar_cortes_ritmicos_bed(bus_cortinas, bus_fx, puntos_corte_globales, intensidad)

    # Fade-Out progresivo final
    n_total = int(SR * total_ms / 1000)
    ini_fade_cama = max(0, int(SR * (fin_remate_seco + 100.0) / 1000))
    if n_total > ini_fade_cama:
        curva_cama = np.linspace(1.0, 0.0, n_total - ini_fade_cama) ** 1.5
        bus_cortinas[ini_fade_cama:n_total] *= curva_cama[:, None]
        bus_cortinas[n_total:] = 0.0

    mezcla = (bus_voces + bus_cortinas + bus_fx)[:n_total]
    final = masterizar(_fade(mezcla, 15, 950))
    SELECTOR.guardar()

    array_a_seg(final).export(path_salida, format="mp3", bitrate="192k")
    usadas_tts = editor_v14.obtener_llamadas_tts_usadas() if editor_v14 is not None else 0
    print(
        f"¡Spot V14 ({len(final) / SR:.1f} s | Llamadas TTS: {usadas_tts}) "
        f"guardado exitosamente en {path_salida}!"
    )


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
    t0 = time.time()
    modo_local = ("--local" in sys.argv) or (os.environ.get("DRY_RUN_LOCAL", "0").strip() == "1")
    ahora_ar = ahora_argentina()
    franja_horaria = obtener_contexto_horario_argentina(ahora_ar)
    print(
        f"=== EJECUTANDO NOGARADIO V14 PROFESSIONAL RADIO IMAGING | "
        f"Hora AR: {ahora_ar.strftime('%H:%M:%S')} | Franja: {franja_horaria.upper()} "
        f"| Modo Local: {'SI' if modo_local else 'NO'} ==="
    )

    sftp_host = os.environ.get("SFTP_HOST")
    sftp_port = int(os.environ.get("SFTP_PORT", "2022"))
    sftp_user = os.environ.get("SFTP_USER")
    sftp_pass = os.environ.get("SFTP_PASS")
    sftp_dir = os.environ.get("SFTP_REMOTE_DIR", "/clima")

    if not modo_local:
        faltantes = [n for n, v in [("SFTP_HOST", sftp_host), ("SFTP_USER", sftp_user), ("SFTP_PASS", sftp_pass)] if not v]
        if faltantes:
            print(f"Faltan variables: {', '.join(faltantes)} (usá --local para probar sin SFTP)", file=sys.stderr)
            sys.exit(1)

    print("Consultando clima (Estación SARE + respaldo Open-Meteo)...")
    datos_clima = obtener_clima()
    ambiente_nombre = elegir_ambiente(datos_clima, franja_horaria)

    print("Consultando y verificando noticias...")
    titular_mundo = obtener_titular_verificado(FEEDS_MUNDO, "mundo", 20)
    titular_musica = obtener_titular_verificado(FEEDS_MUSICA, "musica", 20)

    print("Generando spot con motor V14...")
    generar_spot_v13(datos_clima, titular_mundo, titular_musica, ambiente_nombre, franja_horaria, LOCAL_MP3)

    if modo_local:
        print(f"Modo local activo: se omitió la subida SFTP. Audio listo en {LOCAL_MP3}.")
    else:
        print("Subiendo por SFTP...")
        subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)

    print(f"Listo en {time.time() - t0:.1f} s.")


if __name__ == "__main__":
    main()
