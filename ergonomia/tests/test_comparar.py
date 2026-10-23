"""Banco de pruebas de modelos de pose, con modelos falsos (sin bajar pesos)."""

import json

import cv2
import numpy as np
from esqueletos import perfil

from ergonomia import comparar
from ergonomia.esqueleto import medir_cuadro

ALTO = ANCHO = 1000


class _PoseFija:
    """Devuelve siempre el mismo esqueleto, con el tronco que se le pida."""

    def __init__(self, tronco=10.0):
        self.tronco = tronco
        self.cerrado = False

    def cerrar(self):
        self.cerrado = True

    def estimar(self, cuadro, tiempo_ms):
        return perfil(tronco=self.tronco)


def _video(tmp_path, cuadros=20):
    ruta = tmp_path / "puesto.mp4"
    escritor = cv2.VideoWriter(str(ruta), cv2.VideoWriter_fourcc(*"mp4v"), 10, (ANCHO, ALTO))
    for _ in range(cuadros):
        escritor.write(np.zeros((ALTO, ANCHO, 3), np.uint8))
    escritor.release()
    return ruta


def _mediciones(tronco, cantidad=10):
    return [medir_cuadro(perfil(tronco=tronco), ALTO, ANCHO, "der") for _ in range(cantidad)]


def test_mide_tiempos_y_cuadros_medibles(tmp_path):
    resultado = comparar.medir_modelo("falso", _PoseFija(), str(_video(tmp_path)), 20, 1)
    assert resultado["cuadros"] == 20
    assert resultado["medibles"] == 20
    assert resultado["con_persona"] == 20
    assert resultado["ms_mediana"] >= 0
    assert resultado["cuadros_por_segundo"] > 0


def test_el_paso_saltea_cuadros(tmp_path):
    resultado = comparar.medir_modelo("falso", _PoseFija(), str(_video(tmp_path, 20)), 20, paso=4)
    assert resultado["cuadros"] == 5


def test_dos_modelos_identicos_no_difieren():
    diferencia = comparar.comparar_mediciones(_mediciones(10.0), _mediciones(10.0))
    assert diferencia["cuadros_comparables"] == 10
    assert diferencia["grados"]["tronco"]["max"] == 0.0
    assert diferencia["puntos_en_largos_de_torso"]["max"] == 0.0


def test_mide_cuanto_se_corre_el_angulo():
    # Lo que decide el cambio de modelo: tres grados de tronco pueden mover la
    # banda REBA y cambiar lo que se informa en la planilla.
    diferencia = comparar.comparar_mediciones(_mediciones(10.0), _mediciones(13.0))
    assert abs(diferencia["grados"]["tronco"]["mediana"] - 3.0) < 0.01
    assert diferencia["puntos_en_largos_de_torso"]["mediana"] > 0


def test_los_cuadros_sin_persona_no_se_comparan():
    sin_persona = [medir_cuadro(None, ALTO, ANCHO, "der") for _ in range(10)]
    diferencia = comparar.comparar_mediciones(_mediciones(10.0), sin_persona)
    assert diferencia["cuadros_comparables"] == 0
    assert diferencia["grados"]["tronco"] is None


def test_de_punta_a_punta_con_salida_json(tmp_path, monkeypatch):
    video = _video(tmp_path)
    modelos = {"yolo11n-pose.pt": _PoseFija(10.0), "ncnn_model": _PoseFija(11.0)}
    monkeypatch.setattr(comparar, "crear_modelo", lambda tipo, ruta: modelos[ruta])

    salida = tmp_path / "comparacion.json"
    comparar.main([str(video), "--pytorch", "yolo11n-pose.pt", "--ncnn", "ncnn_model",
                   "--cuadros", "10", "--salida", str(salida)])

    assert all(m.cerrado for m in modelos.values())
    datos = json.loads(salida.read_text(encoding="utf-8"))
    assert datos["referencia"] == "pytorch"
    assert [m["modelo"] for m in datos["modelos"]] == ["pytorch", "ncnn"]
    # El JSON no se lleva las mediciones crudas, que son miles de objetos.
    assert "_mediciones" not in datos["modelos"][0]
    assert abs(datos["comparaciones"]["ncnn"]["grados"]["tronco"]["mediana"] - 1.0) < 0.01


def test_sin_modelos_avisa(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        comparar.main([str(_video(tmp_path))])
