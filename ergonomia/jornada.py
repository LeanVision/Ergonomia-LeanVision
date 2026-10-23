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

`--informe` deja, al lado del .jsonl del día, el borrador en .md, .json y .pdf
(el PDF necesita fpdf2), con los enunciados del formulario del Anexo I.

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
from .pdf import guardar_pdf
from .pose import crear_modelo

# Dos cuadros por segundo. La 2.D habla de horas seguidas, así que no hace
# falta más, y a esa frecuencia la pose entra en una Pi 5 sin pelear con nada.
CUADROS_POR_SEGUNDO = 2.0

# Modo mixto: uno de cada cuántos cuadros se reprocesa con el modelo de
# control. Cincuenta es 2 % de carga extra, que una Pi absorbe sin despeinarse.
CONTROL_CADA = 50
# A partir de acá el desvío del tronco puede mover de banda REBA (20° y 60°) y
# cambiar lo que se informa, así que el borrador avisa.
DESVIO_TOLERABLE_GRADOS = 2.0


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
                  al_cerrar_minuto=None, modelo_control=None,
                  control_cada: int = CONTROL_CADA) -> list[Minuto]:
    """Consume (epoch, imagen) y devuelve un Minuto por cada minuto de reloj.

    `al_cerrar_minuto` se llama con cada minuto en cuanto se cierra, para
    escribirlo a disco sin esperar al final del turno.

    Con `modelo_control`, uno de cada `control_cada` cuadros se mide también
    con ese segundo modelo y se anota cuánto se corre el tronco. Es el modo
    mixto: la IMX500 mide todo el turno sin tocar la CPU, y el modelo de
    referencia reprocesa una fracción como control. Con uno de cada cincuenta,
    el costo es 2 % y alcanza para que el desvío no pase inadvertido.
    """
    minutos: list[Minuto] = []
    bloque_actual: int | None = None
    inicio_bloque = 0.0
    cuadros: list[Cuadro] = []
    desvios: list[float] = []

    def cerrar() -> None:
        if not cuadros:
            return
        minuto = resumir_minuto(inicio_bloque, cuadros, cuadros_por_segundo, ancho_escena_m)
        minuto.control_cuadros = len(desvios)
        minuto.control_tronco_suma = sum(desvios)
        minuto.control_tronco_max = max(desvios, default=0.0)
        minutos.append(minuto)
        if al_cerrar_minuto is not None:
            al_cerrar_minuto(minuto)
        cuadros.clear()
        desvios.clear()

    for indice, (ts, imagen) in enumerate(muestras):
        alto, ancho = imagen.shape[:2]
        medida = medir_cuadro(modelo.estimar(imagen, int(ts * 1000)), alto, ancho, lado)
        lado = medida.lado
        bloque = int(ts // 60)
        if bloque != bloque_actual:
            cerrar()
            bloque_actual, inicio_bloque = bloque, ts
        cuadros.append(Cuadro(ts, medida.angulos, medida.centro, medida.cuerpo_entero))

        if modelo_control is not None and control_cada > 0 and indice % control_cada == 0:
            control = medir_cuadro(modelo_control.estimar(imagen, int(ts * 1000)), alto, ancho, lado)
            # Sólo cuentan los cuadros donde los DOS midieron el tronco: si uno
            # no vio a la persona, la diferencia no significa nada.
            if (medida.angulos is not None and control.angulos is not None
                    and medida.angulos.tronco is not None and control.angulos.tronco is not None):
                desvios.append(abs(medida.angulos.tronco - control.angulos.tronco))
    cerrar()
    return minutos


def resumen_control(minutos: list[Minuto]) -> str | None:
    """Línea de control de calidad del modo mixto, o None si no se usó.

    El número que importa es el desvío máximo: un promedio bajo puede esconder
    un tramo en el que el modelo principal se fue de banda.
    """
    controlados = sum(m.control_cuadros for m in minutos)
    if not controlados:
        return None
    promedio = sum(m.control_tronco_suma for m in minutos) / controlados
    maximo = max(m.control_tronco_max for m in minutos)
    aviso = ("" if maximo <= DESVIO_TOLERABLE_GRADOS else
             f" **ATENCIÓN:** supera los {DESVIO_TOLERABLE_GRADOS:.0f}° y puede cambiar de banda; "
             "conviene medir ese puesto con el modelo de referencia.")
    return (f"- **Control cruzado:** {controlados} cuadros reprocesados con el modelo de "
            f"referencia; el tronco se corrió {promedio:.1f}° en promedio y {maximo:.1f}° "
            f"como máximo.{aviso}")


def _con_linea_de_control(texto: str, linea: str) -> str:
    """Mete la línea del control cruzado junto a las demás del encabezado.

    Va acá y no en `resumen_markdown` porque el control cruzado es una
    propiedad de cómo se midió, no de la planilla.
    """
    lineas = texto.splitlines()
    ultima_vineta = max((i for i, l in enumerate(lineas) if l.startswith("- ")), default=-1)
    if ultima_vineta < 0:
        return texto + chr(10) + linea
    lineas.insert(ultima_vineta + 1, linea)
    return chr(10).join(lineas)


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
    # Modo mixto: el modelo principal mide todos los cuadros (la IMX500 lo hace
    # sin tocar la CPU) y éste reprocesa una fracción como control cruzado.
    p.add_argument("--modelo-control", choices=("yolo", "mediapipe"), default=None,
                   help="segundo modelo para controlar uno de cada N cuadros")
    p.add_argument("--ruta-control", default=None, help="pesos o .task del modelo de control")
    p.add_argument("--control-cada", type=int, default=CONTROL_CADA,
                   help="cada cuántos cuadros correr el modelo de control")
    p.add_argument("--carga-kg", type=float, default=0.0,
                   help="peso que manipula de forma habitual (lo informa el evaluador)")
    p.add_argument("--hay-silla", action="store_true",
                   help="el puesto tiene asiento y el trabajador puede usarlo (lo informa el evaluador)")
    # Los dos ítems del Paso 2 que no salen de la cámara. Sin respuesta quedan
    # como "sin responder", y entonces el borrador no puede presumir tolerable:
    # es la diferencia entre un ítem contestado que no y uno que nadie miró.
    p.add_argument("--carga-termica", choices=("si", "no"), default=None,
                   help="temperatura y humedad fuera de los límites admisibles (termohigrómetro)")
    p.add_argument("--manifestacion-temprana", choices=("si", "no"), default=None,
                   help="el trabajador presenta síntomas de las enfermedades del Art. 1° "
                        "(lo informa el Servicio de Medicina del Trabajo)")
    p.add_argument("--corte-minimo", type=float, default=CORTE_MINIMO_S,
                   help="pausa en segundos que no corta una corrida de bipedestación")
    args = p.parse_args(argv)

    fecha = args.fecha or datetime.now().strftime("%Y-%m-%d")
    ruta = ruta_del_dia(Path(args.salida), args.puesto, fecha)

    if not args.informe:
        modelo = crear_modelo(args.modelo, args.ruta_modelo)
        control = crear_modelo(args.modelo_control, args.ruta_control) if args.modelo_control else None
        lado = None if args.lado == "auto" else args.lado
        print(f"Midiendo {args.puesto} en {ruta}. Ctrl-C para cortar.")
        if control is not None:
            print(f"Control cruzado con {args.modelo_control}, uno de cada {args.control_cada} cuadros.")
        try:
            medir_jornada(
                cuadros_de(args.fuente, args.cuadros_por_segundo, args.horas), modelo,
                args.cuadros_por_segundo, args.ancho_escena, lado,
                al_cerrar_minuto=lambda m: guardar_minuto(ruta, m),
                modelo_control=control, control_cada=args.control_cada,
            )
        except KeyboardInterrupt:
            print("Medición cortada.")
        finally:
            modelo.cerrar()
            if control is not None:
                control.cerrar()

    minutos = cargar_minutos(ruta)
    if not minutos:
        raise SystemExit(f"Sin minutos registrados en {ruta}.")
    def declarado(valor: str | None) -> bool | None:
        return None if valor is None else valor == "si"

    planilla = planilla_2d(minutos, args.hay_silla, args.carga_kg, args.corte_minimo,
                           declarado(args.carga_termica), declarado(args.manifestacion_temprana))
    controlados = sum(m.control_cuadros for m in minutos)
    datos = {
        "Puesto": args.puesto,
        "Fecha": fecha,
        **planilla,
        "control_cruzado": {
            "cuadros": controlados,
            "tronco_promedio": (round(sum(m.control_tronco_suma for m in minutos) / controlados, 2)
                                if controlados else None),
            "tronco_max": round(max((m.control_tronco_max for m in minutos), default=0.0), 2),
        },
    }
    ruta.with_suffix(".json").write_text(
        json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    texto = resumen_markdown(planilla)
    control = resumen_control(minutos)
    if control:
        texto = _con_linea_de_control(texto, control)
    documento = f"# Bipedestación — {args.puesto} — {fecha}\n\n{texto}\n"
    ruta.with_suffix(".md").write_text(documento, encoding="utf-8")
    # El PDF sale del mismo Markdown que el .md, igual que en analizar.py: el
    # borrador se adjunta a la planilla firmada, y para eso tiene que imprimirse.
    hay_pdf = guardar_pdf(documento, ruta.with_suffix(".pdf"))
    if not hay_pdf:
        print("Para tener el borrador en PDF: pip install fpdf2")
    print(texto)
    print(f"\nBorrador en {ruta.with_suffix('.md')}, .json" + (" y .pdf" if hay_pdf else ""))


if __name__ == "__main__":
    main()
