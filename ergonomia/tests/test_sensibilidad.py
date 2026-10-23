"""Estudio de sensibilidad: la simulación tiene que coincidir con la geometría."""

import math

from ergonomia import sensibilidad
from ergonomia.esqueleto import calcular_angulos
from ergonomia.informe import de_perfil


def medido(tronco, azimut, **kwargs):
    esqueleto = sensibilidad.proyectar(sensibilidad.esqueleto_3d(tronco=tronco, **kwargs), azimut)
    return calcular_angulos(esqueleto, "der").tronco


def test_de_perfil_el_angulo_sale_exacto():
    # Sin esto, el resto del estudio mediría el error de la simulación y no el
    # del método.
    for tronco in (0.0, 20.0, 45.0, 60.0, 80.0):
        assert abs(medido(tronco, azimut=0.0) - tronco) < 0.01


def test_el_error_coincide_con_la_formula(monkeypatch):
    # Con la flexión en el plano sagital y la cámara a `azimut` grados de la
    # perpendicular, la componente hacia adelante se acorta por cos(azimut):
    #     medido = atan2(sin(real) * cos(azimut), cos(real))
    # La fórmula vale para el tronco medido entre centros. El repo mide de
    # hombro a cadera del mismo lado, así que para compararla hay que igualar
    # los anchos; con las proporciones reales el error es algo mayor (ver el
    # test siguiente).
    monkeypatch.setattr(sensibilidad, "ANCHO_CADERAS", sensibilidad.ANCHO_HOMBROS)
    for tronco in (20.0, 45.0, 60.0):
        for azimut in (10.0, 25.0, 40.0):
            esperado = math.degrees(math.atan2(
                math.sin(math.radians(tronco)) * math.cos(math.radians(azimut)),
                math.cos(math.radians(tronco)),
            ))
            assert abs(medido(tronco, azimut) - esperado) < 0.01


def test_los_hombros_mas_anchos_que_las_caderas_suman_error(monkeypatch):
    # El tronco se mide de hombro a cadera del mismo lado, y como el hombro
    # está más afuera, al girar la cámara aparece una componente lateral que
    # no existe en la línea media. Es parte del error real del método, no un
    # problema de la simulación.
    con_anatomia = medido(45.0, azimut=40.0)
    monkeypatch.setattr(sensibilidad, "ANCHO_CADERAS", sensibilidad.ANCHO_HOMBROS)
    sin_anatomia = medido(45.0, azimut=40.0)
    assert con_anatomia < sin_anatomia


def test_el_angulo_medido_se_achica_al_girar_la_camara():
    # De frente la flexión casi no se ve: es la razón de filmar de costado.
    valores = [medido(60.0, azimut) for azimut in (0, 15, 30, 45, 60, 75)]
    assert valores == sorted(valores, reverse=True)
    assert medido(60.0, 85.0) < 10.0


def test_la_separacion_de_hombros_crece_con_el_azimut():
    # Es la señal que usa el control de perfil del repo.
    separaciones = []
    for azimut in (0, 10, 20, 30, 45):
        esqueleto = sensibilidad.proyectar(sensibilidad.esqueleto_3d(tronco=30.0), azimut)
        separaciones.append(calcular_angulos(esqueleto, "der").separacion_hombros)
    assert separaciones[0] < 0.01
    assert separaciones == sorted(separaciones)


def test_el_barrido_informa_la_banda_que_el_control_deja_pasar():
    # El hallazgo del estudio: el error pasa los 2° antes de que el control de
    # perfil empiece a rechazar, así que hay cuadros aceptados con error
    # suficiente para mover de banda REBA.
    filas = sensibilidad.barrido_de_azimut(tronco=60.0)
    malo = sensibilidad._primer_azimut_malo(filas)
    rechazado = sensibilidad._primer_azimut_rechazado(filas)
    assert malo is not None and rechazado is not None
    assert malo < rechazado


def test_el_umbral_mas_estricto_cerraria_la_banda():
    # Documenta la propuesta: con 0,25 en vez de 0,35, todo cuadro aceptado
    # queda por debajo de los 2° de error, en todo el rango de flexión.
    for tronco in (20.0, 45.0, 60.0, 80.0):
        for fila in sensibilidad.barrido_de_azimut(tronco=tronco):
            if fila["separacion_hombros"] is not None and fila["separacion_hombros"] <= 0.25:
                assert fila["error_tronco"] <= sensibilidad.ERROR_TOLERABLE


def test_lejos_el_ruido_de_pose_pesa_mas():
    filas = sensibilidad.barrido_de_tamano(tronco=60.0, ruido_px=1.5)
    errores = [f["error_promedio"] for f in filas]
    assert errores == sorted(errores, reverse=True)
    # Con el tronco ocupando 20 px el método no sirve, y con 200 px el ruido
    # del detector deja de importar.
    assert errores[0] > 3.0
    assert errores[-1] < 1.0


def test_sin_ruido_el_tamano_no_cambia_nada():
    filas = sensibilidad.barrido_de_tamano(tronco=60.0, ruido_px=0.0, repeticiones=3)
    assert all(f["error_promedio"] < 0.01 for f in filas)


def test_el_control_de_perfil_acepta_el_caso_ideal():
    angulos = calcular_angulos(sensibilidad.proyectar(sensibilidad.esqueleto_3d(tronco=60.0), 0.0), "der")
    assert de_perfil(angulos)
