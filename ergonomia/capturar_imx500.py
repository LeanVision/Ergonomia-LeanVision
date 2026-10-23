"""Graba un puesto con la Raspberry Pi AI Camera: video y keypoints del sensor.

La IMX500 corre la red de pose DENTRO del sensor, así que los keypoints salen
sin costo de CPU. Esto guarda las dos cosas del MISMO cuadro: el video, para
poder correrle después cualquier otro modelo en una PC, y los keypoints que
largó el sensor, en un JSONL.

Con eso se contesta la pregunta que importa antes de elegir hardware: ¿los
ángulos que salen del sensor son los mismos que los de yolo11n-pose? Se compara
con `ergonomia.comparar`, que trata el JSONL como un modelo más:

    # En la Pi:
    python -m ergonomia.capturar_imx500 --salida captura --segundos 120

    # En la PC, con la carpeta `captura` copiada:
    python -m ergonomia.comparar captura/video.mp4 \\
        --grabada captura/keypoints.jsonl --pytorch yolo11n-pose.pt

Necesita picamera2 y munkres, que vienen o se instalan en la Pi:

    sudo apt install python3-picamera2 imx500-models
    pip3 install --break-system-packages munkres

El modelo por defecto es el HigherHRNet del model zoo de Raspberry Pi, que es
el único de pose que trae. Sus keypoints son los 17 de COCO, el mismo formato
que usa el resto del repo.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

# El orden de los 17 keypoints COCO, tal como los devuelve la red.
COCO = (
    "nariz", None, None, "oreja_izq", "oreja_der",
    "hombro_izq", "hombro_der", "codo_izq", "codo_der",
    "muneca_izq", "muneca_der", "cadera_izq", "cadera_der",
    "rodilla_izq", "rodilla_der", "tobillo_izq", "tobillo_der",
)

MODELO_POR_DEFECTO = "/usr/share/imx500-models/imx500_network_higherhrnet_coco.rpk"
# La red del sensor trabaja a esta resolución; el postproceso reescala los
# puntos al tamaño del cuadro que se graba.
ENTRADA_RED = (288, 384)


def _persona_mas_grande(personas) -> int:
    """Índice de la persona que ocupa más alto en la imagen.

    Mismo criterio que pose.py: se supone que la cámara apunta al trabajador
    del puesto evaluado, así que la más grande es la que interesa.
    """
    alturas = []
    for puntos in personas:
        ys = [p[1] for p in puntos if p[2] > 0]
        alturas.append(max(ys) - min(ys) if len(ys) >= 2 else 0.0)
    return max(range(len(alturas)), key=alturas.__getitem__)


def keypoints_del_cuadro(imx500, metadata, tamano) -> list[list[float]] | None:
    """Pasa los tensores del sensor a 17 puntos [x, y, confianza], o None.

    Es la única parte atada a picamera2: si en tu versión el postproceso
    devuelve otra cosa, se arregla acá y el resto del repo no se entera.
    """
    from picamera2.devices.imx500.postprocess_highernet import postprocess_higherhrnet

    salidas = imx500.get_outputs(metadata=metadata, add_batch=True)
    if salidas is None:
        return None
    alto, ancho = tamano
    personas, scores, _ = postprocess_higherhrnet(
        outputs=salidas,
        img_size=(alto, ancho),
        img_w_pad=(0, 0),
        img_h_pad=(0, 0),
        detection_threshold=0.3,
        network_postprocess=True,
        input_image_size=ENTRADA_RED,
    )
    personas = [p for p in personas if len(p)]
    if not personas:
        return None
    elegida = personas[_persona_mas_grande(personas)]
    if len(elegida) != 17:
        raise SystemExit(
            f"El modelo devolvió {len(elegida)} puntos y se esperaban 17 (COCO). "
            "Revisar keypoints_del_cuadro() contra el ejemplo de pose instalado en la Pi."
        )
    return [[float(p[0]), float(p[1]), float(p[2])] for p in elegida]


def capturar(salida: Path, segundos: float, hz: float, modelo: str,
             ancho: int, alto: int) -> tuple[int, int]:
    """Graba video y keypoints. Devuelve (cuadros, cuadros con persona)."""
    import cv2
    from picamera2 import Picamera2
    from picamera2.devices.imx500 import IMX500

    salida.mkdir(parents=True, exist_ok=True)
    imx500 = IMX500(modelo)
    camara = Picamera2(imx500.camera_num)
    configuracion = camara.create_preview_configuration(
        main={"size": (ancho, alto), "format": "RGB888"},
        controls={"FrameRate": max(hz, 10.0)},
        buffer_count=8,
    )
    camara.start(configuracion)

    escritor = cv2.VideoWriter(str(salida / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                               hz, (ancho, alto))
    archivo = open(salida / "keypoints.jsonl", "w", encoding="utf-8")
    periodo = 1.0 / hz if hz > 0 else 0.0
    arranque = time.time()
    cuadro = con_persona = 0
    try:
        while time.time() - arranque < segundos:
            # El request trae imagen y metadata del MISMO cuadro: capturarlos
            # por separado los desfasa y los keypoints dejarían de
            # corresponderse con el video.
            pedido = camara.capture_request()
            try:
                imagen = pedido.make_array("main")
                puntos = keypoints_del_cuadro(imx500, pedido.get_metadata(), (alto, ancho))
            finally:
                pedido.release()

            escritor.write(imagen)
            archivo.write(json.dumps(
                {"cuadro": cuadro, "ts": round(time.time(), 3), "puntos": puntos}
            ) + "\n")
            archivo.flush()
            cuadro += 1
            con_persona += puntos is not None
            if cuadro % 20 == 0:
                print(f"  {cuadro} cuadros, {con_persona} con persona", flush=True)
            espera = arranque + cuadro * periodo - time.time()
            if espera > 0:
                time.sleep(espera)
    except KeyboardInterrupt:
        print("Captura cortada.")
    finally:
        archivo.close()
        escritor.release()
        camara.stop()
    return cuadro, con_persona


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--salida", default="captura", help="carpeta donde dejar video.mp4 y keypoints.jsonl")
    p.add_argument("--segundos", type=float, default=120.0)
    p.add_argument("--hz", type=float, default=2.0, help="cuadros por segundo a guardar")
    p.add_argument("--modelo", default=MODELO_POR_DEFECTO, help="archivo .rpk de pose")
    p.add_argument("--ancho", type=int, default=1280)
    p.add_argument("--alto", type=int, default=720)
    args = p.parse_args(argv)

    salida = Path(args.salida)
    print(f"Grabando {args.segundos:.0f} s a {args.hz} Hz en {salida}. Ctrl-C para cortar.")
    cuadros, con_persona = capturar(salida, args.segundos, args.hz, args.modelo,
                                    args.ancho, args.alto)
    print(f"\n{cuadros} cuadros, {con_persona} con persona ({100 * con_persona / max(cuadros, 1):.0f} %).")
    print(f"Video en {salida / 'video.mp4'}, keypoints en {salida / 'keypoints.jsonl'}.")
    print("Para comparar contra yolo11n-pose, copiar la carpeta a una PC y correr:")
    print(f"  python -m ergonomia.comparar {salida / 'video.mp4'} "
          f"--grabada {salida / 'keypoints.jsonl'} --pytorch yolo11n-pose.pt")


if __name__ == "__main__":
    main()
