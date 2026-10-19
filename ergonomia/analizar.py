"""Analiza un video de un puesto de trabajo y genera el informe ergonómico.

Uso (desde la raíz del repo):

    python -m ergonomia.analizar video.mp4 --carga-kg 8 --agarre regular

Deja en la carpeta de salida informe.pdf (para leer o imprimir; necesita
fpdf2), informe.md (el mismo texto), informe.json (para procesar) y
angulos.csv (ángulos cuadro por cuadro). Opcionalmente:

- --video-esqueleto: SOLO el esqueleto sobre fondo negro. Sirve para mostrar
  el análisis sin exponer la cara ni el cuerpo del trabajador.
- --video-superpuesto: el video original con el esqueleto, los ángulos y el
  REBA encima. Muestra a la persona: usarlo sólo con su consentimiento.

Si hay ffmpeg instalado, los videos se guardan en H.264 para que se vean en
cualquier navegador o celular, y se pueden analizar videos en formatos que
OpenCV no lee (AV1, el de muchas descargas de YouTube).
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import tempfile
from dataclasses import asdict, fields
from pathlib import Path

import cv2
import numpy as np

from .esqueleto import Angulos, Esqueleto, calcular_angulos, cuerpo_entero, elegir_lado, postura_posible
from .informe import Cuadro, armar_informe, informe_markdown
from .levantamiento import ALCANCE_BAJO, clasificar_tecnica
from .pdf import guardar_pdf
from .pose import crear_modelo
from .reba import AGARRES, Condiciones, calcular_reba

_HUESOS = (
    ("hombro", "codo"), ("codo", "muneca"), ("muneca", "indice"),
    ("hombro", "cadera"), ("cadera", "rodilla"), ("rodilla", "tobillo"), ("hombro", "oreja"),
)

# Color del texto del REBA según el nivel (BGR).
_COLOR_NIVEL = {
    "inapreciable": (120, 220, 120), "bajo": (120, 220, 120), "medio": (0, 210, 255),
    "alto": (60, 60, 255), "muy alto": (60, 60, 255),
}


def _dibujar(lienzo: np.ndarray, e: Esqueleto | None, lado: str | None,
             angulos: Angulos | None, condiciones: Condiciones, tiempo_s: float) -> np.ndarray:
    if e is not None and lado is not None:
        s = "_izq" if lado == "izq" else "_der"
        for a, b in _HUESOS:
            pa, pb = e.punto(a + s), e.punto(b + s)
            if pa and pb:
                cv2.line(lienzo, tuple(map(int, pa)), tuple(map(int, pb)), (0, 230, 255), 3)

    lineas = [(f"{tiempo_s:.1f} s", (255, 255, 255))]
    reba = calcular_reba(angulos, condiciones) if angulos else None
    if reba is None:
        lineas.append(("sin postura", (200, 200, 200)))
    else:
        lineas.append((f"tronco {angulos.tronco:.0f}  rodilla {_g(angulos.rodilla)}", (255, 255, 255)))
        lineas.append((f"REBA {reba.puntaje} ({reba.nivel})", _COLOR_NIVEL[reba.nivel]))
        if (angulos.muneca_sobre_piso is not None and angulos.muneca_sobre_piso < ALCANCE_BAJO
                and angulos.rodilla is not None):
            tecnica = clasificar_tecnica(angulos.tronco, angulos.rodilla)
            color = (60, 60, 255) if tecnica == "con la espalda" else (120, 220, 120)
            lineas.append((f"levanta {tecnica}", color))

    escala = max(0.5, lienzo.shape[1] / 700)
    alto_linea = int(32 * escala)
    cv2.rectangle(lienzo, (0, 0), (lienzo.shape[1], alto_linea * len(lineas) + 10), (0, 0, 0), -1)
    for i, (texto, color) in enumerate(lineas):
        cv2.putText(lienzo, texto, (8, alto_linea * (i + 1)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8 * escala, color, max(1, int(2 * escala)))
    return lienzo


def _g(valor: float | None) -> str:
    return "-" if valor is None else f"{valor:.0f}"


def _a_h264(ruta: Path) -> None:
    """Re-codifica a H.264: OpenCV escribe mp4v, que muchos navegadores no reproducen."""
    if shutil.which("ffmpeg") is None:
        return
    temporal = ruta.with_name(ruta.stem + "_h264.mp4")
    resultado = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(ruta), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(temporal)],
        check=False,
    )
    if resultado.returncode == 0:
        temporal.replace(ruta)


def _abrir_video(ruta: str) -> tuple[cv2.VideoCapture, Path | None]:
    """Abre el video; si OpenCV no lo puede leer, lo convierte a H.264 con ffmpeg.

    Devuelve también el archivo temporal convertido, para borrarlo al final.
    """
    captura = cv2.VideoCapture(ruta)
    if captura.isOpened() and captura.read()[0]:
        captura.set(cv2.CAP_PROP_POS_FRAMES, 0)
        return captura, None
    captura.release()
    if shutil.which("ffmpeg") is None:
        raise SystemExit(f"No se pudo leer el video: {ruta} (instalar ffmpeg permite leer más formatos, como AV1)")
    temporal = Path(tempfile.mkstemp(suffix=".mp4")[1])
    resultado = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", ruta, "-an", "-c:v", "libx264",
                                "-pix_fmt", "yuv420p", str(temporal)], check=False)
    captura = cv2.VideoCapture(str(temporal))
    if resultado.returncode != 0 or not captura.isOpened():
        temporal.unlink(missing_ok=True)
        raise SystemExit(f"No se pudo leer ni convertir el video: {ruta}")
    return captura, temporal


def analizar_video(ruta: str, modelo, lado: str = "auto", cuadros_por_segundo: float = 10.0,
                   condiciones: Condiciones | None = None, video_esqueleto: str | None = None,
                   video_superpuesto: str | None = None) -> tuple[list[Cuadro], float, str | None]:
    """Devuelve los cuadros analizados, los cuadros por segundo efectivos y el lado usado."""
    condiciones = condiciones or Condiciones()
    captura, convertido = _abrir_video(ruta)
    fps_video = captura.get(cv2.CAP_PROP_FPS) or 30.0
    paso = max(1, int(round(fps_video / cuadros_por_segundo)))
    fps_efectivo = fps_video / paso

    salidas = [r for r in (video_esqueleto, video_superpuesto) if r]
    escritores: dict[str, cv2.VideoWriter] = {}
    cuadros, lado_usado = [], None if lado == "auto" else lado
    indice = 0
    while True:
        ok, imagen = captura.read()
        if not ok:
            break
        if indice % paso:
            indice += 1
            continue
        tiempo_ms = int(indice * 1000 / fps_video)
        esqueleto = modelo.estimar(imagen, tiempo_ms)
        alto, ancho = imagen.shape[:2]
        angulos, centro, entero = None, None, True
        if esqueleto is not None and not cuerpo_entero(esqueleto, alto):
            # Primer plano, persona cortada o detección falsa sobre un objeto:
            # los ángulos saldrían inventados. No se mide ni se dibuja.
            esqueleto, entero = None, False
        if esqueleto is not None:
            # El lado se fija con el primer cuadro con persona: cambiarlo en
            # medio del video mezclaría los dos brazos en la misma serie.
            lado_usado = lado_usado or elegir_lado(esqueleto)
            angulos = calcular_angulos(esqueleto, lado_usado)
            if not postura_posible(angulos):
                esqueleto, angulos, entero = None, None, False
            cadera = esqueleto and (esqueleto.punto("cadera_izq") or esqueleto.punto("cadera_der"))
            if cadera:
                centro = (cadera[0] / ancho, cadera[1] / alto)
        cuadros.append(Cuadro(tiempo_ms / 1000.0, angulos, centro, entero))

        for salida in salidas:
            if salida not in escritores:
                escritores[salida] = cv2.VideoWriter(salida, cv2.VideoWriter_fourcc(*"mp4v"),
                                                     fps_efectivo, (ancho, alto))
            fondo = imagen.copy() if salida == video_superpuesto else np.zeros_like(imagen)
            escritores[salida].write(_dibujar(fondo, esqueleto, lado_usado, angulos,
                                              condiciones, tiempo_ms / 1000.0))
        indice += 1
    captura.release()
    if convertido is not None:
        convertido.unlink(missing_ok=True)
    for salida, escritor in escritores.items():
        escritor.release()
        _a_h264(Path(salida))
    return cuadros, fps_efectivo, lado_usado


def guardar_csv(cuadros: list[Cuadro], ruta: Path) -> None:
    columnas = [f.name for f in fields(Angulos)]
    with open(ruta, "w", newline="", encoding="utf-8") as archivo:
        w = csv.writer(archivo)
        w.writerow(["tiempo_s", *columnas])
        for c in cuadros:
            valores = asdict(c.angulos) if c.angulos else {}
            w.writerow([f"{c.tiempo_s:.2f}", *[
                "" if valores.get(k) is None else (f"{valores[k]:.1f}" if isinstance(valores[k], float) else valores[k])
                for k in columnas
            ]])


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video")
    p.add_argument("--salida", default=None, help="carpeta de salida (por defecto, <video>_ergonomia)")
    p.add_argument("--modelo", choices=("yolo", "mediapipe"), default="yolo")
    p.add_argument("--ruta-modelo", default=None, help="pesos YOLO (.pt) o archivo .task de MediaPipe")
    p.add_argument("--lado", choices=("auto", "izq", "der"), default="auto",
                   help="lado del cuerpo que mira a la cámara")
    p.add_argument("--cuadros-por-segundo", type=float, default=10.0)
    p.add_argument("--carga-kg", type=float, default=0.0, help="peso que manipula (lo informa el evaluador)")
    p.add_argument("--golpes", action="store_true", help="fuerza brusca o golpes al manipular")
    p.add_argument("--agarre", choices=tuple(AGARRES), default="bueno")
    p.add_argument("--un-pie", action="store_true", help="trabaja apoyado en un solo pie")
    p.add_argument("--puesto", default="", help="nombre del puesto, para el informe")
    p.add_argument("--video-esqueleto", action="store_true",
                   help="guardar un video con sólo el esqueleto sobre fondo negro")
    p.add_argument("--video-superpuesto", action="store_true",
                   help="guardar el video original con el esqueleto y el REBA encima (muestra a la persona)")
    args = p.parse_args(argv)

    video = Path(args.video)
    salida = Path(args.salida or f"{video.with_suffix('')}_ergonomia")
    salida.mkdir(parents=True, exist_ok=True)

    condiciones = Condiciones(args.carga_kg, args.golpes, args.agarre, args.un_pie)
    modelo = crear_modelo(args.modelo, args.ruta_modelo)
    try:
        cuadros, fps, lado = analizar_video(
            str(video), modelo, args.lado, args.cuadros_por_segundo, condiciones,
            str(salida / "esqueleto.mp4") if args.video_esqueleto else None,
            str(salida / "superpuesto.mp4") if args.video_superpuesto else None,
        )
    finally:
        modelo.cerrar()
    datos = {"Video": video.name, "Puesto": args.puesto or "(sin nombre)",
             "Modelo de pose": args.modelo, "Lado analizado": lado or "sin persona detectada"}
    informe = armar_informe(cuadros, fps, condiciones, datos)

    (salida / "informe.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
    texto = informe_markdown(informe)
    (salida / "informe.md").write_text(texto, encoding="utf-8")
    if not guardar_pdf(texto, salida / "informe.pdf"):
        print("Para tener el informe en PDF: pip install fpdf2")
    guardar_csv(cuadros, salida / "angulos.csv")
    print(f"Informe en {salida / 'informe.md'}")


if __name__ == "__main__":
    main()
