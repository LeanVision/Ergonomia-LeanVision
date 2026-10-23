"""Planilla 2.D: si alguna de estas afirmaciones se rompe, el borrador que
revisa el profesional sale mal."""

from esqueletos import perfil

from ergonomia.bipedestacion import (
    Minuto,
    corridas_de_pie,
    deambulacion_minima_por_hora,
    planilla_2d,
    resumen_markdown,
    resumir_minuto,
)
from ergonomia.esqueleto import calcular_angulos, medir_cuadro
from ergonomia.informe import Cuadro

ALTO = ANCHO = 1000


def sentado(**kwargs):
    """perfil() con el muslo casi horizontal: la rodilla, adelante de la cadera."""
    e = perfil(**kwargs)
    cadera = e.puntos["cadera_der"]
    e.puntos["rodilla_der"] = (cadera[0] + 180, cadera[1] + 10, 0.9)
    e.puntos["tobillo_der"] = (cadera[0] + 190, cadera[1] + 190, 0.9)
    return e


def sin_piernas(**kwargs):
    e = perfil(**kwargs)
    for nombre in ("rodilla_der", "tobillo_der", "rodilla_izq", "tobillo_izq"):
        x, y, _ = e.puntos[nombre]
        e.puntos[nombre] = (x, y, 0.1)
    return e


def cuadros_de(esqueleto, cantidad, inicio=0.0, fps=2.0):
    """Cuadros ya medidos, como los arma `medir_cuadro` en la jornada."""
    salida = []
    for i in range(cantidad):
        medida = medir_cuadro(esqueleto, ALTO, ANCHO, "der")
        salida.append(Cuadro(inicio + i / fps, medida.angulos, medida.centro, medida.cuerpo_entero))
    return salida


def de_pie(inicio, metros=0.0, segundos=60.0):
    # con_escala=True: minuto de un puesto con el ancho de la escena declarado.
    return Minuto(inicio=inicio, cuadros=120, medidos=120, segundos_de_pie=segundos,
                  metros=metros, con_escala=True)


def en_asiento(inicio, segundos_sentado=60.0):
    return Minuto(inicio=inicio, cuadros=120, medidos=120, con_escala=True,
                  segundos_de_pie=60.0 - segundos_sentado, segundos_sentado=segundos_sentado)


def sin_medir(inicio):
    return Minuto(inicio=inicio, cuadros=120, medidos=0, con_escala=True)


def test_el_muslo_vertical_es_estar_de_pie():
    assert calcular_angulos(perfil(), "der").sentado is False
    assert calcular_angulos(sentado(), "der").sentado is True


def test_resumir_un_minuto_de_pie():
    minuto = resumir_minuto(0.0, cuadros_de(perfil(), 120), fps=2.0)
    assert minuto.medidos == 120
    assert minuto.cobertura == 1.0
    assert minuto.segundos_de_pie == 60.0
    assert minuto.segundos_sentado == 0.0


def test_sin_piernas_no_se_mide_ni_se_cuenta():
    # Los controles de calidad de medir_cuadro descartan el cuadro, así que el
    # minuto queda sin mediciones y no suma tiempo de exposición.
    minuto = resumir_minuto(0.0, cuadros_de(sin_piernas(), 120), fps=2.0)
    assert minuto.cuadros == 120
    assert minuto.medidos == 0
    assert minuto.cobertura == 0.0
    assert minuto.de_pie is None


def test_una_pausa_corta_no_corta_la_corrida():
    # 2 h 05 de pie con 30 s sentado en el medio sigue siendo UNA corrida que
    # supera las 2 horas: sentarse medio minuto no es una pausa de recuperación.
    jornada = [de_pie(i * 60) for i in range(125)]
    jornada[60] = en_asiento(60 * 60, segundos_sentado=30.0)
    corridas = corridas_de_pie(jornada)
    assert len(corridas) == 1
    assert corridas[0] >= 2 * 3600


def test_una_pausa_larga_si_corta_la_corrida():
    jornada = [de_pie(i * 60) for i in range(125)]
    for i in range(60, 70):
        jornada[i] = en_asiento(i * 60)
    corridas = corridas_de_pie(jornada)
    assert len(corridas) == 2
    assert max(corridas) < 2 * 3600


def test_un_hueco_sin_medir_corta_la_corrida():
    # Si la cámara estuvo tapada, no se puede declarar bipedestación continua
    # del otro lado del hueco: serían 2 h 20 que nadie midió.
    jornada = [de_pie(i * 60) for i in range(70)]
    jornada += [sin_medir((70 + i) * 60) for i in range(5)]
    jornada += [de_pie((75 + i) * 60) for i in range(70)]
    corridas = corridas_de_pie(jornada)
    assert len(corridas) == 2
    assert max(corridas) < 2 * 3600


def test_deambulacion_toma_la_hora_mas_quieta():
    jornada = [de_pie(i * 60, metros=0.5) for i in range(200)]
    # Media hora caminando no tapa las horas quietas del resto del turno.
    for i in range(100, 130):
        jornada[i] = de_pie(i * 60, metros=10.0)
    assert deambulacion_minima_por_hora(jornada) == 30.0


def test_sin_hora_completa_medida_no_hay_deambulacion():
    jornada = [de_pie(i * 60, metros=0.5) for i in range(60)]
    jornada[30] = sin_medir(30 * 60)
    assert deambulacion_minima_por_hora(jornada) is None
    assert deambulacion_minima_por_hora(jornada[:59]) is None


def _minuto_con_movimiento(desplazamiento_px, ancho_escena_m=4.0):
    """Un minuto donde la cadera se corre `desplazamiento_px` cuadro a cuadro."""
    quieto, corrido = perfil(), perfil()
    cadera = quieto.puntos["cadera_der"]
    corrido.puntos["cadera_der"] = (cadera[0] + desplazamiento_px, cadera[1], 0.9)
    cuadros = []
    for i in range(120):
        medida = medir_cuadro(quieto if i % 2 else corrido, ALTO, ANCHO, "der")
        cuadros.append(Cuadro(i / 2.0, medida.angulos, medida.centro, medida.cuerpo_entero))
    return resumir_minuto(0.0, cuadros, fps=2.0, ancho_escena_m=ancho_escena_m)


def test_el_deadband_ignora_el_temblor_del_punto():
    # Alguien parado: la cadera tiembla medio pixel entre cuadros. Sin deadband
    # eso suma metros y da por caminado lo que está quieto.
    assert _minuto_con_movimiento(0.5).metros == 0.0


def test_caminar_de_verdad_si_suma_metros():
    # Y el deadband no puede ser una excusa para no medir nunca: 100 px de los
    # 1000 de ancho, con la escena de 4 m, son 0,4 m por cuadro.
    assert _minuto_con_movimiento(100.0).metros > 40.0


def test_paso1_necesita_que_no_haya_silla():
    jornada = [de_pie(i * 60) for i in range(125)]
    assert planilla_2d(jornada, hay_silla=False)["paso1_de_pie_2h_sin_sentarse"] is True
    assert planilla_2d(jornada, hay_silla=True)["paso1_de_pie_2h_sin_sentarse"] is False


def test_paso2_necesita_3_horas_y_poca_deambulacion():
    dos_horas = [de_pie(i * 60, metros=0.5) for i in range(125)]
    assert planilla_2d(dos_horas)["paso2_3h_deambulacion_escasa"] is False

    tres_horas = [de_pie(i * 60, metros=0.5) for i in range(200)]
    planilla = planilla_2d(tres_horas)
    assert planilla["deambulacion_minima_m_h"] == 30.0
    assert planilla["paso2_3h_deambulacion_escasa"] is True

    # Quien camina 3 m por minuto hace 180 m/h: no es deambulación escasa.
    caminando = [de_pie(i * 60, metros=3.0) for i in range(200)]
    assert planilla_2d(caminando)["paso2_3h_deambulacion_escasa"] is False


def test_paso2_con_carga_la_declara_el_evaluador():
    jornada = [de_pie(i * 60) for i in range(125)]
    assert planilla_2d(jornada, carga_kg=0.0)["paso2_2h_con_carga"] is False
    assert planilla_2d(jornada, carga_kg=1.5)["paso2_2h_con_carga"] is False
    assert planilla_2d(jornada, carga_kg=8.0)["paso2_2h_con_carga"] is True


def test_la_carga_termica_no_se_mide():
    planilla = planilla_2d([de_pie(0)])
    assert planilla["paso2_2h_con_carga_termica"] is None
    assert "necesita termohigrómetro" in resumen_markdown(planilla)


def test_los_items_del_paso_2_piden_no_poder_sentarse():
    # El formulario repite "sin posibilidad de sentarse" en el Paso 1 y en los
    # dos primeros items del Paso 2: es condicion de los tres.
    jornada = [de_pie(i * 60, metros=0.5) for i in range(200)]
    sin_silla = planilla_2d(jornada, hay_silla=False, carga_kg=8.0)
    assert sin_silla["paso2_3h_deambulacion_escasa"] is True
    assert sin_silla["paso2_2h_con_carga"] is True

    con_silla = planilla_2d(jornada, hay_silla=True, carga_kg=8.0)
    assert con_silla["paso2_3h_deambulacion_escasa"] is False
    assert con_silla["paso2_2h_con_carga"] is False


def test_el_item_de_cargas_tambien_pide_poca_deambulacion():
    # "sin posibilidades de sentarse ni desplazarse o con escasa deambulacion":
    # quien camina 180 m/h no cumple ese item aunque levante 8 kg.
    caminando = [de_pie(i * 60, metros=3.0) for i in range(200)]
    assert planilla_2d(caminando, carga_kg=8.0)["paso2_2h_con_carga"] is False


def test_lo_que_no_se_midio_no_se_contesta_que_no():
    # Sin deambulacion medida, los items 1 y 2 quedan sin responder: un "no"
    # ahi haria presumir tolerable un puesto que nadie evaluo.
    sin_metros = [Minuto(inicio=i * 60, cuadros=120, medidos=120, segundos_de_pie=60.0)
                  for i in range(200)]  # con_escala=False: puesto sin calibrar
    planilla = planilla_2d(sin_metros, carga_kg=8.0)
    assert planilla["paso2_3h_deambulacion_escasa"] is None
    assert planilla["paso2_2h_con_carga"] is None
    assert planilla["conclusion"] == "sin determinar"


def test_la_conclusion_sigue_el_formulario():
    corta = [de_pie(i * 60, metros=0.5) for i in range(30)]
    # Paso 1 en NO: no se sigue al Paso 2.
    assert planilla_2d(corta)["conclusion"] == "tolerable"

    # Paso 1 en SI y todo el Paso 2 en NO (con la manifestacion declarada en no).
    dos_horas = [de_pie(i * 60, metros=10.0) for i in range(125)]
    presumible = planilla_2d(dos_horas, carga_termica=False, manifestacion_temprana=False)
    assert presumible["paso1_de_pie_2h_sin_sentarse"] is True
    assert presumible["conclusion"] == "se presume tolerable"

    # Un SI en cualquier item del Paso 2 obliga a la Evaluacion de Riesgos.
    quieto = [de_pie(i * 60, metros=0.5) for i in range(200)]
    con_riesgo = planilla_2d(quieto, carga_termica=False, manifestacion_temprana=False)
    assert con_riesgo["conclusion"] == "requiere evaluacion de riesgos"


def test_la_manifestacion_temprana_la_declara_medicina_del_trabajo():
    # Item 4 del Paso 2: no se mide con camara y alcanza para que el riesgo
    # deje de presumirse tolerable.
    dos_horas = [de_pie(i * 60, metros=10.0) for i in range(125)]
    sin_sintoma = planilla_2d(dos_horas, carga_termica=False, manifestacion_temprana=False)
    assert sin_sintoma["conclusion"] == "se presume tolerable"
    con_sintoma = planilla_2d(dos_horas, carga_termica=False, manifestacion_temprana=True)
    assert con_sintoma["paso2_manifestacion_temprana"] is True
    assert con_sintoma["conclusion"] == "requiere evaluacion de riesgos"


def test_los_100_metros_por_hora_entran():
    # La planilla dice "caminando no mas de 100 metros/hora": los 100 justos
    # cuentan como escasa deambulacion.
    justo = [de_pie(i * 60, metros=100.0 / 60.0) for i in range(200)]
    planilla = planilla_2d(justo)
    assert planilla["deambulacion_minima_m_h"] == 100.0
    assert planilla["paso2_3h_deambulacion_escasa"] is True


def test_el_resumen_avisa_cuando_la_cobertura_es_baja():
    floja = [Minuto(inicio=i * 60, cuadros=120, medidos=60, segundos_de_pie=30.0) for i in range(10)]
    texto = resumen_markdown(planilla_2d(floja))
    assert "ATENCIÓN" in texto
    buena = [de_pie(i * 60) for i in range(10)]
    assert "ATENCIÓN" not in resumen_markdown(planilla_2d(buena))


def test_sin_ancho_de_escena_no_se_inventan_metros():
    minuto = resumir_minuto(0.0, cuadros_de(perfil(), 120), fps=2.0, ancho_escena_m=0.0)
    assert minuto.metros == 0.0
    assert minuto.con_escala is False
    assert planilla_2d([minuto])["deambulacion_minima_m_h"] is None

    # Y tampoco con un turno entero: sin escala los metros son 0 por falta de
    # dato, y darlos por buenos haria contestar "0 m/h" y disparar el Paso 2.1
    # en cualquier puesto sin calibrar.
    turno = [resumir_minuto(i * 60.0, cuadros_de(perfil(), 120), fps=2.0, ancho_escena_m=0.0)
             for i in range(200)]
    planilla = planilla_2d(turno)
    assert planilla["deambulacion_minima_m_h"] is None
    assert planilla["paso2_3h_deambulacion_escasa"] is None
