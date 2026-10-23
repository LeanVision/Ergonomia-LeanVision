"""Cuánto error meten el ángulo de cámara, la distancia y el ruido de pose.

El README dice "filmar de costado" y los controles de calidad descartan los
cuadros que no lo están, pero ninguna de las dos cosas tenía un número detrás.
Esto lo pone: arma un esqueleto 3D con ángulos CONOCIDOS, lo proyecta como lo
vería una cámara ubicada a tantos grados de la perpendicular, y mide lo que
`calcular_angulos` devuelve contra la verdad.

No necesita cámara, video ni modelo de pose: la verdad es el esqueleto 3D, así
que el error que sale es el del método, no el de ningún detector.

    python -m ergonomia.sensibilidad
    python -m ergonomia.sensibilidad --csv sensibilidad.csv

Convención de ejes (persona de pie mirando hacia +Y):

    X = lado a lado (hombro izquierdo a derecho)
    Y = hacia adelante (hacia donde mira)
    Z = hacia arriba

Azimut 0° es la cámara perpendicular, de perfil puro; 90° es de frente.
"""

from __future__ import annotations

import argparse
import csv
import math
import random

from .esqueleto import Esqueleto, calcular_angulos
from .informe import SEPARACION_HOMBROS_PERFIL, de_perfil

# Proporciones en metros de una persona de 1,75 m. No hace falta que sean
# exactas: los ángulos son invariantes a la escala, y el ancho de hombros sólo
# entra en el control de perfil.
LARGO_TRONCO = 0.50
ANCHO_HOMBROS = 0.38
ANCHO_CADERAS = 0.30
LARGO_CUELLO = 0.22
LARGO_MUSLO = 0.45
LARGO_PIERNA = 0.45
LARGO_BRAZO = 0.32
LARGO_ANTEBRAZO = 0.27

# Más de esto en el tronco puede mover de banda REBA (20° y 60°) y cambiar lo
# que se informa en la planilla.
ERROR_TOLERABLE = 2.0


def esqueleto_3d(tronco: float = 0.0, cuello: float = 0.0, brazo: float = 0.0,
                 rodilla: float = 0.0) -> dict[str, tuple[float, float, float]]:
    """Puntos 3D de una persona con los ángulos pedidos, en grados.

    Todas las flexiones ocurren en el plano sagital, que es el caso que la
    medición 2D puede resolver bien cuando la cámara está de costado.
    """
    def adelante_arriba(grados: float) -> tuple[float, float, float]:
        radianes = math.radians(grados)
        return (0.0, math.sin(radianes), math.cos(radianes))

    cadera = (0.0, 0.0, 0.0)
    dir_tronco = adelante_arriba(tronco)
    hombro = tuple(cadera[i] + dir_tronco[i] * LARGO_TRONCO for i in range(3))
    # El cuello se mide respecto del tronco, así que su flexión se suma.
    dir_cuello = adelante_arriba(tronco + cuello)
    oreja = tuple(hombro[i] + dir_cuello[i] * LARGO_CUELLO for i in range(3))
    nariz = (oreja[0], oreja[1] + 0.10, oreja[2])
    # El brazo cuelga a lo largo del tronco y se eleva hacia adelante.
    dir_brazo = adelante_arriba(tronco + 180.0 - brazo)
    codo = tuple(hombro[i] + dir_brazo[i] * LARGO_BRAZO for i in range(3))
    munieca = tuple(codo[i] + dir_brazo[i] * LARGO_ANTEBRAZO for i in range(3))
    knee_dir = adelante_arriba(180.0)
    rodilla_p = tuple(cadera[i] + knee_dir[i] * LARGO_MUSLO for i in range(3))
    dir_pierna = adelante_arriba(180.0 - rodilla)
    tobillo = tuple(rodilla_p[i] + dir_pierna[i] * LARGO_PIERNA for i in range(3))

    puntos: dict[str, tuple[float, float, float]] = {"nariz": nariz}
    for nombre, centro, ancho in (
        ("hombro", hombro, ANCHO_HOMBROS), ("cadera", cadera, ANCHO_CADERAS),
        ("oreja", oreja, 0.16), ("codo", codo, ANCHO_HOMBROS),
        ("muneca", munieca, ANCHO_HOMBROS), ("rodilla", rodilla_p, ANCHO_CADERAS),
        ("tobillo", tobillo, ANCHO_CADERAS),
    ):
        for lado, signo in (("izq", -1.0), ("der", 1.0)):
            puntos[f"{nombre}_{lado}"] = (centro[0] + signo * ancho / 2, centro[1], centro[2])
    return puntos


def proyectar(puntos: dict, azimut: float, px_por_metro: float = 400.0,
              ruido_px: float = 0.0, semilla: int = 0) -> Esqueleto:
    """Proyecta los puntos 3D como los vería una cámara a `azimut` grados.

    Proyección ortográfica: alcanza para el error de ángulo, y evita mezclar el
    efecto de la perspectiva con el que se quiere medir.
    """
    radianes = math.radians(azimut)
    # Vector "derecha" de la imagen para una cámara que mira a la persona desde
    # la dirección (cos azimut, sin azimut, 0).
    derecha = (math.sin(radianes), -math.cos(radianes), 0.0)
    azar = random.Random(semilla)

    salida: dict[str, tuple[float, float, float]] = {}
    for nombre, p in puntos.items():
        x = (p[0] * derecha[0] + p[1] * derecha[1]) * px_por_metro
        y = -p[2] * px_por_metro
        if ruido_px:
            x += azar.gauss(0.0, ruido_px)
            y += azar.gauss(0.0, ruido_px)
        # Se centra en una imagen imaginaria; sólo importan las distancias.
        salida[nombre] = (500.0 + x, 900.0 + y, 0.9)
    return Esqueleto(salida)


def _error(medido: float | None, verdadero: float) -> float | None:
    return None if medido is None else abs(medido - verdadero)


def barrido_de_azimut(tronco: float = 60.0, cuello: float = 20.0,
                      hasta: int = 60, paso: int = 5) -> list[dict]:
    """Error de tronco y cuello según cuánto se aparta la cámara del perfil."""
    filas = []
    for azimut in range(0, hasta + 1, paso):
        esqueleto = proyectar(esqueleto_3d(tronco=tronco, cuello=cuello), azimut)
        angulos = calcular_angulos(esqueleto, "der")
        filas.append({
            "azimut": azimut,
            "tronco_real": tronco,
            "tronco_medido": None if angulos.tronco is None else round(angulos.tronco, 1),
            "error_tronco": _error(angulos.tronco, tronco),
            "error_cuello": _error(angulos.cuello, cuello),
            "separacion_hombros": (None if angulos.separacion_hombros is None
                                   else round(angulos.separacion_hombros, 2)),
            "pasa_control_de_perfil": de_perfil(angulos),
        })
    return filas


def barrido_de_tamano(tronco: float = 60.0, ruido_px: float = 1.5,
                      repeticiones: int = 40) -> list[dict]:
    """Error según cuántos píxeles ocupa el tronco, con ruido de pose fijo.

    Es el otro límite de la toma: de costado pero lejos, el ruido del detector
    pesa cada vez más respecto del largo del segmento.
    """
    filas = []
    for alto_tronco_px in (20, 30, 40, 60, 80, 120, 200):
        px_por_metro = alto_tronco_px / LARGO_TRONCO
        errores = []
        for semilla in range(repeticiones):
            esqueleto = proyectar(esqueleto_3d(tronco=tronco), 0.0, px_por_metro,
                                  ruido_px, semilla)
            error = _error(calcular_angulos(esqueleto, "der").tronco, tronco)
            if error is not None:
                errores.append(error)
        filas.append({
            "tronco_px": alto_tronco_px,
            # Con una imagen de 1080 de alto, el control de calidad del repo
            # exige que el tronco ocupe al menos el 6 % (unos 65 px).
            "porcentaje_de_1080": round(100 * alto_tronco_px / 1080, 1),
            "error_promedio": round(sum(errores) / len(errores), 2) if errores else None,
            "error_max": round(max(errores), 2) if errores else None,
        })
    return filas


def _primer_azimut_malo(filas: list[dict]) -> int | None:
    for fila in filas:
        if fila["error_tronco"] is not None and fila["error_tronco"] > ERROR_TOLERABLE:
            return fila["azimut"]
    return None


def _primer_azimut_rechazado(filas: list[dict]) -> int | None:
    for fila in filas:
        if not fila["pasa_control_de_perfil"]:
            return fila["azimut"]
    return None


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tronco", type=float, default=60.0, help="flexión real de tronco a simular")
    p.add_argument("--cuello", type=float, default=20.0)
    p.add_argument("--ruido-px", type=float, default=1.5, help="ruido del detector de pose")
    p.add_argument("--csv", default=None, help="archivo donde guardar las tablas")
    args = p.parse_args(argv)

    azimut = barrido_de_azimut(args.tronco, args.cuello)
    print(f"Tronco real {args.tronco:.0f}°, cuello real {args.cuello:.0f}°.")
    print(f"\n{'azimut':>7} {'tronco':>8} {'err':>6} {'err cuello':>11} "
          f"{'separacion':>11} {'pasa control':>13}")
    for fila in azimut:
        print(f"{fila['azimut']:>6}° {fila['tronco_medido']:>8} "
              f"{round(fila['error_tronco'], 1):>6} {round(fila['error_cuello'], 1):>11} "
              f"{fila['separacion_hombros']:>11} {str(fila['pasa_control_de_perfil']):>13}")

    malo = _primer_azimut_malo(azimut)
    rechazado = _primer_azimut_rechazado(azimut)
    print()
    print(f"El error de tronco pasa los {ERROR_TOLERABLE:.0f}° a partir de los {malo}° de azimut.")
    print(f"El control de perfil (separación > {SEPARACION_HOMBROS_PERFIL}) "
          f"empieza a rechazar a los {rechazado}°.")
    if malo is not None and rechazado is not None and rechazado > malo:
        print(f"OJO: entre {malo}° y {rechazado}° el cuadro se acepta con un error "
              "que puede mover de banda REBA.")

    print(f"\nCon la cámara de perfil y ruido de pose de {args.ruido_px} px:")
    print(f"\n{'tronco px':>10} {'% de 1080':>10} {'err medio':>10} {'err max':>9}")
    for fila in barrido_de_tamano(args.tronco, args.ruido_px):
        print(f"{fila['tronco_px']:>10} {fila['porcentaje_de_1080']:>10} "
              f"{fila['error_promedio']:>10} {fila['error_max']:>9}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as archivo:
            escritor = csv.DictWriter(archivo, fieldnames=list(azimut[0]))
            escritor.writeheader()
            escritor.writerows(azimut)
        print(f"\nTabla de azimut en {args.csv}")


if __name__ == "__main__":
    main()
