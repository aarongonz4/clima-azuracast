#!/usr/bin/env python3
"""
Script de locución automática de clima para AzuraCast.

Qué hace:
1. Consulta el clima actual de Resistencia, Chaco (OpenWeatherMap).
2. Arma un texto natural en español con la info.
3. Genera un archivo MP3 con gTTS (voz de Google, gratis).
4. Sube el MP3 por SFTP a la carpeta de medios de AzuraCast,
   siempre pisando el mismo nombre de archivo (clima.mp3),
   para que la playlist "Clima" (modo Jingle) siempre reproduzca
   la última versión.

Usa Open-Meteo (open-meteo.com) para el clima: es gratis, no pide API key
y funciona al instante, sin esperas de activación.

También lee 1 titular de actualidad (Diario Red) y 1 titular de música
(Planeta.pe) desde sus feeds RSS, y los suma al mismo audio.

Requiere:
    pip install requests gTTS paramiko feedparser

Variables de entorno necesarias (configurar como Secrets en GitHub Actions):
    SFTP_HOST       -> host que te dio AzuraCast en "SFTP Users"
    SFTP_PORT       -> puerto SFTP (normalmente 2022)
    SFTP_USER       -> usuario SFTP
    SFTP_PASS       -> contraseña SFTP
    SFTP_REMOTE_DIR -> carpeta remota donde va el archivo, ej: "/clima"
"""

import os
import random
import sys
from datetime import datetime

import feedparser
import requests
from gtts import gTTS
import paramiko

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

CIUDAD = "Resistencia,AR"
LAT = -27.4514
LON = -58.9867

LOCAL_MP3 = "clima.mp3"
REMOTE_FILENAME = "clima.mp3"  # nombre fijo: siempre pisa el mismo archivo

RSS_MUNDO = "https://www.diario-red.com/rss/"
RSS_MUSICA = "https://www.planeta.pe/noticias/musica/feed.xml"

# ---------------------------------------------------------------------------
# 1. Obtener el clima (Open-Meteo, sin API key)
# ---------------------------------------------------------------------------

# Códigos WMO de clima -> descripción en español
CODIGOS_CLIMA = {
    0: "cielo despejado",
    1: "mayormente despejado",
    2: "parcialmente nublado",
    3: "nublado",
    45: "neblina",
    48: "neblina con escarcha",
    51: "llovizna débil",
    53: "llovizna moderada",
    55: "llovizna intensa",
    61: "lluvia débil",
    63: "lluvia moderada",
    65: "lluvia intensa",
    71: "nevada débil",
    73: "nevada moderada",
    75: "nevada intensa",
    80: "chubascos débiles",
    81: "chubascos moderados",
    82: "chubascos fuertes",
    95: "tormenta eléctrica",
    96: "tormenta con granizo",
    99: "tormenta fuerte con granizo",
}


def obtener_clima() -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,weather_code",
        "timezone": "America/Argentina/Buenos_Aires",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def saludo_segun_hora(hora_actual: int) -> str:
    if 6 <= hora_actual < 12:
        return "Buenos días"
    if 12 <= hora_actual < 20:
        return "Buenas tardes"
    return "Buenas noches"


# Varias plantillas distintas, con la marca "Nogaradio", para que la
# locución no suene siempre igual.
# {saludo}, {hora}, {descripcion}, {temp}, {sensacion}, {humedad}, {viento}
PLANTILLAS = [
    (
        "Nogaradio te dice la hora y la temperatura. {saludo}, son las {hora} horas. "
        "Así está el clima en Resistencia: {descripcion}, con {temp} grados "
        "y sensación térmica de {sensacion} grados. Humedad del {humedad} por ciento "
        "y vientos de {viento} kilómetros por hora."
    ),
    (
        "{saludo}. Nogaradio te informa: son las {hora} horas en Resistencia, "
        "con {descripcion} y una temperatura de {temp} grados, que se siente como {sensacion}. "
        "Humedad del {humedad} por ciento, viento de {viento} kilómetros por hora."
    ),
    (
        "La hora y el clima, con Nogaradio. {hora} horas, {saludo}. "
        "El tiempo hoy en Resistencia: {descripcion}, temperatura de {temp} grados, "
        "sensación térmica de {sensacion} grados. Humedad del {humedad} por ciento, "
        "viento de {viento} kilómetros por hora."
    ),
    (
        "{saludo}, esto es Nogaradio. Reporte del tiempo en Resistencia, {hora} horas: "
        "{descripcion}, {temp} grados de temperatura y {sensacion} grados de sensación térmica. "
        "Humedad {humedad} por ciento, con viento de {viento} kilómetros por hora."
    ),
]


def armar_texto(data: dict) -> str:
    actual = data["current"]
    temp = round(actual["temperature_2m"])
    sensacion = round(actual["apparent_temperature"])
    humedad = round(actual["relative_humidity_2m"])
    viento = round(actual["wind_speed_10m"])
    descripcion = CODIGOS_CLIMA.get(actual["weather_code"], "condiciones variables")

    ahora = datetime.now()
    hora_str = ahora.strftime("%H:%M")
    saludo = saludo_segun_hora(ahora.hour)

    plantilla = random.choice(PLANTILLAS)
    texto = plantilla.format(
        saludo=saludo,
        hora=hora_str,
        descripcion=descripcion,
        temp=temp,
        sensacion=sensacion,
        humedad=humedad,
        viento=viento,
    )
    return texto


# ---------------------------------------------------------------------------
# 1b. Obtener titulares de RSS
# ---------------------------------------------------------------------------

def obtener_titular(url_feed: str) -> str | None:
    """Devuelve el título de la primera noticia del feed, o None si falla."""
    try:
        feed = feedparser.parse(url_feed)
        if feed.entries:
            return feed.entries[0].title.strip()
    except Exception as e:
        print(f"No se pudo leer el feed {url_feed}: {e}", file=sys.stderr)
    return None


PLANTILLAS_NOTICIAS = [
    "En la actualidad: {titular_mundo}. Y en el mundo de la música: {titular_musica}.",
    "Esto es lo último: {titular_mundo}. Además, en música: {titular_musica}.",
    "Te contamos: {titular_mundo}. Y del lado musical: {titular_musica}.",
    "Las noticias del momento: {titular_mundo}. En música, se habla de: {titular_musica}.",
]


def armar_texto_noticias(titular_mundo: str | None, titular_musica: str | None) -> str:
    partes = []
    if titular_mundo and titular_musica:
        plantilla = random.choice(PLANTILLAS_NOTICIAS)
        partes.append(plantilla.format(titular_mundo=titular_mundo, titular_musica=titular_musica))
    elif titular_mundo:
        partes.append(f"En la actualidad: {titular_mundo}.")
    elif titular_musica:
        partes.append(f"En el mundo de la música: {titular_musica}.")
    return " ".join(partes)


# ---------------------------------------------------------------------------
# 2. Generar el audio TTS
# ---------------------------------------------------------------------------

def generar_audio(texto: str, path_salida: str) -> None:
    tts = gTTS(text=texto, lang="es", tld="com.ar")
    tts.save(path_salida)


# ---------------------------------------------------------------------------
# 3. Subir por SFTP
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sftp_host = os.environ.get("SFTP_HOST")
    sftp_port = int(os.environ.get("SFTP_PORT", "2022"))
    sftp_user = os.environ.get("SFTP_USER")
    sftp_pass = os.environ.get("SFTP_PASS")
    sftp_dir = os.environ.get("SFTP_REMOTE_DIR", "/clima")

    faltantes = [
        nombre
        for nombre, valor in [
            ("SFTP_HOST", sftp_host),
            ("SFTP_USER", sftp_user),
            ("SFTP_PASS", sftp_pass),
        ]
        if not valor
    ]
    if faltantes:
        print(f"Faltan variables de entorno: {', '.join(faltantes)}", file=sys.stderr)
        sys.exit(1)

    print("Consultando clima...")
    data = obtener_clima()
    texto_clima = armar_texto(data)
    print(f"Texto clima: {texto_clima}")

    print("Consultando noticias...")
    titular_mundo = obtener_titular(RSS_MUNDO)
    titular_musica = obtener_titular(RSS_MUSICA)
    texto_noticias = armar_texto_noticias(titular_mundo, titular_musica)
    print(f"Texto noticias: {texto_noticias}")

    texto_final = f"{texto_clima} {texto_noticias}".strip()

    print("Generando audio...")
    generar_audio(texto_final, LOCAL_MP3)

    print("Subiendo por SFTP...")
    subir_sftp(LOCAL_MP3, sftp_host, sftp_port, sftp_user, sftp_pass, sftp_dir)

    print("Listo.")


if __name__ == "__main__":
    main()
