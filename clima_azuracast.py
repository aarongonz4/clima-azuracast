#!/usr/bin/env python3
"""
Script de locución automática de clima y noticias para AzuraCast.
Incluye: Camuflaje web, noticias rotativas y Google Cloud TTS (Dos voces Chirp HD turnándose).
"""

import os
import random
import sys
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import feedparser
import requests
from google.cloud import texttospeech
import paramiko

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
    if hora_12 == 0: hora_12 = 12
    texto_hora = NUMEROS[hora_12]
    if minuto == 0: return f"{texto_hora} en punto"
    return f"{texto_hora} con {NUMEROS[minuto]} minutos"

def saludo_segun_hora(hora_actual: int) -> str:
    if 6 <= hora_actual < 12:
        return random.choice(["Buenos días", "Muy buenos días", "Hola, buenos días", "Excelente mañana"])
    elif 12 <= hora_actual < 20:
        return random.choice(["Buenas tardes", "Muy buenas tardes", "Hola, buenas tardes", "Excelente tarde"])
    return random.choice(["Buenas noches", "Muy buenas noches", "Excelente noche", "Hola, buenas noches"])

# ---------------------------------------------------------------------------
# Clima
# ---------------------------------------------------------------------------

def obtener_clima() -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT, "longitude": LON,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
        "timezone": "America/Argentina/Buenos_Aires",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()

PLANTILLAS_CLIMA = [
    "{saludo}. En Argentina son las {hora} horas. Nogaradio te cuenta cómo está el tiempo en Resistencia: {descripcion}, con {temp} grados y sensación térmica de {sensacion} grados. Humedad del {humedad} por ciento y vientos de {viento} kilómetros por hora. Ahora los dejamos con las noticias.",
    "{saludo}. En Argentina son las {hora} horas. Este es el informe del tiempo de Nogaradio. En Resistencia tenemos {descripcion}, {temp} grados de temperatura y una sensación térmica de {sensacion} grados. La humedad es del {humedad} por ciento. A continuación, el resumen de noticias.",
    "Nogaradio te informa. En Argentina son las {hora} horas. Así está el clima en Resistencia: {descripcion}, {temp} grados de temperatura y {sensacion} grados de sensación térmica. Humedad del {humedad} por ciento y viento de {viento} kilómetros por hora. Pasamos a la información.",
]

def armar_texto_clima(data: dict) -> str:
    actual = data["current"]
    temp = round(actual["temperature_2m"])
    sensacion = round(actual["apparent_temperature"])
    humedad = round(actual["relative_humidity_2m"])
    viento = round(actual["wind_speed_10m"])
    descripcion = CODIGOS_CLIMA.get(actual["weather_code"], "condiciones variables")

    ahora = datetime.now(ZONA_HORARIA)
    hora_str = hora_en_espanol(ahora.hour, ahora.minute)
    saludo = saludo_segun_hora(ahora.hour)
    
    plantilla = random.choice(PLANTILLAS_CLIMA)
    return plantilla.format(saludo=saludo, hora=hora_str, descripcion=descripcion, temp=temp, sensacion=sensacion, humedad=humedad, viento=viento)

# ---------------------------------------------------------------------------
# Noticias (Con Camuflaje Anti-Bots y Limpieza)
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

        # ¡Aquí sigue funcionando la variación de noticias! 
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
# Google Cloud TTS (Generación a DOS VOCES - CHIRP HD TURNÁNDOSE)
# ---------------------------------------------------------------------------

def generar_audio_dos_voces(texto_clima: str, texto_noticias: str, path_salida: str) -> None:
    client = texttospeech.TextToSpeechClient()
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)
    
    audio_combinado = b""
    ahora = datetime.now(ZONA_HORARIA)

    # Lógica de turnos: Hora PAR vs Hora IMPAR
    if ahora.hour % 2 == 0:
        # Hora Par (ej: 10:00, 12:00) -> Mujer: Clima | Hombre: Noticias
        voz_clima_nombre = "en-US-Chirp-HD-F"
        voz_noticias_nombre = "en-US-Chirp-HD-D"
        print("Turno actual (Hora Par): Mujer (Clima) -> Hombre (Noticias)")
    else:
        # Hora Impar (ej: 11:00, 13:00) -> Hombre: Clima | Mujer: Noticias
        voz_clima_nombre = "en-US-Chirp-HD-D"
        voz_noticias_nombre = "en-US-Chirp-HD-F"
        print("Turno actual (Hora Impar): Hombre (Clima) -> Mujer (Noticias)")

    # 1. AUDIO CLIMA 
    if texto_clima:
        input_clima = texttospeech.SynthesisInput(text=texto_clima)
        voz_clima = texttospeech.VoiceSelectionParams(
            language_code="en-US",
            name=voz_clima_nombre
        )
        resp_clima = client.synthesize_speech(input=input_clima, voice=voz_clima, audio_config=audio_config)
        audio_combinado += resp_clima.audio_content

    # 2. AUDIO NOTICIAS 
    if texto_noticias:
        input_noticias = texttospeech.SynthesisInput(text=texto_noticias)
        voz_noticias = texttospeech.VoiceSelectionParams(
            language_code="en-US",
            name=voz_noticias_nombre
        )
        resp_noticias = client.synthesize_speech(input=input_noticias, voice=voz_noticias, audio_config=audio_config)
        audio_combinado += resp_noticias.audio_content

    # 3. GUARDAR TODO JUNTO EN UN SOLO ARCHIVO MP3
    with open(path_salida, "wb") as out:
        out.write(audio_combinado)
        print(f"¡Audio final combinado a DOS VOCES guardado exitosamente en {path_salida}!")

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
    texto_clima = armar_texto_clima(obtener_clima())
    
    print("Consultando noticias...")
    titular_mundo = obtener_titular(RSS_MUNDO, 15)
    titular_musica = obtener_titular(RSS_MUSICA, 15)
    texto_noticias = armar_texto_noticias(titular_mundo, titular_musica)
    
    print("Generando audios combinados con Google Cloud...")
    generar_audio_dos_voces(texto_clima, texto_noticias, LOCAL_MP3)
    
    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)
    print("Listo.")

if __name__ == "__main__":
    main()
