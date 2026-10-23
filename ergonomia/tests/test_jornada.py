"""Modo jornada: agrupar por minuto, guardar y releer, y el borrador del día."""

import json

import cv2
import numpy as np
from esqueletos import perfil
from test_bipedestacion import sentado

from ergonomia import jornada
from ergonomia.bipedestacion import Minuto

IMAGEN = np.zeros((1000, 1000, 3), np.uint8)


class _PoseFalsa:
    """Siempre la misma persona de pie."""

    cerrado = False

    def cerrar(self):
        _PoseFalsa.cerrado = True

    def estimar(self, cuadro, tiempo_ms):
        return perfil()


class _PoseQueSeSienta:
    """De pie la primera mitad del turno, sentada la segunda."""

    def __init__(self, corte_ms):
        self.corte_ms = corte_ms

    def cerrar(self):
        pass

    def estimar(self, cuadro, tiempo_ms):
        return perfil() if tiempo_ms < self.corte_ms else sentado()


def muestras(cantidad, inicio=0.0, fps=2.0):
    return [(inicio + i / fps, IMAGEN) for i in range(cantidad)]


def test_agrupa_las_muestras_por_minuto_de_reloj():
    cerrados = []
    minutos = jornada.medir_jornada(muestras(240), _PoseFalsa(), cuadros_por_segundo=2.0,
                                    al_cerrar_minuto=cerrados.append)
    assert len(minutos) == 2
    assert [m.cuadros for m in minutos] == [120, 120]
    assert [m.segundos_de_pie for m in minutos] == [60.0, 60.0]
    # Cada minuto se entrega en cuanto se cierra, para que no haya que esperar
    # al final del turno para escribirlo.
    assert cerrados == minutos


def test_distingue_de_pie_de_sentado_a_lo_largo_del_turno():
    minutos = jornada.medir_jornada(muestras(240), _PoseQueSeSienta(corte_ms=60_000),
                                    cuadros_por_segundo=2.0)
    assert minutos[0].segundos_de_pie == 60.0
    assert minutos[1].segundos_sentado == 60.0


def test_ida_y_vuelta_a_disco(tmp_path):
    ruta = jornada.ruta_del_dia(tmp_path, "Embalaje línea 2", "2026-10-22")
    assert ruta.name == "2026-10-22.jsonl"
    assert ruta.parent.name == "embalaje_linea_2"

    originales = jornada.medir_jornada(muestras(240), _PoseFalsa(), cuadros_por_segundo=2.0)
    for minuto in originales:
        jornada.guardar_minuto(ruta, minuto)

    leidos = jornada.cargar_minutos(ruta)
    assert leidos == originales


def test_al_releer_se_rellenan_los_huecos(tmp_path):
    # El proceso estuvo caído tres minutos: en el archivo quedan dos bloques
    # pegados, y leerlos de corrido haría pasar por continuo lo que nadie midió.
    ruta = jornada.ruta_del_dia(tmp_path, "Torno", "2026-10-22")
    for inicio in (0.0, 60.0, 300.0):
        jornada.guardar_minuto(ruta, Minuto(inicio=inicio, cuadros=120, medidos=120,
                                            segundos_de_pie=60.0))

    leidos = jornada.cargar_minutos(ruta)
    assert len(leidos) == 6
    assert [m.medidos for m in leidos] == [120, 120, 0, 0, 0, 120]
    assert [m.de_pie for m in leidos] == [True, True, None, None, None, True]


def test_una_linea_rota_no_tira_abajo_el_informe(tmp_path):
    ruta = jornada.ruta_del_dia(tmp_path, "Torno", "2026-10-22")
    jornada.guardar_minuto(ruta, Minuto(inicio=0.0, cuadros=120, medidos=120, segundos_de_pie=60.0))
    with open(ruta, "a", encoding="utf-8") as archivo:
        archivo.write("{esto no es json\n")
    assert len(jornada.cargar_minutos(ruta)) == 1


def test_un_nombre_hostil_no_escribe_fuera_de_la_carpeta(tmp_path):
    ruta = jornada.ruta_del_dia(tmp_path, "../../etc/passwd", "2026-10-22")
    assert tmp_path.resolve() in ruta.resolve().parents
    assert jornada.slug("../../etc") == "etc"
    assert jornada.ruta_del_dia(tmp_path, "///", "2026-10-22").parent.name == "sin_nombre"


def test_el_informe_del_dia(tmp_path, capsys):
    ruta = jornada.ruta_del_dia(tmp_path, "Prensa 2", "2026-10-22")
    for i in range(200):  # 3 h 20 de pie, quieto
        jornada.guardar_minuto(ruta, Minuto(inicio=i * 60, cuadros=120, medidos=120,
                                            segundos_de_pie=60.0, metros=0.5, con_escala=True))

    jornada.main(["--informe", "--puesto", "Prensa 2", "--salida", str(tmp_path),
                  "--fecha", "2026-10-22", "--carga-kg", "8"])

    datos = json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))
    assert datos["Puesto"] == "Prensa 2"
    assert datos["paso1_de_pie_2h_sin_sentarse"] is True
    assert datos["paso2_3h_deambulacion_escasa"] is True
    assert datos["paso2_2h_con_carga"] is True
    assert datos["declarado"] == {"hay_silla": False, "carga_kg": 8.0,
                                  "carga_termica": None, "manifestacion_temprana": None}

    texto = ruta.with_suffix(".md").read_text(encoding="utf-8")
    assert "planilla 2.D" in texto
    assert "3 h 20 min" in texto
    assert "planilla 2.D" in capsys.readouterr().out


def test_con_silla_declarada_el_paso_1_no_se_dispara(tmp_path):
    ruta = jornada.ruta_del_dia(tmp_path, "Prensa 2", "2026-10-22")
    for i in range(200):
        jornada.guardar_minuto(ruta, Minuto(inicio=i * 60, cuadros=120, medidos=120,
                                            segundos_de_pie=60.0))

    jornada.main(["--informe", "--puesto", "Prensa 2", "--salida", str(tmp_path),
                  "--fecha", "2026-10-22", "--hay-silla"])

    datos = json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))
    assert datos["declarado"]["hay_silla"] is True
    assert datos["paso1_de_pie_2h_sin_sentarse"] is False


def test_de_un_video_al_borrador(tmp_path, monkeypatch):
    # Un turno grabado se reprocesa desde el archivo: el tiempo de cada cuadro
    # sale de la posición en el video, no del reloj.
    video = tmp_path / "turno.mp4"
    escritor = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 20, (1000, 1000))
    for _ in range(20 * 120):  # 2 min a 20 fps
        escritor.write(IMAGEN)
    escritor.release()

    monkeypatch.setattr(jornada, "crear_modelo", lambda nombre, ruta: _PoseFalsa())
    jornada.main(["--puesto", "Torno 1", "--salida", str(tmp_path), "--fuente", str(video),
                  "--fecha", "2026-10-22", "--ancho-escena", "4"])
    assert _PoseFalsa.cerrado

    ruta = jornada.ruta_del_dia(tmp_path, "Torno 1", "2026-10-22")
    minutos = jornada.cargar_minutos(ruta)
    # Los minutos son de reloj, y el video no arranca justo en uno: dos minutos
    # de grabación caen en dos o tres minutos de reloj segun cuando se corrio.
    assert 2 <= len(minutos) <= 3
    assert sum(m.cuadros for m in minutos) == 240  # 2 cuadros por segundo
    datos = json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))
    assert datos["minutos_de_pie"] == 2.0
    # Dos minutos no alcanzan para ninguno de los pasos de la planilla.
    assert datos["paso1_de_pie_2h_sin_sentarse"] is False


def test_los_items_declarados_llegan_al_borrador(tmp_path):
    ruta = jornada.ruta_del_dia(tmp_path, "Prensa 2", "2026-10-22")
    for i in range(200):
        jornada.guardar_minuto(ruta, Minuto(inicio=i * 60, cuadros=120, medidos=120,
                                            segundos_de_pie=60.0, metros=10.0, con_escala=True))

    # Sin declarar, los dos ítems que no salen de la cámara quedan sin
    # responder y el borrador no puede presumir tolerable.
    jornada.main(["--informe", "--puesto", "Prensa 2", "--salida", str(tmp_path),
                  "--fecha", "2026-10-22"])
    datos = json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))
    assert datos["conclusion"] == "sin determinar"

    jornada.main(["--informe", "--puesto", "Prensa 2", "--salida", str(tmp_path),
                  "--fecha", "2026-10-22", "--carga-termica", "no",
                  "--manifestacion-temprana", "si"])
    datos = json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))
    assert datos["declarado"]["carga_termica"] is False
    assert datos["paso2_manifestacion_temprana"] is True
    assert datos["conclusion"] == "requiere evaluacion de riesgos"
