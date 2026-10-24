#!/usr/bin/env python3
"""
Ejecuta clima_azuracast.py para una emisora concreta, usando voces de Microsoft Edge (edge-tts)
en lugar de Google Cloud. No hace falta modificar clima_azuracast.py.

Variables de entorno (las pone el workflow):
  NOMBRE_EMISORA   Nombre que dice la locución (reemplaza a "Nogaradio")
  CIUDAD_EMISORA   (opcional) Ciudad que dice la locución (reemplaza a "Resistencia")
  LAT_EMISORA      (opcional) Latitud para el clima
  LON_EMISORA      (opcional) Longitud para el clima
  VELOCIDAD_VOZ    (opcional) Velocidad de la voz, ej "+0%", "+5%", "-5%". Por defecto "+3%"
  SFTP_*           Credenciales de AzuraCast de esa emisora

Voces: la voz "A" (mujer) del script se reemplaza por es-AR-ElenaNeural y la "B" (hombre)
por es-AR-TomasNeural. Si la síntesis falla tras varios reintentos, el script termina con error
ANTES de subir nada, así que en AzuraCast queda el último audio bueno.
"""

import asyncio
import io
import os
import sys
import time
import types

# --- El script original importa google.cloud.texttospeech: lo reemplazamos por un módulo vacío
# --- para no depender de Google (ni de sus credenciales ni de su paquete).
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

VOZ_MUJER = "es-AR-ElenaNeural"
VOZ_HOMBRE = "es-AR-TomasNeural"
REINTENTOS = 4
ESPERA_S = 6


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


def configurar(modulo, nombre: str, ciudad: str = "", lat: str = "", lon: str = "") -> None:
    if lat:
        modulo.LAT = float(lat)
    if lon:
        modulo.LON = float(lon)

    modulo.sintetizar_voz = sintetizar_voz_edge
    original = modulo.generar_spot

    def generar_spot(texto_clima, texto_noticias, *resto, **kw):
        texto_clima = texto_clima.replace(NOMBRE_BASE, nombre)
        texto_noticias = texto_noticias.replace(NOMBRE_BASE, nombre)
        if ciudad:
            texto_clima = texto_clima.replace(CIUDAD_BASE, ciudad)
        return original(texto_clima, texto_noticias, *resto, **kw)

    modulo.generar_spot = generar_spot


def main() -> None:
    nombre = os.environ.get("NOMBRE_EMISORA", "").strip() or NOMBRE_BASE
    print(f"Emisora: {nombre}")
    configurar(
        c,
        nombre,
        os.environ.get("CIUDAD_EMISORA", "").strip(),
        os.environ.get("LAT_EMISORA", "").strip(),
        os.environ.get("LON_EMISORA", "").strip(),
    )
    c.main()


if __name__ == "__main__":
    main()
