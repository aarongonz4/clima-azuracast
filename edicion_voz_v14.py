#!/usr/bin/env python3
"""
Módulo de Edición de Voz Profesional para Nogaradio (V14.1).
Ajustes de V14.1:
  - Mayor presencia real de efectos del Editor V14 (sin doble atenuación).
  - Ducking de colas suavizado a -5.5 dB para que los ecos y reverbs respiren
    detrás de la voz sin quedar tapados.
  - Mantiene corte por palabras con timepoints (v1beta1 SSML_MARK), fallbacks
    seguros, regla de inteligibilidad en clima/noticias y cuantización al BPM.
"""

import io
import math
import os
import re
import sys
from typing import Any

import numpy as np
from pydub import AudioSegment

try:
    from google.cloud import texttospeech_v1beta1 as tts_beta
except Exception:
    tts_beta = None

from google.cloud import texttospeech

# ---------------------------------------------------------------------------
# 1) DICCIONARIO EDITABLE DE PALABRAS CLAVE Y REGLAS DE INTELIGIBILIDAD
# ---------------------------------------------------------------------------

PALABRAS_CLAVE_V14: dict[str, list[str]] = {
    "MARCA": [
        "nogaradio",
    ],
    "PROMO": [
        "play store",
        "apple store",
        "descargá",
        "descarga",
        "aplicación",
        "aplicacion",
        "app",
    ],
    "ENERGIA": [
        "música",
        "musica",
        "ritmo",
        "energía",
        "energia",
        "en vivo",
        "volumen",
        "hits",
    ],
    "DATO_CRITICO": [
        "temperatura",
        "sensación",
        "sensacion",
        "térmica",
        "termica",
        "humedad",
        "viento",
        "vientos",
        "grados",
        "por ciento",
        "kilómetros",
        "kilometros",
        "cielo",
        "lluvia",
        "tormenta",
        "nublado",
        "despejado",
        "neblina",
        "llovizna",
        "chubascos",
    ],
}

NUMEROS_PALABRAS_CRITICAS = {
    "cero", "una", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete",
    "ocho", "nueve", "diez", "once", "doce", "trece", "catorce", "quince",
    "dieciséis", "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno",
    "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis",
    "veintisiete", "veintiocho", "veintinueve", "treinta", "cuarenta", "cincuenta",
    "sesenta", "setenta", "ochenta", "noventa", "cien",
}

EFECTOS_PROHIBIDOS_CRITICOS = {
    "stutter_glitch",
    "vocal_chop",
    "pitch_shift",
    "radio_filter",
    "tape_stop",
}

EFECTOS_FUERTES = {
    "stutter_glitch",
    "vocal_chop",
    "pitch_shift",
    "radio_filter",
    "tape_stop",
    "pausa_dramatica_impact",
}

# ---------------------------------------------------------------------------
# 2) REGLAS DE PARTITURA POR INTENSIDAD (Niveles V14.1 calibrados para presencia)
# ---------------------------------------------------------------------------

REGLAS_PARTITURA_POR_INTENSIDAD: dict[str, dict[str, Any]] = {
    "clean_imaging": {
        "max_efectos_por_bloque": 2,
        "efectos_marca": ["pausa_dramatica_impact", "delay_final_fade"],
        "efectos_promo": ["delay_final_fade"],
        "efectos_energia": ["reverse_reverb_palabra", "delay_final_fade"],
        "efectos_final_frase": ["delay_final_fade"],
        "efectos_remate": ["riser_pre_remate", "delay_final_fade"],
        "intensidad_db_fx": -1.5,
        "permite_vocal_chop": False,
        "permite_tape_stop": False,
    },
    "modern_imaging": {
        "max_efectos_por_bloque": 3,
        "efectos_marca": ["pausa_dramatica_impact", "reverse_reverb_palabra", "pitch_shift"],
        "efectos_promo": ["radio_filter", "delay_final_fade"],
        "efectos_energia": ["vocal_chop", "reverse_reverb_palabra"],
        "efectos_final_frase": ["delay_final_fade", "whoosh_post"],
        "efectos_remate": ["riser_pre_remate", "delay_final_fade"],
        "intensidad_db_fx": 0.0,
        "permite_vocal_chop": True,
        "permite_tape_stop": False,
    },
    "high_energy_imaging": {
        "max_efectos_por_bloque": 4,
        "efectos_marca": ["stutter_glitch", "pausa_dramatica_impact"],
        "efectos_promo": ["radio_filter", "stutter_glitch", "delay_final_fade"],
        "efectos_energia": ["vocal_chop", "stutter_glitch", "pitch_shift"],
        "efectos_final_frase": ["delay_final_fade", "whoosh_post"],
        "efectos_remate": ["riser_pre_remate", "tape_stop", "delay_final_fade"],
        "intensidad_db_fx": 0.5,
        "permite_vocal_chop": True,
        "permite_tape_stop": True,
    },
    "emotional_imaging": {
        "max_efectos_por_bloque": 2,
        "efectos_marca": ["reverse_reverb_palabra", "pausa_dramatica_impact"],
        "efectos_promo": ["delay_final_fade"],
        "efectos_energia": ["reverse_reverb_palabra"],
        "efectos_final_frase": ["delay_final_fade"],
        "efectos_remate": ["riser_pre_remate", "delay_final_fade"],
        "intensidad_db_fx": -1.0,
        "permite_vocal_chop": False,
        "permite_tape_stop": False,
    },
    "fm_classic_imaging": {
        "max_efectos_por_bloque": 3,
        "efectos_marca": ["pausa_dramatica_impact", "radio_filter"],
        "efectos_promo": ["radio_filter", "delay_final_fade"],
        "efectos_energia": ["radio_filter", "delay_final_fade"],
        "efectos_final_frase": ["delay_final_fade", "whoosh_post"],
        "efectos_remate": ["riser_pre_remate", "delay_final_fade"],
        "intensidad_db_fx": 0.0,
        "permite_vocal_chop": False,
        "permite_tape_stop": False,
    },
}

BPM_POR_ESTILO: dict[str, float] = {
    "electronic_imaging": 124.0,
    "cinematic_imaging": 100.0,
    "dance_club_imaging": 128.0,
    "pop_imaging": 120.0,
    "urban_imaging": 98.0,
    "epic_power_imaging": 116.0,
    "retro_synthwave": 110.0,
    "emotional_imaging": 104.0,
}

_CACHE_TTS_V14: dict[tuple[str, str], tuple[np.ndarray, list[dict[str, Any]]]] = {}
_CONTADOR_LLAMADAS_TTS: int = 0
MAX_LLAMADAS_TTS_SPOT: int = 25


def reiniciar_estado_editor_v14() -> None:
    global _CONTADOR_LLAMADAS_TTS
    _CONTADOR_LLAMADAS_TTS = 0


def obtener_llamadas_tts_usadas() -> int:
    return _CONTADOR_LLAMADAS_TTS


def bpm_de_estilo(estilo: str) -> float:
    return BPM_POR_ESTILO.get(estilo, 120.0)


# ---------------------------------------------------------------------------
# 3) NORMALIZACIÓN Y DETECCIÓN DE PALABRAS CLAVE
# ---------------------------------------------------------------------------

def _limpiar_palabra_cmp(palabra: str) -> str:
    return re.sub(r"[^a-záéíóúñ0-9]", "", palabra.lower())


def clasificar_palabras_clave(palabras: list[dict[str, Any]], es_noticia: bool = False) -> list[dict[str, Any]]:
    n = len(palabras)
    limpias = [_limpiar_palabra_cmp(p["texto"]) for p in palabras]

    for p in palabras:
        p["es_clave"] = False
        p["categoria_clave"] = None
        p["es_noticia"] = es_noticia

    for i in range(n - 1):
        bigrama = f"{limpias[i]} {limpias[i + 1]}".strip()
        for categoria in ("MARCA", "DATO_CRITICO", "PROMO", "ENERGIA"):
            for termino in PALABRAS_CLAVE_V14[categoria]:
                if " " in termino and bigrama == termino.lower():
                    for idx in (i, i + 1):
                        palabras[idx]["es_clave"] = True
                        palabras[idx]["categoria_clave"] = categoria

    for i, pal_norm in enumerate(limpias):
        if palabras[i]["categoria_clave"] is not None:
            continue
        if not pal_norm:
            continue

        if any(ch.isdigit() for ch in pal_norm) or pal_norm in NUMEROS_PALABRAS_CRITICAS:
            palabras[i]["es_clave"] = True
            palabras[i]["categoria_clave"] = "DATO_CRITICO"
            continue

        for categoria in ("MARCA", "DATO_CRITICO", "PROMO", "ENERGIA"):
            for termino in PALABRAS_CLAVE_V14[categoria]:
                if " " not in termino and pal_norm == _limpiar_palabra_cmp(termino):
                    palabras[i]["es_clave"] = True
                    palabras[i]["categoria_clave"] = categoria
                    break
            if palabras[i]["categoria_clave"] is not None:
                break

    return palabras


# ---------------------------------------------------------------------------
# 4) SÍNTESIS CON TIMEPOINTS (v1beta1 SSML_MARK) + FALLBACKS + CACHÉ
# ---------------------------------------------------------------------------

def _mapa_perfil_voz(perfil: str) -> tuple[str, str, str]:
    mapa = {
        "hombre_grave": ("es-US-Neural2-B", "0.99", "-2.2st"),
        "mujer_juvenil": ("es-US-Neural2-A", "1.05", "+1.8st"),
        "hombre_locutor": ("es-US-Neural2-B", "1.03", "-0.6st"),
        "mujer_locutora": ("es-US-Neural2-A", "1.04", "+0.8st"),
    }
    return mapa.get(perfil, ("es-US-Neural2-A", "1.03", "+0.0st"))


def _construir_ssml_con_marcas(texto: str, rate: str, pitch: str) -> tuple[str, list[str]]:
    seguro = texto.replace("&", " y ").replace("<", "").replace(">", "").strip()
    tokens = [tok for tok in seguro.split() if tok]
    partes_ssml = []
    for idx, tok in enumerate(tokens):
        partes_ssml.append(f'<mark name="w{idx}"/>{tok}')
        if tok.endswith((".", "!", "?")):
            partes_ssml.append('<break time="190ms"/>')
        elif tok.endswith(":"):
            partes_ssml.append('<break time="140ms"/>')
    cuerpo = " ".join(partes_ssml)
    ssml = f"<speak><prosody rate='{rate}' pitch='{pitch}'>{cuerpo}</prosody></speak>"
    return ssml, tokens


def _estimar_tiempos_palabras_en_tramo(
    texto_tramo: str,
    inicio_base_ms: float,
    duracion_ms: float,
) -> list[dict[str, Any]]:
    tokens = [tok for tok in texto_tramo.strip().split() if tok]
    if not tokens:
        return []
    pesos = [max(2, len(_limpiar_palabra_cmp(t))) for t in tokens]
    total_peso = float(sum(pesos))
    cursor = inicio_base_ms
    resultado = []
    for tok, peso in zip(tokens, pesos):
        dur_p = duracion_ms * (peso / total_peso)
        resultado.append({
            "texto": tok,
            "inicio_ms": round(cursor, 1),
            "fin_ms": round(cursor + dur_p, 1),
            "es_clave": False,
            "categoria_clave": None,
        })
        cursor += dur_p
    return resultado


def sintetizar_voz_con_tiempos(
    client,
    texto: str,
    perfil: str,
    audio_config,
    core: Any,
    es_noticia: bool = False,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    global _CONTADOR_LLAMADAS_TTS

    texto_norm = " ".join(texto.strip().split())
    clave_cache = (texto_norm, perfil)
    if clave_cache in _CACHE_TTS_V14:
        audio_c, palabras_c = _CACHE_TTS_V14[clave_cache]
        copia_palabras = [dict(p) for p in palabras_c]
        clasificar_palabras_clave(copia_palabras, es_noticia=es_noticia)
        return audio_c.copy(), copia_palabras

    nombre_voz, rate, pitch = _mapa_perfil_voz(perfil)
    ssml_marcas, tokens = _construir_ssml_con_marcas(texto_norm, rate, pitch)

    if tts_beta is not None and tokens and _CONTADOR_LLAMADAS_TTS < MAX_LLAMADAS_TTS_SPOT:
        try:
            beta_client = client if isinstance(client, tts_beta.TextToSpeechClient) else tts_beta.TextToSpeechClient()
            entrada = tts_beta.SynthesisInput(ssml=ssml_marcas)
            voz = tts_beta.VoiceSelectionParams(language_code="es-US", name=nombre_voz)
            cfg_beta = tts_beta.AudioConfig(audio_encoding=tts_beta.AudioEncoding.MP3)
            req = tts_beta.SynthesizeSpeechRequest(
                input=entrada,
                voice=voz,
                audio_config=cfg_beta,
                enable_time_pointing=[
                    tts_beta.SynthesizeSpeechRequest.TimepointType.SSML_MARK
                ],
            )
            resp = beta_client.synthesize_speech(request=req)
            _CONTADOR_LLAMADAS_TTS += 1

            seg = AudioSegment.from_file(io.BytesIO(resp.audio_content), format="mp3")
            audio_raw = core.seg_a_array(seg)

            env = np.abs(audio_raw).max(axis=1)
            activos = np.where(env > 0.015)[0]
            offset_recorte_ms = 0.0
            if len(activos) > 0:
                ini_idx = max(0, activos[0] - int(core.SR * 0.025))
                offset_recorte_ms = ini_idx * 1000.0 / core.SR

            audio_proc = core.procesar_voz_broadcast_v13(core._recortar_silencio(audio_raw), perfil=perfil)
            dur_total_ms = core._dur_ms(audio_proc)

            mapa_marcas: dict[int, float] = {}
            for tp in getattr(resp, "timepoints", []):
                m = re.match(r"^w(\d+)$", getattr(tp, "mark_name", ""))
                if m:
                    idx_w = int(m.group(1))
                    t_ms = max(0.0, float(tp.time_seconds) * 1000.0 - offset_recorte_ms)
                    mapa_marcas[idx_w] = min(dur_total_ms, t_ms)

            if len(mapa_marcas) >= max(1, len(tokens) // 2):
                palabras_res: list[dict[str, Any]] = []
                for idx, tok in enumerate(tokens):
                    ini_ms = mapa_marcas.get(idx)
                    if ini_ms is None:
                        ini_ms = palabras_res[-1]["fin_ms"] if palabras_res else 0.0
                    palabras_res.append({
                        "texto": tok,
                        "inicio_ms": round(ini_ms, 1),
                        "fin_ms": round(ini_ms, 1),
                        "es_clave": False,
                        "categoria_clave": None,
                    })

                for i in range(len(palabras_res)):
                    if i + 1 < len(palabras_res):
                        fin_i = max(palabras_res[i]["inicio_ms"] + 40.0, palabras_res[i + 1]["inicio_ms"])
                    else:
                        fin_i = dur_total_ms
                    palabras_res[i]["fin_ms"] = round(min(dur_total_ms, fin_i), 1)

                clasificar_palabras_clave(palabras_res, es_noticia=es_noticia)
                _CACHE_TTS_V14[clave_cache] = (audio_proc.copy(), [dict(p) for p in palabras_res])
                return audio_proc, palabras_res
        except Exception as e:
            print(f"[Editor V14.1] Aviso timepoints v1beta1 no disponibles ({e}). Activando fallback por frases...", file=sys.stderr)

    try:
        frases = [f.strip() for f in re.split(r"(?<=[.,:;!?])\s+", texto_norm) if f.strip()]
        if 1 < len(frases) <= 4 and (_CONTADOR_LLAMADAS_TTS + len(frases)) <= MAX_LLAMADAS_TTS_SPOT:
            pausa_ms = 110.0
            pausa_arr = np.zeros((int(core.SR * pausa_ms / 1000), 2))
            bloques_audio = []
            palabras_acum: list[dict[str, Any]] = []
            cursor_ms = 0.0

            for idx_f, frase in enumerate(frases):
                audio_f = core.sintetizar_voz_perfil(client, frase, perfil, audio_config)
                _CONTADOR_LLAMADAS_TTS += 1
                dur_f = core._dur_ms(audio_f)
                palabras_acum.extend(_estimar_tiempos_palabras_en_tramo(frase, cursor_ms, dur_f))
                bloques_audio.append(audio_f)
                cursor_ms += dur_f
                if idx_f < len(frases) - 1:
                    bloques_audio.append(pausa_arr)
                    cursor_ms += pausa_ms

            audio_total = np.vstack(bloques_audio)
            clasificar_palabras_clave(palabras_acum, es_noticia=es_noticia)
            _CACHE_TTS_V14[clave_cache] = (audio_total.copy(), [dict(p) for p in palabras_acum])
            return audio_total, palabras_acum
    except Exception as e:
        print(f"[Editor V14.1] Aviso fallback por frases falló ({e}). Usando fallback V13...", file=sys.stderr)

    try:
        audio_v13 = core.sintetizar_voz_perfil(client, texto_norm, perfil, audio_config)
        _CONTADOR_LLAMADAS_TTS += 1
        _CACHE_TTS_V14[clave_cache] = (audio_v13.copy(), [])
        return audio_v13, []
    except Exception as e:
        print(f"[Editor V14.1] Error crítico sintetizando voz: {e}", file=sys.stderr)
        raise


# ---------------------------------------------------------------------------
# 5) CUANTIZACIÓN AL BEAT / SEMICORCHEA
# ---------------------------------------------------------------------------

def cuantizar_al_beat(tiempo_ms: float, bpm: float, max_desplazamiento_ms: float = 90.0) -> float:
    if bpm <= 0:
        return tiempo_ms
    beat_ms = 60000.0 / bpm
    semi_ms = beat_ms / 4.0

    objetivo_beat = round(tiempo_ms / beat_ms) * beat_ms
    if abs(objetivo_beat - tiempo_ms) <= max_desplazamiento_ms:
        return max(0.0, round(objetivo_beat, 1))

    objetivo_semi = round(tiempo_ms / semi_ms) * semi_ms
    if abs(objetivo_semi - tiempo_ms) <= max_desplazamiento_ms:
        return max(0.0, round(objetivo_semi, 1))

    return max(0.0, round(tiempo_ms, 1))


# ---------------------------------------------------------------------------
# 6) PLANIFICADOR DE PARTITURA DE EFECTOS
# ---------------------------------------------------------------------------

def agrupar_palabras_en_frases(palabras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not palabras:
        return []

    frases: list[dict[str, Any]] = []
    actual: list[dict[str, Any]] = []

    for i, pal in enumerate(palabras):
        actual.append(pal)
        cierra_punt = pal["texto"].endswith((".", "!", "?", ":"))
        pausa_larga = False
        if i + 1 < len(palabras):
            pausa_larga = (palabras[i + 1]["inicio_ms"] - pal["fin_ms"]) > 260.0

        if cierra_punt or pausa_larga or i == len(palabras) - 1:
            frases.append({
                "indice": len(frases),
                "texto": " ".join(p["texto"] for p in actual),
                "palabras": list(actual),
                "inicio_ms": actual[0]["inicio_ms"],
                "fin_ms": actual[-1]["fin_ms"],
                "es_noticia": any(p.get("es_noticia", False) for p in actual),
                "contiene_dato_critico": any(p.get("categoria_clave") == "DATO_CRITICO" for p in actual),
            })
            actual = []

    return frases


def planificar_partitura(
    frases: list[dict[str, Any]],
    intensidad: str,
    estilo: str,
    bpm: float,
    selector: Any = None,
    es_remate: bool = False,
    nombre_bloque: str = "bloque",
) -> list[dict[str, Any]]:
    if not frases:
        return []

    reglas = REGLAS_PARTITURA_POR_INTENSIDAD.get(
        intensidad, REGLAS_PARTITURA_POR_INTENSIDAD["modern_imaging"]
    )
    max_eventos = reglas["max_efectos_por_bloque"]
    db_base = float(reglas["intensidad_db_fx"])

    eventos: list[dict[str, Any]] = []
    ultimo_indice_fuerte = -10
    ultimo_fue_fuerte = False

    def _elegir_con_rotacion(clave: str, opciones: list[str]) -> str:
        if not opciones:
            return ""
        if selector is not None:
            return selector.elegir(clave, opciones)
        return opciones[0]

    if es_remate and frases:
        t_riser = cuantizar_al_beat(frases[0]["inicio_ms"], bpm)
        eventos.append({
            "tiempo_ms": t_riser,
            "inicio_ms": frases[0]["inicio_ms"],
            "fin_ms": frases[0]["fin_ms"],
            "tipo_efecto": "riser_pre_remate",
            "palabra_objetivo": frases[0]["palabras"][0]["texto"],
            "intensidad_db": db_base - 0.5,
            "es_fuerte": False,
            "dispara_corte_bed": True,
        })

    for idx_f, frase in enumerate(frases):
        if len(eventos) >= max_eventos:
            break

        es_not = frase["es_noticia"]
        es_critica = frase["contiene_dato_critico"]
        puede_fuerte = (not ultimo_fue_fuerte) and ((idx_f - ultimo_indice_fuerte) >= 2)
        palabras_f = frase["palabras"]
        if not palabras_f:
            continue

        evento_frase = None

        pal_marca = next((p for p in palabras_f if p.get("categoria_clave") == "MARCA"), None)
        if pal_marca is not None and not es_not:
            candidatos = list(reglas["efectos_marca"])
            if not puede_fuerte:
                candidatos = [e for e in candidatos if e not in EFECTOS_FUERTES] or ["delay_final_fade"]
            efecto = _elegir_con_rotacion(f"v14_{nombre_bloque}_marca_{idx_f}", candidatos)
            evento_frase = {
                "tiempo_ms": cuantizar_al_beat(pal_marca["inicio_ms"], bpm),
                "inicio_ms": pal_marca["inicio_ms"],
                "fin_ms": pal_marca["fin_ms"],
                "tipo_efecto": efecto,
                "palabra_objetivo": pal_marca["texto"],
                "intensidad_db": db_base,
                "es_fuerte": efecto in EFECTOS_FUERTES,
                "dispara_corte_bed": True,
            }

        if evento_frase is None and not es_not and not es_critica:
            pal_promo = next((p for p in palabras_f if p.get("categoria_clave") == "PROMO"), None)
            if pal_promo is not None:
                candidatos = list(reglas["efectos_promo"])
                if not puede_fuerte:
                    candidatos = [e for e in candidatos if e not in EFECTOS_FUERTES] or ["delay_final_fade"]
                efecto = _elegir_con_rotacion(f"v14_{nombre_bloque}_promo_{idx_f}", candidatos)
                evento_frase = {
                    "tiempo_ms": cuantizar_al_beat(pal_promo["inicio_ms"], bpm),
                    "inicio_ms": pal_promo["inicio_ms"],
                    "fin_ms": pal_promo["fin_ms"],
                    "tipo_efecto": efecto,
                    "palabra_objetivo": pal_promo["texto"],
                    "intensidad_db": db_base - 0.3,
                    "es_fuerte": efecto in EFECTOS_FUERTES,
                    "dispara_corte_bed": True,
                }

        if evento_frase is None and not es_not and not es_critica:
            pal_ene = next((p for p in palabras_f if p.get("categoria_clave") == "ENERGIA"), None)
            if pal_ene is not None:
                candidatos = list(reglas["efectos_energia"])
                if not reglas["permite_vocal_chop"]:
                    candidatos = [e for e in candidatos if e != "vocal_chop"]
                if not puede_fuerte:
                    candidatos = [e for e in candidatos if e not in EFECTOS_FUERTES] or ["reverse_reverb_palabra"]
                if candidatos:
                    efecto = _elegir_con_rotacion(f"v14_{nombre_bloque}_energia_{idx_f}", candidatos)
                    evento_frase = {
                        "tiempo_ms": cuantizar_al_beat(pal_ene["inicio_ms"], bpm),
                        "inicio_ms": pal_ene["inicio_ms"],
                        "fin_ms": pal_ene["fin_ms"],
                        "tipo_efecto": efecto,
                        "palabra_objetivo": pal_ene["texto"],
                        "intensidad_db": db_base - 0.4,
                        "es_fuerte": efecto in EFECTOS_FUERTES,
                        "dispara_corte_bed": efecto in EFECTOS_FUERTES,
                    }

        if evento_frase is None and (idx_f == len(frases) - 1 or len(palabras_f) >= 6):
            ultima_pal = palabras_f[-1]
            if es_not or es_critica:
                candidatos_seguros = ["delay_suave_post", "whoosh_post"]
                efecto = _elegir_con_rotacion(f"v14_{nombre_bloque}_post_{idx_f}", candidatos_seguros)
                evento_frase = {
                    "tiempo_ms": cuantizar_al_beat(frase["fin_ms"], bpm),
                    "inicio_ms": ultima_pal["inicio_ms"],
                    "fin_ms": frase["fin_ms"],
                    "tipo_efecto": efecto,
                    "palabra_objetivo": ultima_pal["texto"],
                    "intensidad_db": db_base - 1.5,
                    "es_fuerte": False,
                    "dispara_corte_bed": False,
                }
            else:
                candidatos_fin = list(reglas["efectos_remate"] if es_remate else reglas["efectos_final_frase"])
                candidatos_fin = [e for e in candidatos_fin if e != "riser_pre_remate"]
                if not reglas["permite_tape_stop"] or not puede_fuerte:
                    candidatos_fin = [e for e in candidatos_fin if e != "tape_stop"]
                if not candidatos_fin:
                    candidatos_fin = ["delay_final_fade"]
                efecto = _elegir_con_rotacion(f"v14_{nombre_bloque}_fin_{idx_f}", candidatos_fin)
                evento_frase = {
                    "tiempo_ms": cuantizar_al_beat(ultima_pal["fin_ms"], bpm),
                    "inicio_ms": ultima_pal["inicio_ms"],
                    "fin_ms": ultima_pal["fin_ms"],
                    "tipo_efecto": efecto,
                    "palabra_objetivo": ultima_pal["texto"],
                    "intensidad_db": db_base - 0.5,
                    "es_fuerte": efecto in EFECTOS_FUERTES,
                    "dispara_corte_bed": es_remate,
                }

        if evento_frase is not None:
            if (es_not or es_critica) and evento_frase["tipo_efecto"] in EFECTOS_PROHIBIDOS_CRITICOS:
                evento_frase["tipo_efecto"] = "delay_suave_post"
                evento_frase["es_fuerte"] = False

            eventos.append(evento_frase)
            if evento_frase["es_fuerte"]:
                ultimo_indice_fuerte = idx_f
                ultimo_fue_fuerte = True
            else:
                ultimo_fue_fuerte = False

    imprimir_partitura(eventos, nombre_bloque, intensidad, bpm)
    return eventos


def imprimir_partitura(
    partitura: list[dict[str, Any]],
    nombre_bloque: str,
    intensidad: str,
    bpm: float,
) -> None:
    print(f"--- [PARTITURA V14.1 | {nombre_bloque.upper()} | {intensidad} | {bpm:.0f} BPM] ---")
    if not partitura:
        print("  (Sin efectos adicionales - lectura limpia)")
        return
    for ev in partitura:
        print(
            f"  @{ev['tiempo_ms']:7.1f} ms | {ev['tipo_efecto']:<24} | "
            f"palabra='{ev['palabra_objetivo']}' | {ev['intensidad_db']:+.1f} dB"
        )


def extraer_puntos_corte_partitura(partitura: list[dict[str, Any]]) -> list[float]:
    return [float(ev["tiempo_ms"]) for ev in partitura if ev.get("dispara_corte_bed")]


# ---------------------------------------------------------------------------
# 7) APLICACIÓN DE LA PARTITURA SOBRE LA VOZ (Presencia V14.1 + Ducking -5.5 dB)
# ---------------------------------------------------------------------------

def _aplicar_crossfade_insercion(
    base: np.ndarray,
    reemplazo: np.ndarray,
    ini_idx: int,
    fin_idx: int,
    xfade_ms: float = 8.0,
    sr: int = 44100,
) -> np.ndarray:
    n_xf = min(int(sr * xfade_ms / 1000), max(1, len(reemplazo) // 4))
    antes = base[:ini_idx].copy()
    despues = base[fin_idx:].copy()
    seg = reemplazo.copy()

    if len(antes) >= n_xf and len(seg) >= n_xf and n_xf > 1:
        rampa = np.linspace(0.0, 1.0, n_xf)[:, None]
        antes[-n_xf:] = antes[-n_xf:] * (1.0 - rampa) + seg[:n_xf] * rampa
        seg = seg[n_xf:]

    if len(despues) >= n_xf and len(seg) >= n_xf and n_xf > 1:
        rampa = np.linspace(0.0, 1.0, n_xf)[:, None]
        despues[:n_xf] = seg[-n_xf:] * (1.0 - rampa) + despues[:n_xf] * rampa
        seg = seg[:-n_xf]

    return np.vstack((antes, seg, despues))


def _ducking_colas_por_voz(
    pista_colas: np.ndarray,
    voz_seca: np.ndarray,
    sr: int = 44100,
    reduccion_db: float = -5.5,
) -> np.ndarray:
    """
    Aplica ducking moderado (-5.5 dB) sobre las colas de eco/reverb para que
    se escuchen claras y con aire sin tapar la siguiente frase hablada.
    """
    n = min(len(pista_colas), len(voz_seca))
    if n == 0:
        return pista_colas
    blk = int(sr * 0.012)
    nb = n // blk + 1
    env = np.pad(np.abs(voz_seca[:n]).max(axis=1), (0, nb * blk - n)).reshape(nb, blk).max(axis=1)
    hablando = (env > 0.045).astype(np.float64)

    a = math.exp(-12.0 / 20.0)
    r = math.exp(-12.0 / 150.0)
    suave = np.zeros(nb)
    val = 0.0
    for i in range(nb):
        coef = a if hablando[i] > val else r
        val = coef * val + (1.0 - coef) * hablando[i]
        suave[i] = val

    centros = (np.arange(nb) + 0.5) * blk
    curva_db = np.interp(np.arange(len(pista_colas)), centros, suave * reduccion_db)
    return pista_colas * (10.0 ** (curva_db / 20.0))[:, None]


def aplicar_partitura(
    audio: np.ndarray,
    palabras: list[dict[str, Any]],
    partitura: list[dict[str, Any]],
    core: Any,
    bpm: float = 120.0,
) -> tuple[np.ndarray, np.ndarray]:
    if not partitura or len(audio) == 0:
        return audio.copy(), np.zeros_like(audio)

    sr = core.SR
    voz_proc = audio.copy()
    dur_base_ms = core._dur_ms(voz_proc)

    pista_colas = core._pista(dur_base_ms + 2200.0)
    pista_fx = core._pista(dur_base_ms + 2200.0)

    eventos_ord = sorted(partitura, key=lambda e: float(e["inicio_ms"]))
    desplazamiento_ms = 0.0

    for ev in eventos_ord:
        tipo = ev["tipo_efecto"]
        ini_ms = max(0.0, float(ev["inicio_ms"]) + desplazamiento_ms)
        fin_ms = max(ini_ms + 60.0, float(ev["fin_ms"]) + desplazamiento_ms)
        gan_fx = core._g(float(ev.get("intensidad_db", 0.0)))

        ini_idx = max(0, min(len(voz_proc) - 1, int(sr * ini_ms / 1000)))
        fin_idx = max(ini_idx + 1, min(len(voz_proc), int(sr * fin_ms / 1000)))
        tramo = voz_proc[ini_idx:fin_idx].copy()

        if len(tramo) < int(sr * 0.03):
            continue

        if tipo == "pausa_dramatica_impact":
            pausa_ms = 155.0
            silencio = np.zeros((int(sr * pausa_ms / 1000), 2))
            nuevo_tramo = np.vstack((silencio, tramo))
            voz_proc = _aplicar_crossfade_insercion(voz_proc, nuevo_tramo, ini_idx, fin_idx, xfade_ms=7.0, sr=sr)
            core._sumar(pista_fx, core.MotorCapasSoundDesign.impact(900.0), ini_ms + pausa_ms - 15.0, gan_fx * 0.92)
            desplazamiento_ms += pausa_ms

        elif tipo == "reverse_reverb_palabra":
            rev_completo, sw_ms = core.ModuloRadioImagingFX.fx_reverse_reverb(tramo, dur_swell_ms=280.0)
            solo_swell = rev_completo[: int(sr * sw_ms / 1000)]
            core._sumar(pista_colas, solo_swell, max(0.0, ini_ms - sw_ms), gan_fx * 1.05)

        elif tipo == "stutter_glitch":
            tramo_st = core.ModuloRadioImagingFX.fx_stutter_glitch_creativo(tramo)
            delta_ms = core._dur_ms(tramo_st) - core._dur_ms(tramo)
            voz_proc = _aplicar_crossfade_insercion(voz_proc, tramo_st, ini_idx, fin_idx, xfade_ms=6.0, sr=sr)
            desplazamiento_ms += delta_ms

        elif tipo == "vocal_chop":
            chop = core.ModuloRadioImagingFX.fx_vocal_chop_melodico(tramo, bpm=bpm)
            core._sumar(pista_colas, chop, fin_ms + 25.0, gan_fx * 0.92)

        elif tipo == "pitch_shift":
            rep_up = core.ModuloRadioImagingFX.fx_pitch_shift(tramo, semitonos=2.5)
            rep_dn = core.ModuloRadioImagingFX.fx_pitch_shift(tramo, semitonos=-2.0)
            core._sumar(pista_colas, rep_up, fin_ms + 65.0, gan_fx * 0.78)
            core._sumar(pista_colas, rep_dn, fin_ms + 280.0, gan_fx * 0.55)

        elif tipo == "radio_filter":
            tramo_rf = core.ModuloRadioImagingFX.fx_radio_filter(tramo, mezcla=0.95)
            voz_proc = _aplicar_crossfade_insercion(voz_proc, tramo_rf, ini_idx, fin_idx, xfade_ms=8.0, sr=sr)

        elif tipo == "tape_stop":
            cola_ts = core.ModuloRadioImagingFX.fx_tape_stop(tramo, dur_frenado_ms=300.0)
            core._sumar(pista_colas, cola_ts, fin_ms + 35.0, gan_fx * 0.88)

        elif tipo in ("delay_final_fade", "delay_suave_post"):
            cola_eco = core.ModuloRadioImagingFX.fx_echo_delay(tramo)
            escala = 0.72 if tipo == "delay_suave_post" else 0.96
            core._sumar(pista_colas, cola_eco, fin_ms + 45.0, gan_fx * escala)

        elif tipo == "whoosh_post":
            core._sumar(pista_fx, core.MotorCapasSoundDesign.whoosh(380.0), fin_ms + 15.0, gan_fx * 0.85)

        elif tipo == "riser_pre_remate":
            core._sumar(pista_fx, core.MotorCapasSoundDesign.riser(460.0), max(0.0, ini_ms - 400.0), gan_fx * 0.90)

    largo_final = max(len(voz_proc), len(pista_colas), len(pista_fx))
    voz_ext = np.pad(voz_proc, ((0, max(0, largo_final - len(voz_proc))), (0, 0)))
    colas_ext = np.pad(pista_colas, ((0, max(0, largo_final - len(pista_colas))), (0, 0)))
    fx_ext = np.pad(pista_fx, ((0, max(0, largo_final - len(pista_fx))), (0, 0)))

    colas_ducked = _ducking_colas_por_voz(colas_ext, voz_ext, sr=sr, reduccion_db=-5.5)
    voz_final = core._pico(voz_ext + colas_ducked, 0.94)

    env_tot = np.maximum(np.abs(voz_final).max(axis=1), np.abs(fx_ext).max(axis=1))
    activos = np.where(env_tot > 0.004)[0]
    if len(activos) > 0:
        corte_fin = min(len(voz_final), activos[-1] + int(sr * 0.14))
        voz_final = core._fade(voz_final[:corte_fin], 5, 35)
        fx_ext = core._fade(fx_ext[:corte_fin], 5, 35)

    return voz_final, fx_ext


# ---------------------------------------------------------------------------
# 8) FUNCIÓN DE ALTO NIVEL PARA INTEGRACIÓN EN clima_azuracast.py
# ---------------------------------------------------------------------------

def producir_locucion_editada_v14(
    client,
    texto: str,
    perfil: str,
    audio_config,
    core: Any,
    intensidad: str,
    estilo: str,
    es_noticia: bool = False,
    es_remate: bool = False,
    nombre_bloque: str = "bloque",
) -> tuple[np.ndarray, np.ndarray, list[float]]:
    bpm = bpm_de_estilo(estilo)
    try:
        audio_base, palabras = sintetizar_voz_con_tiempos(
            client, texto, perfil, audio_config, core=core, es_noticia=es_noticia
        )
        if not palabras:
            return audio_base, np.zeros_like(audio_base), []

        frases = agrupar_palabras_en_frases(palabras)
        partitura = planificar_partitura(
            frases=frases,
            intensidad=intensidad,
            estilo=estilo,
            bpm=bpm,
            selector=getattr(core, "SELECTOR", None),
            es_remate=es_remate,
            nombre_bloque=nombre_bloque,
        )
        voz_editada, fx_editado = aplicar_partitura(
            audio_base, palabras, partitura, core=core, bpm=bpm
        )
        puntos_corte = extraer_puntos_corte_partitura(partitura)
        return voz_editada, fx_editado, puntos_corte
    except Exception as e:
        print(f"[Editor V14.1] Excepción capturada en '{nombre_bloque}' ({e}). Fallback a V13...", file=sys.stderr)
        audio_v13 = core.sintetizar_voz_perfil(client, texto, perfil, audio_config)
        return audio_v13, np.zeros_like(audio_v13), []
