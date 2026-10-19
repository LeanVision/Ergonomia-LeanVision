"""Técnica al levantar o bajar cargas del piso (planilla 2.A).

Busca los momentos en que las manos bajan cerca del piso (episodios de
alcance bajo) y, en el punto más bajo de cada uno, mira cómo se agachó la
persona: doblando la espalda con las piernas casi rectas ("con la espalda",
la técnica que hay que corregir), flexionando las rodillas con el tronco
más derecho ("con las piernas"), o algo intermedio ("mixta").

REBA no separa bien estas técnicas: castiga tanto el tronco doblado como
las rodillas muy flexionadas, así que un levantamiento correcto en
cuclillas también sale con puntaje alto. Este indicador complementa al REBA,
no lo reemplaza. El peso y la frecuencia los evalúa el profesional.
"""

from __future__ import annotations

from dataclasses import dataclass

# Manos más cerca del piso que esto (en largos de tronco, de cadera a
# hombro) = alcance bajo. De pie con los brazos colgando la muñeca queda a
# unos 1,4 largos del tobillo; a la altura de la rodilla, a unos 0,8.
ALCANCE_BAJO = 0.7
# Dos tramos bajos separados por menos que esto son el mismo episodio (el
# modelo puede perder la muñeca un instante).
HUECO_MAXIMO_S = 0.5
DURACION_MINIMA_S = 0.2

TRONCO_ESPALDA = 60.0   # tronco flexionado al menos esto...
RODILLA_RECTA = 60.0    # ...con las rodillas por debajo de esto: con la espalda
RODILLA_PIERNAS = 90.0  # rodillas al menos esto con el tronco más derecho: con las piernas


@dataclass
class Alcance:
    inicio_s: float
    fin_s: float
    tronco: float
    rodilla: float
    tecnica: str  # "con la espalda", "con las piernas" o "mixta"


def clasificar_tecnica(tronco: float, rodilla: float) -> str:
    if tronco >= TRONCO_ESPALDA and rodilla < RODILLA_RECTA:
        return "con la espalda"
    if rodilla >= RODILLA_PIERNAS and tronco < TRONCO_ESPALDA:
        return "con las piernas"
    return "mixta"


def detectar_alcances(cuadros) -> list[Alcance]:
    """`cuadros`: secuencia de objetos con `tiempo_s` y `angulos` (o None)."""
    episodios: list[list] = []
    for c in cuadros:
        a = c.angulos
        if a is None or a.muneca_sobre_piso is None or a.tronco is None or a.rodilla is None:
            continue
        if a.muneca_sobre_piso >= ALCANCE_BAJO:
            continue
        if episodios and c.tiempo_s - episodios[-1][-1].tiempo_s <= HUECO_MAXIMO_S:
            episodios[-1].append(c)
        else:
            episodios.append([c])

    alcances = []
    for ep in episodios:
        if ep[-1].tiempo_s - ep[0].tiempo_s < DURACION_MINIMA_S:
            continue
        mas_bajo = min(ep, key=lambda c: c.angulos.muneca_sobre_piso).angulos
        alcances.append(Alcance(ep[0].tiempo_s, ep[-1].tiempo_s, mas_bajo.tronco, mas_bajo.rodilla,
                                clasificar_tecnica(mas_bajo.tronco, mas_bajo.rodilla)))
    return alcances
