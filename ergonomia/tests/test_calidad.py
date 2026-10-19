"""Avisos de calidad de la toma: persona de frente y cambios de persona o de toma."""

from esqueletos import perfil

from ergonomia.esqueleto import calcular_angulos
from ergonomia.informe import Cuadro, armar_informe, contar_saltos, informe_markdown
from ergonomia.pdf import guardar_pdf
from ergonomia.reba import Condiciones


def _con_hombros(separacion_px, **postura):
    """Esqueleto con el hombro del otro lado visible, a esa distancia horizontal."""
    e = perfil(**postura)
    x, y, _ = e.puntos["hombro_der"]
    e.puntos["hombro_izq"] = (x + separacion_px, y, 0.9)
    return e


def _de_frente(**postura):
    # Hombros a 150 px con un tronco de 200 px: como cuando mira a la cámara.
    return _con_hombros(150, **postura)


def test_de_costado_los_hombros_se_superponen():
    assert calcular_angulos(_con_hombros(15, tronco=30), "der").separacion_hombros < 0.35
    assert calcular_angulos(_de_frente(tronco=30), "der").separacion_hombros > 0.35


def test_descarta_los_cuadros_de_frente_y_avisa():
    costado = calcular_angulos(perfil(tronco=30), "der")
    frente = calcular_angulos(_de_frente(tronco=-15), "der")
    cuadros = [Cuadro(i / 10, costado if i < 30 else frente) for i in range(100)]
    inf = armar_informe(cuadros, 10, Condiciones())
    assert inf["calidad"]["porcentaje_de_perfil"] == 30.0
    assert inf["cuadros_con_postura"] == 30
    # La extensión de -15° de los cuadros de frente no entra en las estadísticas.
    assert inf["posturas_porcentaje_tiempo"]["tronco_extension"] == 0.0
    assert "ATENCIÓN" in informe_markdown(inf)


def test_toma_buena_no_avisa():
    a = calcular_angulos(perfil(tronco=30), "der")
    inf = armar_informe([Cuadro(i / 10, a, (0.5, 0.5)) for i in range(50)], 10, Condiciones())
    assert "ATENCIÓN" not in informe_markdown(inf)


def test_cuenta_los_saltos_de_persona_o_de_toma():
    centros = [(0.3, 0.6)] * 5 + [None] * 2 + [(0.32, 0.6)] * 3 + [(0.8, 0.5)] * 4
    cuadros = [Cuadro(i / 10, None, c) for i, c in enumerate(centros)]
    assert contar_saltos(cuadros) == 1


def test_genera_el_pdf(tmp_path):
    a = calcular_angulos(perfil(tronco=70, brazo=30), "der")
    texto = informe_markdown(armar_informe([Cuadro(i / 10, a) for i in range(50)], 10, Condiciones()))
    ruta = tmp_path / "informe.pdf"
    assert guardar_pdf(texto, ruta)
    assert ruta.read_bytes().startswith(b"%PDF")


def test_descarta_cuerpo_cortado_o_muy_lejos():
    from ergonomia.esqueleto import cuerpo_entero

    e = perfil()  # tronco de 200 px
    assert cuerpo_entero(e, alto_imagen=1000)
    assert not cuerpo_entero(e, alto_imagen=5000)  # tronco del 4 % de la imagen: muy lejos
    assert cuerpo_entero(perfil(tronco=90), alto_imagen=1000)  # agachado, tronco horizontal
    for nombre in ("tobillo_der", "tobillo_izq"):
        x, y, _ = e.puntos[nombre]
        e.puntos[nombre] = (x, y, 0.3)  # primer plano: el modelo inventa los pies
    assert not cuerpo_entero(e, alto_imagen=1000)


def test_descarta_esqueletos_dados_vuelta():
    from ergonomia.esqueleto import Angulos, postura_posible

    assert postura_posible(Angulos(tronco=93))
    assert not postura_posible(Angulos(tronco=-177))
