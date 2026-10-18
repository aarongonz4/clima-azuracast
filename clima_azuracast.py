#!/usr/bin/env python3
"""
Spot automático de clima y noticias para AzuraCast (Nogaradio).

Incluye: reintentos anti-caídas para el clima, camuflaje web para RSS,
noticias rotativas, Google Cloud TTS a dos voces y EFECTOS DE SONIDO:
  - Intro (whoosh + chime)
  - Transición entre clima y noticias (sting tipo noticiero)
  - Cierre (chime descendente)
  - Cama musical opcional bajo las voces (assets/cama.mp3)

Los efectos se sintetizan artificialmente con numpy (no hace falta subir
archivos). Si existen archivos en la carpeta assets/, se usan en lugar
de los sintetizados:
  assets/intro.mp3, assets/transicion.mp3, assets/cierre.mp3, assets/cama.mp3
"""

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
from pydub.effects import normalize

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
SAMPLE_RATE = 44100

# Volumen (en dB) de la cama musical respecto a la voz. Más negativo = más bajo.
VOLUMEN_CAMA_DB = -24

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
# Efectos de sonido sintetizados (numpy + pydub)
# ---------------------------------------------------------------------------

def _a_segmento(onda: np.ndarray) -> AudioSegment:
    """Convierte un array float [-1, 1] en AudioSegment mono 16 bits."""
    onda = np.clip(onda, -1.0, 1.0)
    datos = (onda * 32767).astype(np.int16).tobytes()
    return AudioSegment(data=datos, sample_width=2, frame_rate=SAMPLE_RATE, channels=1)


def _nota(freq: float, dur_ms: int, decay: float = 4.0) -> np.ndarray:
    """Nota tipo campana: fundamental + armónicos con caída exponencial."""
    n = int(SAMPLE_RATE * dur_ms / 1000)
    t = np.linspace(0, dur_ms / 1000, n, endpoint=False)
    onda = (
        1.00 * np.sin(2 * np.pi * freq * t)
        + 0.40 * np.sin(2 * np.pi * freq * 2 * t)
        + 0.20 * np.sin(2 * np.pi * freq * 3 * t)
    )
    envolvente = np.exp(-decay * t)
    ataque = min(int(SAMPLE_RATE * 0.005), n)
    envolvente[:ataque] *= np.linspace(0, 1, ataque)
    return onda * envolvente * 0.35


def _secuencia(notas: list[tuple[float, int]], paso_ms: int, cola_ms: int = 600) -> AudioSegment:
    """Arma una secuencia de notas, cada una empezando 'paso_ms' después de la anterior."""
    total_ms = paso_ms * (len(notas) - 1) + cola_ms
    base = AudioSegment.silent(duration=total_ms, frame_rate=SAMPLE_RATE)
    for i, (freq, dur) in enumerate(notas):
        base = base.overlay(_a_segmento(_nota(freq, dur)), position=i * paso_ms)
    return base


def sintetizar_whoosh(dur_ms: int = 900, subida: bool = True) -> AudioSegment:
    """Barrido de ruido filtrado (efecto 'whoosh')."""
    n = int(SAMPLE_RATE * dur_ms / 1000)
    ruido = np.random.uniform(-1, 1, n) * 0.5
    seg = _a_segmento(ruido).low_pass_filter(5000).high_pass_filter(300)
    seg = seg.fade_in(dur_ms // 2 if subida else 60).fade_out(60 if subida else dur_ms // 2)
    return seg - 10


def sintetizar_intro() -> AudioSegment:
    # Do-Mi-Sol-Do agudo ascendente, con whoosh de entrada
    chime = _secuencia([(523.25, 700), (659.25, 700), (783.99, 700), (1046.50, 1200)], paso_ms=140, cola_ms=1200)
    whoosh = sintetizar_whoosh(700, subida=True)
    return whoosh.overlay(chime, position=350)


def sintetizar_transicion() -> AudioSegment:
    # Sting tipo noticiero: dos golpes graves + notas rápidas agudas
    golpes = _secuencia([(196.00, 500), (196.00, 500)], paso_ms=180, cola_ms=500)
    agudas = _secuencia([(1046.50, 400), (1318.51, 400), (1567.98, 700)], paso_ms=110, cola_ms=800)
    whoosh = sintetizar_whoosh(500, subida=False)
    return whoosh.overlay(golpes, position=0).overlay(agudas, position=380)


def sintetizar_cierre() -> AudioSegment:
    # Do-Sol-Mi-Do descendente
    return _secuencia([(1046.50, 800), (783.99, 800), (659.25, 800), (523.25, 1400)], paso_ms=160, cola_ms=1400)


def cargar_efecto(nombre_archivo: str, sintetizador) -> AudioSegment:
    """Usa assets/<archivo> si existe; si no, sintetiza el efecto."""
    ruta = os.path.join(ASSETS_DIR, nombre_archivo)
    if os.path.exists(ruta):
        print(f"Usando archivo propio: {ruta}")
        return AudioSegment.from_file(ruta).set_frame_rate(SAMPLE_RATE).set_channels(1)
    print(f"Sintetizando efecto: {nombre_archivo}")
    return sintetizador()


def aplicar_cama_musical(voz: AudioSegment) -> AudioSegment:
    """Si existe assets/cama.mp3, la repite y la mezcla bajita debajo de todo el audio."""
    ruta = os.path.join(ASSETS_DIR, "cama.mp3")
    if not os.path.exists(ruta):
        return voz
    print(f"Aplicando cama musical: {ruta}")
    cama = AudioSegment.from_file(ruta).set_frame_rate(SAMPLE_RATE).set_channels(1)
    repeticiones = (len(voz) // len(cama)) + 2
    cama = (cama * repeticiones)[: len(voz)]
    cama = cama.fade_in(800).fade_out(1500) + VOLUMEN_CAMA_DB
    return voz.overlay(cama)


# ---------------------------------------------------------------------------
# Google Cloud TTS (dos voces) + armado del spot
# ---------------------------------------------------------------------------

def sintetizar_voz(client, texto: str, nombre_voz: str, audio_config) -> AudioSegment:
    entrada = texttospeech.SynthesisInput(text=texto)
    voz = texttospeech.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
    resp = client.synthesize_speech(input=entrada, voice=voz, audio_config=audio_config)
    seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
    return seg.set_frame_rate(SAMPLE_RATE).set_channels(1)


def generar_spot(texto_clima: str, texto_noticias: str, path_salida: str) -> None:
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

    # 1) Voces
    voz_clima = sintetizar_voz(client, texto_clima, voz_clima_nombre, audio_config) if texto_clima else None
    voz_noticias = sintetizar_voz(client, texto_noticias, voz_noticias_nombre, audio_config) if texto_noticias else None

    # 2) Efectos
    intro = cargar_efecto("intro.mp3", sintetizar_intro)
    transicion = cargar_efecto("transicion.mp3", sintetizar_transicion)
    cierre = cargar_efecto("cierre.mp3", sintetizar_cierre)

    # 3) Ensamblado: intro -> clima -> transición -> noticias -> cierre
    pausa = AudioSegment.silent(duration=250, frame_rate=SAMPLE_RATE)
    spot = intro.fade_out(300)

    if voz_clima:
        spot = spot + pausa + voz_clima

    if voz_noticias:
        spot = spot + pausa + transicion + pausa + voz_noticias

    spot = spot + pausa + cierre

    # 4) Cama musical opcional + normalización de volumen
    spot = aplicar_cama_musical(spot)
    spot = normalize(spot, headroom=1.0)

    # 5) Exportar
    spot.export(path_salida, format="mp3", bitrate="128k")
    print(f"¡Spot final ({len(spot) / 1000:.1f} s) guardado exitosamente en {path_salida}!")


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

    print("Consultando noticias...")
    titular_mundo = obtener_titular(RSS_MUNDO, 15)
    titular_musica = obtener_titular(RSS_MUSICA, 15)
    texto_noticias = armar_texto_noticias(titular_mundo, titular_musica)

    print("Generando spot con voces y efectos...")
    generar_spot(texto_clima, texto_noticias, LOCAL_MP3)

    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")


if __name__ == "__main__":
    main()
