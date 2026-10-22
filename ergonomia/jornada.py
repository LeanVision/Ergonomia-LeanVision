"""Mide la jornada completa de un puesto y arma el borrador de la planilla 2.D.

`ergonomia.analizar` analiza un video de unos minutos. Esto es lo otro: queda
mirando el puesto toda la jornada y deja en disco un resumen por minuto, que es
lo único que la 2.D permite contestar (ver `bipedestacion.py`).

    # Mientras dura el turno:
    python -m ergonomia.jornada --fuente rtsp://camara/stream \\
        --puesto "Embalaje linea 2" --ancho-escena 4

    # Después, el borrador del día:
    python -m ergonomia.jornada --informe --puesto "Embalaje linea 2" \\
        --carga-kg 8 --fecha 2026-10-22

No se guarda ni video ni cuadros: de cada cuadro sale si la persona estaba de
pie y cuánto se movió, y a disco van doce números por minuto. Una jornada de
8 h son 480 líneas.
"""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import cv2

from .bipedestacion import CORTE_MINIMO_S, Minuto, planilla_2d, resumen_markdown, resumir_minuto
from .esqueleto import medir_cuadro
from .informe import Cuadro
from .pose import crear_modelo

# Dos cuadros por segundo. La 2.D habla de horas seguidas, así que no hace
# falta más, y a esa frecuencia la pose entra en una Pi 5 sin pelear con nada.
CUADROS_POR_SEGUNDO = 2.0


def slug(nombre: str) -> str:
    """Normaliza un nombre a [a-z0-9_]. Devuelve "" si no queda nada.

    Que no sobreviva ningún carácter raro es lo que hace seguro armar una ruta
    con esto: un puesto llamado "../../etc" queda en "etc".
    """
    sin_acentos = "".join(
        c for c in unicodedata.normalize("NFD", nombre) if unicodedata.category(c) != "Mn"
    )
    base = "".join(c if c.isalnum() else "_" for c in sin_acentos.lower())
    while "__" in base:
        base = base.replace("__", "_")
    return base.strip("_")


def ruta_del_dia(carpeta: Path, puesto: str, fecha: str) -> Path:
    """<carpeta>/<puesto>/<AAAA-MM-DD>.jsonl, una línea por minuto.

    Append puro: aguanta un corte de luz perdiendo a lo sumo la última línea, y
    cuesta una escritura por minuto, que es lo que la SD de una Pi sostiene
    todo el día.

    La fecha es local y no UTC: el archivo tiene que coincidir con la jornada
    que declara el servicio de Higiene y Seguridad, no con el meridiano.
    """
    return carpeta / (slug(puesto) or "sin_nombre") / f"{fecha}.jsonl"


def guardar_minuto(ruta: Path, minuto: Minuto) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fila = asdict(minuto)
    for clave in ("segundos_de_pie", "segundos_sentado", "metros"):
        fila[clave] = round(fila[clave], 2)
    with open(ruta, "a", encoding="utf-8") as archivo:
        archivo.write(json.dumps(fila, ensure_ascii=False) + "\n")


def cargar_minutos(ruta: Path) -> list[Minuto]:
    """Relee el día. Rellena los huecos y descarta las líneas rotas.

    Los huecos importan: si el proceso estuvo caído tres horas, en el archivo
    hay dos bloques de minutos pegados, y leerlos de corrido haría pasar por
    bipedestación continua lo que nadie midió. Cada minuto faltante entra como
    un minuto sin mediciones, que corta la corrida.
    """
    if not ruta.exists():
        return []
    leidos: list[Minuto] = []
    with open(ruta, "r", encoding="utf-8") as archivo:
        for numero, linea in enumerate(archivo, start=1):
            linea = linea.strip()
            if not linea:
                continue
            try:
                leidos.append(Minuto(**json.loads(linea)))
            except (json.JSONDecodeError, TypeError) as error:
                print(f"Línea {numero} de {ruta} ilegible: {error}")
    leidos.sort(key=lambda m: m.inicio)

    completos: list[Minuto] = []
    for minuto in leidos:
        if completos:
            faltantes = int((minuto.inicio - completos[-1].inicio) // 60) - 1
            # Un tope por si el archivo junta dos días: no hace falta
            # materializar miles de minutos vacíos para cortar una corrida.
            for i in range(min(faltantes, 1000)):
                completos.append(Minuto(inicio=completos[-1].inicio + 60 * (i + 1)))
        completos.append(minuto)
    return completos


def en_vivo(fuente: str) -> bool:
    """Si la fuente es una cámara o un stream, en vez de un archivo de video.

    Cambia de dónde sale el tiempo de cada cuadro: en vivo, del reloj; en un
    archivo, de la posición dentro del video, así un turno grabado se puede
    reprocesar y da los mismos minutos que dio en su momento.
    """
    return fuente.isdigit() or "://" in fuente


def cuadros_de(fuente: str, cuadros_por_segundo: float = CUADROS_POR_SEGUNDO,
               horas: float | None = None, desde: float | None = None):
    """Va largando (epoch, imagen) al ritmo pedido."""
    captura = cv2.VideoCapture(int(fuente) if fuente.isdigit() else fuente)
    if not captura.isOpened():
        raise SystemExit(f"No se pudo abrir la fuente: {fuente}")
    arranque = time.time() if desde is None else desde
    try:
        if en_vivo(fuente):
            yield from _cuadros_en_vivo(captura, cuadros_por_segundo, horas, arranque)
        else:
            yield from _cuadros_de_archivo(captura, cuadros_por_segundo, horas, arranque)
    finally:
        captura.release()


def _cuadros_en_vivo(captura, cuadros_por_segundo: float, horas: float | None, arranque: float):
    """Lee todos los cuadros y entrega sólo los que toca.

    Dejar de leer llena el buffer de la cámara y lo que llega después queda
    viejo, así que se descarta leyendo, no esperando.
    """
    periodo = 1.0 / cuadros_por_segundo if cuadros_por_segundo > 0 else 0.0
    proximo = 0.0
    while True:
        ok, imagen = captura.read()
        if not ok:
            break
        ahora = time.time()
        if horas is not None and ahora - arranque >= horas * 3600:
            break
        if ahora < proximo:
            continue
        proximo = max(ahora, proximo) + periodo
        yield ahora, imagen


def _cuadros_de_archivo(captura, cuadros_por_segundo: float, horas: float | None, arranque: float):
    """Saltea cuadros del archivo para llegar al ritmo pedido, como analizar.py."""
    fps_video = captura.get(cv2.CAP_PROP_FPS) or 30.0
    paso = max(1, int(round(fps_video / cuadros_por_segundo))) if cuadros_por_segundo > 0 else 1
    indice = 0
    while True:
        ok, imagen = captura.read()
        if not ok:
            break
        segundos = indice / fps_video
        indice += 1
        if horas is not None and segundos >= horas * 3600:
            break
        if (indice - 1) % paso:
            continue
        yield arranque + segundos, imagen


def medir_jornada(muestras, modelo, cuadros_por_segundo: float = CUADROS_POR_SEGUNDO,
                  ancho_escena_m: float = 0.0, lado: str | None = None,
                  al_cerrar_minuto=None) -> list[Minuto]:
    """Consume (epoch, imagen) y devuelve un Minuto por cada minuto de reloj.

    `al_cerrar_minuto` se llama con cada minuto en cuanto se cierra, para
    escribirlo a disco sin esperar al final del turno.
    """
    minutos: list[Minuto] = []
    bloque_actual: int | None = None
    inicio_bloque = 0.0
    cuadros: list[Cuadro] = []

    def cerrar() -> None:
        if not cuadros:
            return
        minuto = resumir_minuto(inicio_bloque, cuadros, cuadros_por_segundo, ancho_escena_m)
        minutos.append(minuto)
        if al_cerrar_minuto is not None:
            al_cerrar_minuto(minuto)
        cuadros.clear()

    for ts, imagen in muestras:
        alto, ancho = imagen.shape[:2]
        medida = medir_cuadro(modelo.estimar(imagen, int(ts * 1000)), alto, ancho, lado)
        lado = medida.lado
        bloque = int(ts // 60)
        if bloque != bloque_actual:
            cerrar()
            bloque_actual, inicio_bloque = bloque, ts
        cuadros.append(Cuadro(ts, medida.angulos, medida.centro, medida.cuerpo_entero))
    cerrar()
    return minutos


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--puesto", required=True, help="nombre del puesto: define la carpeta y el informe")
    p.add_argument("--salida", default="jornada", help="carpeta de los resúmenes por minuto")
    p.add_argument("--informe", action="store_true", help="no mide: lee el día y escribe el borrador")
    p.add_argument("--fecha", default=None, help="día a informar (AAAA-MM-DD; por defecto, hoy)")
    p.add_argument("--fuente", default="0", help="cámara (0, 1...), URL RTSP o archivo de video")
    p.add_argument("--cuadros-por-segundo", type=float, default=CUADROS_POR_SEGUNDO)
    p.add_argument("--horas", type=float, default=None, help="cortar la medición después de N horas")
    p.add_argument("--ancho-escena", type=float, default=0.0,
                   help="metros reales que abarca el ancho de la imagen; sin esto no hay deambulación")
    p.add_argument("--modelo", choices=("yolo", "mediapipe"), default="yolo")
    p.add_argument("--ruta-modelo", default=None, help="pesos YOLO (.pt) o archivo .task de MediaPipe")
    p.add_argument("--lado", choices=("auto", "izq", "der"), default="auto",
                   help="lado del cuerpo que mira a la cámara")
    p.add_argument("--carga-kg", type=float, default=0.0,
                   help="peso que manipula de forma habitual (lo informa el evaluador)")
    p.add_argument("--hay-silla", action="store_true",
                   help="el puesto tiene asiento y el trabajador puede usarlo (lo informa el evaluador)")
    p.add_argument("--corte-minimo", type=float, default=CORTE_MINIMO_S,
                   help="pausa en segundos que no corta una corrida de bipedestación")
    args = p.parse_args(argv)

    fecha = args.fecha or datetime.now().strftime("%Y-%m-%d")
    ruta = ruta_del_dia(Path(args.salida), args.puesto, fecha)

    if not args.informe:
        modelo = crear_modelo(args.modelo, args.ruta_modelo)
        lado = None if args.lado == "auto" else args.lado
        print(f"Midiendo {args.puesto} en {ruta}. Ctrl-C para cortar.")
        try:
            medir_jornada(
                cuadros_de(args.fuente, args.cuadros_por_segundo, args.horas), modelo,
                args.cuadros_por_segundo, args.ancho_escena, lado,
                al_cerrar_minuto=lambda m: guardar_minuto(ruta, m),
            )
        except KeyboardInterrupt:
            print("Medición cortada.")
        finally:
            modelo.cerrar()

    minutos = cargar_minutos(ruta)
    if not minutos:
        raise SystemExit(f"Sin minutos registrados en {ruta}.")
    planilla = planilla_2d(minutos, args.hay_silla, args.carga_kg, args.corte_minimo)
    datos = {"Puesto": args.puesto, "Fecha": fecha, **planilla}
    ruta.with_suffix(".json").write_text(
        json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    texto = resumen_markdown(planilla)
    ruta.with_suffix(".md").write_text(
        f"# Bipedestación — {args.puesto} — {fecha}\n\n{texto}\n", encoding="utf-8"
    )
    print(texto)
    print(f"\nBorrador en {ruta.with_suffix('.md')}")


if __name__ == "__main__":
    main()
