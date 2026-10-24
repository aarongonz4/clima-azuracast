#!/usr/bin/env python3
"""
Ejecuta clima_azuracast.py para una emisora concreta. Cada emisora puede usar su propio motor de voz:
  - edge:   voces de Microsoft Edge (edge-tts), gratis
  - google: Google Cloud TTS (el original del script)
No hace falta modificar clima_azuracast.py.

Variables de entorno (las pone el workflow):
  NOMBRE_EMISORA   Nombre que dice la locución (reemplaza a "Nogaradio")
  CIUDAD_EMISORA   (opcional) Ciudad que dice la locución (reemplaza a "Resistencia")
  LAT_EMISORA      (opcional) Latitud para el clima
  LON_EMISORA      (opcional) Longitud para el clima
  VELOCIDAD_VOZ    (opcional) Velocidad de la voz, ej "+0%", "+5%", "-5%". Por defecto "+3%"
  MOTOR_VOZ        "edge" (voces de Microsoft Edge, gratis) o "google" (Google Cloud TTS). Por defecto "edge"
  VOZ_MUJER        (opcional, solo edge) Voz femenina. Por defecto es-AR-ElenaNeural
  VOZ_HOMBRE       (opcional, solo edge) Voz masculina. Por defecto es-AR-TomasNeural
  SOLO_CLIMA       (opcional) "1" = solo clima, sin noticias, cerrando con "seguimos escuchando..."
  SFTP_*           Credenciales de AzuraCast de esa emisora

Voces (motor edge): la voz "A" (mujer) del script se reemplaza por es-AR-ElenaNeural y la "B" (hombre)
por es-AR-TomasNeural. Si la síntesis falla tras varios reintentos, el script termina con error
ANTES de subir nada, así que en AzuraCast queda el último audio bueno.
"""

import asyncio
import io
import os
import random
import re
import sys
import time
import types

MOTOR = os.environ.get("MOTOR_VOZ", "").strip().lower() or "edge"

# --- Con motor edge no hace falta Google: reemplazamos su módulo por uno vacío.
# --- Con motor google se usa el paquete real (google-cloud-texttospeech).
if MOTOR != "google":
    _g = types.ModuleType("google")
    _gc = types.ModuleType("google.cloud")
    _tts = types.ModuleType("google.cloud.texttospeech")
    _tts.TextToSpeechClient = lambda *a, **k: None
    _tts.AudioConfig = lambda *a, **k: None
    _tts.AudioEncoding = types.SimpleNamespace(MP3=1)
    _g.cloud = _gc
    _gc.texttospeech = _tts
    sys.modules.update({"google": _g, "google.cloud": _gc, "google.cloud.texttospeech": _tts})

import edge_tts  # noqa: E402
from pydub import AudioSegment  # noqa: E402

import clima_azuracast as c  # noqa: E402

NOMBRE_BASE = "Nogaradio"
CIUDAD_BASE = "Resistencia"

VOZ_MUJER = os.environ.get("VOZ_MUJER", "").strip() or "es-AR-ElenaNeural"
VOZ_HOMBRE = os.environ.get("VOZ_HOMBRE", "").strip() or "es-AR-TomasNeural"
REINTENTOS = 4
ESPERA_S = 6


PLANTILLAS_SOLO_CLIMA = [
    "{saludo}. En Argentina son las {hora}. Nogaradio te cuenta cómo está el tiempo en Resistencia: tenemos {descripcion}, con {temp} grados y una sensación térmica de {sensacion}. La humedad es del {humedad} por ciento. Seguimos escuchando Nogaradio.",
    "{saludo}, ¿cómo estás? Son las {hora}. Así está el tiempo ahora en Resistencia: {descripcion}, {temp} grados de temperatura y {sensacion} de sensación térmica. Humedad del {humedad} por ciento. Seguí acompañándonos en Nogaradio.",
    "Nogaradio te informa. Son las {hora} en Argentina. El tiempo en Resistencia: cielo con {descripcion}, temperatura de {temp} grados, térmica de {sensacion} y vientos de {viento} kilómetros por hora. Seguimos escuchando Nogaradio.",
    "{saludo}. Con ustedes Nogaradio. En Argentina son las {hora}. En Resistencia tenemos {descripcion}, {temp} grados y una térmica de {sensacion}. La humedad es del {humedad} por ciento. Quedate con nosotros, seguimos escuchando Nogaradio.",
]

CIERRES_SOLO_CLIMA = [
    "Seguimos escuchando Nogaradio.",
    "Seguí acompañándonos en Nogaradio.",
    "Quedate con nosotros, seguimos escuchando Nogaradio.",
]


def _sin_noticias(texto: str) -> str:
    """Quita frases que anuncian noticias y asegura un cierre tipo 'seguimos escuchando'."""
    frases = re.split(r"(?<=[.!?])\s+", texto.strip())
    frases = [f for f in frases if not re.search(r"noticia|informativo|informaci[oó]n", f, re.I)]
    texto = " ".join(frases)
    if "seguimos" not in texto.lower() and "seguí" not in texto.lower():
        texto = f"{texto} {random.choice(CIERRES_SOLO_CLIMA)}"
    return texto


async def _descargar_audio(texto: str, voz: str, velocidad: str) -> bytes:
    comunicacion = edge_tts.Communicate(texto, voz, rate=velocidad)
    audio = bytearray()
    async for pedazo in comunicacion.stream():
        if pedazo["type"] == "audio":
            audio.extend(pedazo["data"])
    if not audio:
        raise RuntimeError("edge-tts no devolvió audio")
    return bytes(audio)


def _elegir_voz(nombre_voz_script: str) -> str:
    # En el script original: ...-A = mujer, ...-B = hombre
    return VOZ_HOMBRE if str(nombre_voz_script).upper().endswith("-B") else VOZ_MUJER


def sintetizar_voz_edge(client, texto: str, nombre_voz: str, audio_config):
    voz = _elegir_voz(nombre_voz)
    velocidad = os.environ.get("VELOCIDAD_VOZ", "").strip() or "+3%"
    ultimo_error = None
    for intento in range(1, REINTENTOS + 1):
        try:
            audio = asyncio.run(_descargar_audio(texto, voz, velocidad))
            seg = AudioSegment.from_file(io.BytesIO(audio), format="mp3")
            print(f"Voz {voz}: OK ({len(seg) / 1000:.1f} s)")
            return c.seg_a_array(seg)
        except Exception as e:  # noqa: BLE001
            ultimo_error = e
            print(f"edge-tts intento {intento}/{REINTENTOS} falló ({voz}): {e}", file=sys.stderr)
            if intento < REINTENTOS:
                time.sleep(ESPERA_S * intento)
    raise RuntimeError(f"No se pudo generar la voz con edge-tts: {ultimo_error}")


def configurar(modulo, nombre: str, ciudad: str = "", lat: str = "", lon: str = "",
               solo_clima: bool = False, motor: str = "edge") -> None:
    if lat:
        modulo.LAT = float(lat)
    if lon:
        modulo.LON = float(lon)

    if motor != "google":
        modulo.sintetizar_voz = sintetizar_voz_edge
    if solo_clima:
        modulo.PLANTILLAS_CLIMA = PLANTILLAS_SOLO_CLIMA
        modulo.obtener_titular = lambda *a, **k: None  # no consulta RSS
    original = modulo.generar_spot

    def generar_spot(texto_clima, texto_noticias, *resto, **kw):
        if solo_clima:
            texto_clima = _sin_noticias(texto_clima)
            texto_noticias = ""
        texto_clima = texto_clima.replace(NOMBRE_BASE, nombre)
        texto_noticias = texto_noticias.replace(NOMBRE_BASE, nombre)
        if ciudad:
            texto_clima = texto_clima.replace(CIUDAD_BASE, ciudad)
        return original(texto_clima, texto_noticias, *resto, **kw)

    modulo.generar_spot = generar_spot


def main() -> None:
    nombre = os.environ.get("NOMBRE_EMISORA", "").strip() or NOMBRE_BASE
    solo_clima = os.environ.get("SOLO_CLIMA", "").strip() == "1"
    print(f"Emisora: {nombre} | motor de voz: {MOTOR}" + (" | solo clima" if solo_clima else ""))
    configurar(
        c,
        nombre,
        os.environ.get("CIUDAD_EMISORA", "").strip(),
        os.environ.get("LAT_EMISORA", "").strip(),
        os.environ.get("LON_EMISORA", "").strip(),
        solo_clima,
        MOTOR,
    )
    c.main()


if __name__ == "__main__":
    main()
