"""Compara modelos de pose sobre el mismo video, para elegir con números.

La pregunta no es sólo cuál corre más rápido. Un modelo más veloz que mueve el
ángulo de tronco cinco grados no sirve: los umbrales de la 2.F están en 20° y
60°, y los de la 2.A en 30°. Por eso esto mide tres cosas por modelo:

1. Cuánto tarda por cuadro (mediana y p95).
2. Cuántos cuadros sobreviven a los controles de calidad.
3. Cuánto se corren los ángulos respecto del primer modelo, que se toma como
   referencia.

En una Raspberry anota además la temperatura del SoC antes y después, que es
el límite real cuando el proceso queda corriendo todo el turno.

Uso típico (exporta a NCNN y compara contra PyTorch):

    python -m ergonomia.comparar video.mp4 --pytorch yolo11n-pose.pt --exportar-ncnn

O con los dos modelos ya en disco:

    python -m ergonomia.comparar video.mp4 \\
        --pytorch yolo11n-pose.pt --ncnn yolo11n-pose_ncnn_model --cuadros 300

Cada modelo lee el video de nuevo en vez de guardar los cuadros en memoria: en
una Pi de 1 GB, trescientos cuadros de 1080p no entran.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import cv2

from .esqueleto import medir_cuadro
from .pose import crear_modelo

# El desplazamiento de los puntos se mide en largos de torso, no en píxeles,
# para que no dependa de cuán cerca esté la persona de la cámara.
_PUNTOS_COMPARADOS = ("hombro", "cadera", "codo", "muneca", "rodilla", "tobillo")


def temperatura_soc() -> float | None:
    """Grados del SoC, o None fuera de Linux. Sólo diagnóstico."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", encoding="utf-8") as archivo:
            return int(archivo.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def memoria_rss_mb() -> float | None:
    """RSS del proceso en MB, o None si no se puede leer."""
    try:
        import resource
        import sys

        pico = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux informa en KB; macOS en bytes.
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        return pico / divisor
    except ImportError:
        try:
            with open("/proc/self/statm", encoding="utf-8") as archivo:
                paginas = int(archivo.read().split()[1])
            return paginas * 4096 / (1024 * 1024)
        except OSError:
            return None


def _cuadros(ruta: str, cuantos: int, paso: int):
    """Entrega hasta `cuantos` cuadros del video, saltando de a `paso`."""
    captura = cv2.VideoCapture(ruta)
    if not captura.isOpened():
        raise SystemExit(f"No se pudo abrir el video: {ruta}")
    indice = entregados = 0
    try:
        while entregados < cuantos:
            ok, imagen = captura.read()
            if not ok:
                break
            if indice % paso == 0:
                entregados += 1
                # El índice real del cuadro en el video, no el de entrega: es
                # lo que permite cruzar con keypoints grabados aunque se
                # saltee de a varios.
                yield indice, imagen
            indice += 1
    finally:
        captura.release()


def medir_modelo(nombre: str, modelo, video: str, cuantos: int, paso: int,
                 lado: str | None = None) -> dict:
    """Corre un modelo sobre el video y devuelve tiempos y mediciones."""
    tiempos: list[float] = []
    mediciones: list = []
    temperatura_inicial = temperatura_soc()

    for indice, imagen in _cuadros(video, cuantos, paso):
        alto, ancho = imagen.shape[:2]
        arranque = time.perf_counter()
        esqueleto = modelo.estimar(imagen, indice * 100)
        tiempos.append((time.perf_counter() - arranque) * 1000)
        medida = medir_cuadro(esqueleto, alto, ancho, lado)
        lado = medida.lado
        mediciones.append(medida)

    if not tiempos:
        raise SystemExit(f"El video no dio ningún cuadro para {nombre}.")
    medibles = [m for m in mediciones if m.angulos is not None and m.angulos.tronco is not None]
    return {
        "modelo": nombre,
        "cuadros": len(tiempos),
        "ms_mediana": round(statistics.median(tiempos), 1),
        "ms_p95": round(sorted(tiempos)[int(len(tiempos) * 0.95) - 1], 1),
        "cuadros_por_segundo": round(1000 / statistics.median(tiempos), 1),
        "con_persona": sum(1 for m in mediciones if m.esqueleto is not None),
        "medibles": len(medibles),
        "lado": lado,
        "temperatura_soc_inicial": temperatura_inicial,
        "temperatura_soc_final": temperatura_soc(),
        "rss_mb": memoria_rss_mb(),
        "_mediciones": mediciones,
    }


def comparar_mediciones(referencia: list, otras: list) -> dict:
    """Cuánto se mueven los ángulos y los puntos entre dos modelos.

    Es el número que decide: si el tronco se corre menos de un grado, el modelo
    más rápido es gratis; si se corre cinco, puede cambiar de banda REBA y
    cambiar lo que se informa en la planilla.
    """
    diferencias: dict[str, list[float]] = {"tronco": [], "cuello": [], "brazo": [], "rodilla": []}
    desplazamientos: list[float] = []
    ambos = 0

    for a, b in zip(referencia, otras):
        if a.angulos is None or b.angulos is None:
            continue
        ambos += 1
        for campo in diferencias:
            va, vb = getattr(a.angulos, campo), getattr(b.angulos, campo)
            if va is not None and vb is not None:
                diferencias[campo].append(abs(va - vb))
        if a.esqueleto is None or b.esqueleto is None:
            continue
        torso = _largo_torso(a.esqueleto)
        if not torso:
            continue
        for nombre in _PUNTOS_COMPARADOS:
            for sufijo in ("_izq", "_der"):
                pa, pb = a.esqueleto.punto(nombre + sufijo), b.esqueleto.punto(nombre + sufijo)
                if pa and pb:
                    desplazamientos.append(((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2) ** 0.5 / torso)

    def resumen(valores: list[float]) -> dict | None:
        if not valores:
            return None
        return {"mediana": round(statistics.median(valores), 2),
                "p95": round(sorted(valores)[int(len(valores) * 0.95) - 1], 2),
                "max": round(max(valores), 2)}

    return {
        "cuadros_comparables": ambos,
        "grados": {campo: resumen(v) for campo, v in diferencias.items()},
        "puntos_en_largos_de_torso": resumen(desplazamientos),
    }


def _largo_torso(esqueleto) -> float | None:
    hombro = esqueleto.punto("hombro_izq") or esqueleto.punto("hombro_der")
    cadera = esqueleto.punto("cadera_izq") or esqueleto.punto("cadera_der")
    if not hombro or not cadera:
        return None
    largo = ((hombro[0] - cadera[0]) ** 2 + (hombro[1] - cadera[1]) ** 2) ** 0.5
    return largo or None


def exportar_ncnn(pesos: str) -> str:
    """Exporta el .pt a NCNN y devuelve la carpeta resultante."""
    try:
        from ultralytics import YOLO
    except ImportError:
        raise SystemExit("Para exportar hace falta ultralytics: pip install ultralytics")
    print(f"Exportando {pesos} a NCNN (tarda un rato la primera vez)...")
    salida = YOLO(pesos).export(format="ncnn")
    print(f"Exportado en {salida}")
    return str(salida)


def _imprimir(resultados: list[dict], comparaciones: dict) -> None:
    print()
    print(f"{'modelo':<12} {'ms/cuadro':>10} {'p95':>8} {'cuadros/s':>10} "
          f"{'medibles':>9} {'RSS MB':>8} {'SoC °C':>8}")
    for r in resultados:
        temperatura = r["temperatura_soc_final"]
        print(f"{r['modelo']:<12} {r['ms_mediana']:>10} {r['ms_p95']:>8} "
              f"{r['cuadros_por_segundo']:>10} {r['medibles']:>5}/{r['cuadros']:<3} "
              f"{(round(r['rss_mb']) if r['rss_mb'] else '-'):>8} "
              f"{(round(temperatura, 1) if temperatura else '-'):>8}")

    for nombre, comparacion in comparaciones.items():
        tronco = comparacion["grados"].get("tronco")
        puntos = comparacion["puntos_en_largos_de_torso"]
        print()
        print(f"{nombre} contra la referencia, sobre {comparacion['cuadros_comparables']} cuadros:")
        if tronco:
            print(f"  tronco: mediana {tronco['mediana']}°, p95 {tronco['p95']}°, máx {tronco['max']}°")
        if puntos:
            print(f"  puntos: mediana {puntos['mediana']} largos de torso, p95 {puntos['p95']}")
        if tronco and tronco["p95"] <= 2.0:
            print("  -> el p95 del tronco no llega a 2°: no cambia lo que se informa.")
        elif tronco:
            print("  -> OJO: el tronco se corre más de 2°; revisar antes de cambiar de modelo.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video")
    p.add_argument("--pytorch", default=None, help="pesos .pt de YOLO-pose")
    p.add_argument("--ncnn", default=None, help="carpeta del modelo exportado a NCNN")
    p.add_argument("--mediapipe", default=None, help="archivo .task de MediaPipe")
    p.add_argument("--grabada", default=None,
                   help="keypoints.jsonl de ergonomia.capturar_imx500 (lo que midió la IMX500)")
    p.add_argument("--exportar-ncnn", action="store_true",
                   help="exportar primero el .pt a NCNN y comparar contra él")
    p.add_argument("--cuadros", type=int, default=200, help="cuántos cuadros medir")
    p.add_argument("--paso", type=int, default=1, help="procesar uno de cada N cuadros del video")
    p.add_argument("--salida", default=None, help="archivo JSON con los números crudos")
    args = p.parse_args(argv)

    ncnn = args.ncnn
    if args.exportar_ncnn:
        if not args.pytorch:
            raise SystemExit("--exportar-ncnn necesita --pytorch con el archivo .pt de origen.")
        ncnn = exportar_ncnn(args.pytorch)

    # El primero que se pida es la referencia contra la que se miden los demás.
    # La grabada va última a propósito: lo que se quiere leer es cuánto se
    # desvía el sensor del modelo de referencia, no al revés.
    pedidos = [("pytorch", "yolo", args.pytorch), ("ncnn", "yolo", ncnn),
               ("mediapipe", "mediapipe", args.mediapipe),
               ("imx500", "grabada", args.grabada)]
    pedidos = [(nombre, tipo, ruta) for nombre, tipo, ruta in pedidos if ruta]
    if not pedidos:
        raise SystemExit("Indicar al menos un modelo: --pytorch, --ncnn o --mediapipe.")

    resultados = []
    for nombre, tipo, ruta in pedidos:
        print(f"Midiendo {nombre} ({ruta})...")
        modelo = crear_modelo(tipo, ruta)
        try:
            resultados.append(medir_modelo(nombre, modelo, args.video, args.cuadros, args.paso))
        finally:
            modelo.cerrar()

    referencia = resultados[0]
    comparaciones = {
        r["modelo"]: comparar_mediciones(referencia["_mediciones"], r["_mediciones"])
        for r in resultados[1:]
    }
    _imprimir(resultados, comparaciones)

    if args.salida:
        crudo = {
            "video": args.video,
            "referencia": referencia["modelo"],
            "modelos": [{k: v for k, v in r.items() if k != "_mediciones"} for r in resultados],
            "comparaciones": comparaciones,
        }
        Path(args.salida).write_text(json.dumps(crudo, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nNúmeros crudos en {args.salida}")


if __name__ == "__main__":
    main()
