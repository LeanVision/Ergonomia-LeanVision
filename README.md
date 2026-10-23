# Ergonomía con visión artificial (prototipo)

Asistente para el **Protocolo de Ergonomía de la Resolución SRT 886/2015**. A
partir de un video del puesto de trabajo, estima la postura del trabajador
(su "esqueleto") y arma un **borrador de evidencias** para las planillas:

- **2.A Levantamiento y descenso de cargas:** cada vez que las manos bajan cerca del piso, si la persona se agachó **con la espalda** (tronco doblado, piernas rectas: técnica a corregir), **con las piernas** (en cuclillas) o de forma **mixta**.
- **2.F Posturas forzadas:** % del tiempo con el tronco, el cuello, los brazos, la muñeca y las rodillas en posturas de riesgo, más el puntaje **REBA** por cuadro.
- **2.E Movimientos repetitivos:** ciclos por minuto, duración del ciclo, % del tiempo en movimiento y un **Nivel de Actividad Manual estimado** (ecuación de Radwin, 2015).
- **2.D Bipedestación:** tiempo de pie, y sobre la jornada completa (ver *Medir la jornada*) la bipedestación continua más larga y la deambulación en metros por hora.

La norma pide que la evaluación la haga un profesional con conocimientos en
ergonomía: el informe trae las mediciones y el espacio para que ese
profesional las revise, complete (carga, fuerza percibida, duración de la
tarea en la jornada) y firme.

## Cómo filmar

- **Cámara de costado al trabajador**, a la altura de la cintura o el pecho, con todo el cuerpo en cuadro. De frente los ángulos salen más chicos de lo que son.
- Una sola persona en el puesto (si hay varias se toma la más grande en la imagen).
- Varios ciclos completos de la tarea. Con un celular alcanza.

## Uso

```bash
pip install -r requirements.txt
python -m ergonomia.analizar video.mp4 --puesto "Embalaje línea 2" --carga-kg 8 --agarre regular
```

Deja en `video_ergonomia/`:

- `informe.pdf`: el informe para leer o imprimir (necesita `fpdf2`).
- `informe.md`: el mismo informe en texto.
- `informe.json`: los mismos datos, para procesarlos.
- `angulos.csv`: los ángulos cuadro por cuadro.
- `esqueleto.mp4`, sólo con `--video-esqueleto`: el esqueleto sobre fondo negro, para mostrar el análisis sin mostrar a la persona.
- `superpuesto.mp4`, sólo con `--video-superpuesto`: el video original con el esqueleto, los ángulos, el REBA y la técnica de levantamiento encima. **Muestra a la persona:** usarlo sólo con su consentimiento.

Si hay `ffmpeg` instalado, los videos se guardan en H.264 para que se vean en cualquier navegador o celular.

Opciones útiles: `--lado izq|der` si la detección automática del lado que mira a la cámara falla, `--golpes`, `--un-pie`, `--cuadros-por-segundo`.

### Modelo de pose

- `--modelo yolo` (por defecto): Ultralytics YOLO-pose. Baja los pesos solo la primera vez. No detecta los dedos, así que la muñeca no se mide.
- `--modelo mediapipe --ruta-modelo pose_landmarker_full.task`: MediaPipe Pose Landmarker. Sí mide la muñeca. El archivo se baja de la [página de MediaPipe Pose Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker#models).

Todo corre local: el video no sale de la máquina y sus cuadros no se guardan.

## Medir la jornada (planilla 2.D)

La 2.D no se puede contestar con un video de unos minutos: pregunta por **dos
horas seguidas** de pie sin poder sentarse, por **tres horas** con deambulación
de menos de **100 m/h**, o por **dos horas** con portación de cargas. Para eso
hay un segundo modo, que queda mirando el puesto todo el turno:

```bash
# Durante el turno (Ctrl-C para cortar):
python -m ergonomia.jornada --fuente rtsp://camara/stream     --puesto "Embalaje linea 2" --ancho-escena 4

# Después, el borrador del día:
python -m ergonomia.jornada --informe --puesto "Embalaje linea 2"     --carga-kg 8 --fecha 2026-10-22
```

Deja en `jornada/<puesto>/<AAAA-MM-DD>.jsonl` **un resumen por minuto**: de
cada cuadro sólo se guarda si la persona estaba de pie y cuánto se movió. Una
jornada de 8 h a 2 cuadros por segundo son 57.600 cuadros pero 480 líneas, y
con eso alcanza para la planilla. No se guarda ni video ni cuadros.

`--informe` escribe al lado el `.md` y el `.json` con la corrida de
bipedestación más larga, la deambulación de la hora más quieta y las respuestas
de los pasos 1, 2.1 y 2.2.

Los enunciados del borrador son los del formulario del Anexo I, y termina con
la conclusión que pide la planilla: riesgo tolerable, se presume tolerable, o
hay que hacer una Evaluación de Riesgos.

Cuatro cosas que decide el evaluador y la cámara no puede ver:

- `--hay-silla`: la cámara ve que el trabajador no se sienta, no si **no puede**
  sentarse. Es condición del Paso 1 y de los dos primeros ítems del Paso 2.
- `--carga-kg`: el peso que manipula de forma habitual (ítem 2 del Paso 2).
- `--carga-termica si|no`: temperatura y humedad fuera de los límites
  admisibles (ítem 3). Necesita termohigrómetro.
- `--manifestacion-temprana si|no`: síntomas de las enfermedades del Art. 1°
  (ítem 4). Lo informa el Servicio de Medicina del Trabajo.

Un ítem que no se declaró queda en **sin responder**, nunca en "no": mientras
falte alguno, el borrador no puede presumir que el riesgo sea tolerable.

Y dos detalles de la medición:

- `--ancho-escena` son los metros reales que abarca el ancho de la imagen. Sin
  ese dato no hay metros y la deambulación queda **sin medir**, que es mejor que
  un número inventado: dar los 0 m por buenos haría contestar "0 m/h" y
  disparar el ítem 1 del Paso 2 en cualquier puesto sin calibrar.
- Un minuto que la pose no pudo medir **corta** la bipedestación continua, y lo
  mismo los minutos que falten en el archivo si el proceso estuvo caído: no se
  puede declarar continuo lo que nadie midió.

A 2 cuadros por segundo alcanza: la 2.D habla de horas, no de instantes.

## Controles de calidad de la toma

El informe empieza con una sección **"Calidad de la toma"** y avisa en
**ATENCIÓN** cuando los resultados no son confiables. Sólo se usan para los
ángulos los cuadros que pasan estos controles:

- **De costado:** de costado los dos hombros se superponen en la imagen; de frente o de espaldas quedan separados. Los cuadros que no están de costado se descartan, porque ahí la flexión de tronco, cuello y brazo sale mal medida. Si quedan menos del 70 %, aviso.
- **Cuerpo entero y a buena distancia:** se descartan primeros planos donde no se ven las piernas (el modelo las inventa) y personas tan lejos que el tronco ocupa menos del 6 % del alto de la imagen.
- **Postura posible:** se descartan esqueletos absurdos, como la cadera por encima de los hombros.
- **Cambios de persona o de toma:** cuenta los saltos bruscos del esqueleto (cortes de cámara, o el modelo que pasa a seguir a otra persona) y avisa si hay alguno.

Los videos que OpenCV no lee (por ejemplo AV1, el formato de muchas descargas
de YouTube) se convierten solos si hay `ffmpeg` instalado.

## Limitaciones

- Ángulos en 2D: no se miden giros ni inclinaciones laterales, abducción del brazo, hombro elevado ni desvío lateral de la muñeca. Esas partes del REBA no suman.
- El peso de la carga, el agarre y la fuerza percibida (escala de Borg) los informa quien evalúa.
- REBA castiga también las rodillas muy flexionadas, así que un levantamiento correcto en cuclillas puede salir con puntaje alto. Para juzgar la técnica de levantamiento está la sección 2.A.
- Los metros de la 2.D salen del corrido horizontal de la cadera: el movimiento en profundidad no se ve en 2D, así que son un **piso** del recorrido real. Como el Paso 2.1 se dispara con POCA deambulación, el error empuja a marcar riesgo donde puede no haberlo.
- Estar de pie o sentado sale de la orientación del muslo. De frente a la cámara el muslo se acorta en la imagen y la distinción se vuelve dudosa: otra razón para filmar de costado.
- La carga térmica (Paso 2.3 de la 2.D) necesita termohigrómetro.
- Factores que una cámara no mide (vibraciones, confort térmico) quedan fuera.
- Antes de usarlo en una evaluación real hay que **validarlo** contra la evaluación manual de un ergónomo en algunos puestos.

## Privacidad

Filmar trabajadores es tratamiento de datos personales (Ley 25.326) y un
control del empleador (art. 70 de la Ley de Contrato de Trabajo): avisar a
los trabajadores, filmar sólo lo necesario y borrar el video original después
del análisis. El informe, el CSV y el video del esqueleto no contienen
imágenes de la persona; el video superpuesto sí.

## Pruebas

```bash
python -m pytest ergonomia/tests
```

Usan esqueletos sintéticos con ángulos conocidos y un modelo de pose falso,
así que no necesitan bajar pesos.
