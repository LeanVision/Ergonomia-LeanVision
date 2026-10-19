"""Técnica al levantar del piso: con la espalda o con las piernas."""

from esqueletos import perfil

from ergonomia.esqueleto import calcular_angulos
from ergonomia.informe import Cuadro
from ergonomia.levantamiento import clasificar_tecnica, detectar_alcances

# Agachado con la espalda: tronco a 90°, brazos colgando, piernas casi rectas.
ESPALDA = dict(tronco=90, brazo=90, codo=0, rodilla=20)
# En cuclillas: tronco más derecho, rodillas muy flexionadas.
PIERNAS = dict(tronco=45, brazo=45, codo=0, rodilla=130)
DE_PIE = dict(tronco=0, brazo=0, codo=10, rodilla=0)


def _cuadros(*tramos, fps=10):
    """Cada tramo es (postura, segundos)."""
    cuadros, t = [], 0.0
    for postura, segundos in tramos:
        a = calcular_angulos(perfil(**postura), "der")
        for _ in range(int(segundos * fps)):
            cuadros.append(Cuadro(round(t, 2), a))
            t += 1 / fps
    return cuadros


def test_clasifica_la_tecnica():
    assert clasificar_tecnica(92, 31) == "con la espalda"
    assert clasificar_tecnica(48, 129) == "con las piernas"
    assert clasificar_tecnica(70, 75) == "mixta"


def test_de_pie_las_manos_no_estan_cerca_del_piso():
    assert calcular_angulos(perfil(**DE_PIE), "der").muneca_sobre_piso > 1.0


def test_detecta_cada_levantamiento_y_su_tecnica():
    cuadros = _cuadros((DE_PIE, 2), (ESPALDA, 2), (DE_PIE, 2), (PIERNAS, 1.5), (DE_PIE, 1))
    alcances = detectar_alcances(cuadros)
    assert [a.tecnica for a in alcances] == ["con la espalda", "con las piernas"]
    assert alcances[0].inicio_s == 2.0
    assert round(alcances[0].tronco) == 90


def test_un_corte_breve_no_parte_el_levantamiento():
    cuadros = _cuadros((DE_PIE, 1), (ESPALDA, 3))
    for c in cuadros[20:23]:  # el modelo pierde a la persona 0,3 s
        c.angulos = None
    assert len(detectar_alcances(cuadros)) == 1


def test_un_instante_no_es_un_levantamiento():
    assert detectar_alcances(_cuadros((DE_PIE, 1), (ESPALDA, 0.1), (DE_PIE, 1))) == []
