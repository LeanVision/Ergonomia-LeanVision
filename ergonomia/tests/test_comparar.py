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


def _jsonl_de_keypoints(tmp_path, tronco, cuadros=10, falta=()):
    """keypoints.jsonl como el que deja la captura en la Pi."""
    from ergonomia.pose import _COCO

    esqueleto = perfil(tronco=tronco)
    ruta = tmp_path / "keypoints.jsonl"
    with open(ruta, "w", encoding="utf-8") as archivo:
        for i in range(cuadros):
            puntos = None
            if i not in falta:
                puntos = [list(esqueleto.puntos.get(n, (0.0, 0.0, 0.0))) if n else [0.0, 0.0, 0.0]
                          for n in _COCO]
            archivo.write(json.dumps({"cuadro": i, "ts": 1700000000 + i, "puntos": puntos}) + "\n")
    return ruta


def test_la_pose_grabada_se_relee_por_numero_de_cuadro(tmp_path):
    from ergonomia.pose import PoseGrabada

    modelo = PoseGrabada(str(_jsonl_de_keypoints(tmp_path, tronco=10.0)))
    # tiempo_ms es el número de cuadro por cien, como lo pasa comparar.py.
    assert modelo.estimar(None, 0) is not None
    assert modelo.estimar(None, 700) is not None
    # Un cuadro que no está en el archivo no inventa una persona.
    assert modelo.estimar(None, 999900) is None


def test_los_cuadros_sin_persona_vuelven_vacios(tmp_path):
    from ergonomia.pose import PoseGrabada

    modelo = PoseGrabada(str(_jsonl_de_keypoints(tmp_path, tronco=10.0, falta=(3,))))
    assert modelo.estimar(None, 200) is not None
    assert modelo.estimar(None, 300) is None


def test_el_sensor_grabado_se_compara_contra_el_modelo(tmp_path, monkeypatch):
    # El caso real: la IMX500 midió en el puesto y se quiere saber cuánto se
    # desvía de yolo11n-pose sobre el mismo video.
    from ergonomia.pose import PoseGrabada

    video = _video(tmp_path, 10)
    grabada = _jsonl_de_keypoints(tmp_path, tronco=12.0, cuadros=10)
    modelos = {"yolo11n-pose.pt": _PoseFija(10.0), str(grabada): PoseGrabada(str(grabada))}
    monkeypatch.setattr(comparar, "crear_modelo", lambda tipo, ruta: modelos[ruta])

    salida = tmp_path / "comparacion.json"
    comparar.main([str(video), "--pytorch", "yolo11n-pose.pt", "--grabada", str(grabada),
                   "--cuadros", "10", "--salida", str(salida)])

    datos = json.loads(salida.read_text(encoding="utf-8"))
    assert datos["referencia"] == "pytorch"
    assert abs(datos["comparaciones"]["imx500"]["grados"]["tronco"]["mediana"] - 2.0) < 0.01
