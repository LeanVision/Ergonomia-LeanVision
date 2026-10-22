"""Planilla 2.D — Bipedestación, medida sobre la jornada completa.

El resto del repo analiza un video de unos minutos y eso alcanza para la 2.A,
la 2.E y la 2.F. La 2.D no: pregunta por DOS horas seguidas de pie sin poder
sentarse, por TRES horas con deambulación de menos de CIEN metros por hora, o
por DOS horas con portación de cargas (Dec. 49/14, citado en la Guía Práctica
de la SRT). Un clip de celular no puede contestar nada de eso, por buena que
sea la pose: hay que medir la jornada.

De ahí la forma de este módulo: en vez de una lista de cuadros, trabaja sobre
una lista de resúmenes de UN minuto, que es lo que `ergonomia.jornada` va
dejando en disco mientras la cámara mira. Una jornada de 8 h a 2 cuadros por
segundo son 57.600 cuadros pero 480 minutos, y con 480 filas alcanza para
contestar la planilla.

Todo acá es cálculo puro sobre esos resúmenes.
"""

from __future__ import annotations

from dataclasses import dataclass

from .informe import Cuadro

# Umbrales de la planilla, en segundos. No son ajustables: los fija la norma.
DOS_HORAS = 2 * 3600
TRES_HORAS = 3 * 3600
# "Deambulación restringida": menos de 100 m por hora.
METROS_POR_HORA_ESCASOS = 100.0
# Portación de cargas: más de 2 kg.
CARGA_MINIMA_KG = 2.0

# Una pausa más corta que esto no corta una corrida de bipedestación. La norma
# dice "2 horas seguidas" y sentarse medio minuto no es una pausa de
# recuperación; dónde está el corte es criterio del profesional, por eso es
# ajustable.
CORTE_MINIMO_S = 60.0

# Desplazamiento mínimo real entre dos cuadros para contarlo como caminata.
# Sin esto, el temblor del punto de la cadera en alguien parado suma cientos de
# metros por hora e invierte la respuesta del Paso 2.1, que pregunta si
# deambula MENOS de 100 m/h. Medido en largos de tronco no, en metros: la
# escena declarada da la escala.
DEADBAND_M = 0.15


@dataclass
class Minuto:
    """Resumen de un minuto de jornada. Es lo único que se guarda en disco."""

    inicio: float  # epoch del primer cuadro del minuto
    cuadros: int = 0
    medidos: int = 0  # cuadros donde se pudo decir si estaba de pie o sentado
    segundos_de_pie: float = 0.0
    segundos_sentado: float = 0.0
    metros: float = 0.0

    @property
    def cobertura(self) -> float:
        """Fracción del minuto que la pose pudo leer.

        Va en el informe: si la pose vio la mitad de la jornada, el tiempo de
        exposición que se declare es la mitad del real, y eso lo tiene que
        saber quien firma la planilla.
        """
        return self.medidos / self.cuadros if self.cuadros else 0.0

    @property
    def de_pie(self) -> bool | None:
        """Si el minuto cuenta como de pie. None = no se pudo medir.

        Un minuto sin mediciones NO es un minuto de pie: si la cámara estuvo
        tapada tres horas, no se puede declarar que estuvo cinco de pie.
        """
        if not self.medidos:
            return None
        return self.segundos_de_pie >= self.segundos_sentado


def resumir_minuto(inicio: float, cuadros: list[Cuadro], fps: float,
                   ancho_escena_m: float = 0.0) -> Minuto:
    """Junta los cuadros de un minuto en una fila.

    No filtra por `de_perfil`: eso hace falta para los ángulos de flexión, pero
    estar de pie o sentado sale de la orientación del muslo, que se ve igual
    desde cualquier lado. Sí se apoya en los controles de calidad que ya
    aplicó `medir_cuadro` (cuerpo entero a la vista y postura posible).

    `ancho_escena_m` son los metros reales que abarca el ancho de la imagen.
    Sin ese dato no hay metros y la deambulación queda sin medir, que es lo
    correcto: mejor sin dato que con un número inventado.
    """
    minuto = Minuto(inicio=inicio)
    dt = 1.0 / fps if fps > 0 else 0.0
    anterior = None
    for cuadro in cuadros:
        minuto.cuadros += 1
        sentado = cuadro.angulos.sentado if cuadro.angulos is not None else None
        if sentado is None:
            continue
        minuto.medidos += 1
        if sentado:
            minuto.segundos_sentado += dt
        else:
            minuto.segundos_de_pie += dt
        minuto.metros += _metros(anterior, cuadro.centro, ancho_escena_m)
        anterior = cuadro.centro
    return minuto


def _metros(anterior, actual, ancho_escena_m: float) -> float:
    """Metros caminados entre dos cuadros, con deadband.

    Sólo mide el corrido horizontal: con la cámara de costado, la persona
    camina a lo ancho del cuadro, y el movimiento en profundidad no se ve en
    2D. Los metros son entonces un PISO del recorrido real, y como el Paso 2.1
    se dispara con POCA deambulación, el error empuja a marcar riesgo donde
    puede no haberlo. El profesional lo verifica.
    """
    if anterior is None or actual is None or ancho_escena_m <= 0:
        return 0.0
    metros = abs(actual[0] - anterior[0]) * ancho_escena_m
    return metros if metros >= DEADBAND_M else 0.0


def corridas_de_pie(minutos: list[Minuto], corte_minimo_s: float = CORTE_MINIMO_S) -> list[float]:
    """Duraciones en segundos de cada tramo de bipedestación continua.

    Un minuto sin medir corta la corrida: sin saber qué pasó, no se puede
    sumar. Un minuto sentado la corta sólo si la pausa acumulada pasa
    `corte_minimo_s`.
    """
    corridas: list[float] = []
    actual = 0.0
    pausa = 0.0

    def cerrar() -> None:
        nonlocal actual, pausa
        if actual:
            corridas.append(actual)
        actual, pausa = 0.0, 0.0

    for minuto in minutos:
        estado = minuto.de_pie
        if estado is None:
            cerrar()
        elif estado:
            actual += minuto.segundos_de_pie + pausa
            pausa = 0.0
        else:
            pausa += minuto.segundos_sentado
            if pausa > corte_minimo_s:
                cerrar()
    cerrar()
    return corridas


def deambulacion_minima_por_hora(minutos: list[Minuto]) -> float | None:
    """Metros de la hora más quieta de la jornada, en ventanas de 60 minutos.

    Se busca el mínimo y no el promedio porque el Paso 2.1 se dispara con UNA
    hora de poca deambulación, y promediar la jornada la taparía. Las ventanas
    con algún minuto sin medir se descartan: con un hueco adentro, el total de
    metros sale bajo por falta de datos y no porque la persona esté quieta.
    """
    ventanas = [
        sum(m.metros for m in minutos[i:i + 60])
        for i in range(len(minutos) - 59)
        if all(m.medidos for m in minutos[i:i + 60])
    ]
    return min(ventanas) if ventanas else None


def planilla_2d(minutos: list[Minuto], hay_silla: bool = False, carga_kg: float = 0.0,
                corte_minimo_s: float = CORTE_MINIMO_S) -> dict:
    """Borrador de la 2.D, con el número que justifica cada respuesta.

    `hay_silla` y `carga_kg` los informa quien evalúa: la cámara ve que el
    trabajador no se sienta, no si no PUEDE sentarse, y no pesa lo que levanta.
    Son justo los dos datos que la planilla pide y que ninguna medición óptica
    puede dar.

    No dictamina el nivel de riesgo. La Evaluación de Riesgos la hace y la
    rubrica el profesional con conocimiento en ergonomía.
    """
    corridas = corridas_de_pie(minutos, corte_minimo_s)
    mayor = max(corridas, default=0.0)
    deambulacion = deambulacion_minima_por_hora(minutos)
    medidos = sum(m.medidos for m in minutos)
    cuadros = sum(m.cuadros for m in minutos)
    con_carga = carga_kg > CARGA_MINIMA_KG
    return {
        "minutos_registrados": len(minutos),
        "cobertura": round(medidos / cuadros, 3) if cuadros else 0.0,
        "minutos_de_pie": round(sum(m.segundos_de_pie for m in minutos) / 60.0, 1),
        "corrida_de_pie_mas_larga_s": round(mayor),
        "corridas_de_pie_s": [round(c) for c in corridas],
        "deambulacion_minima_m_h": None if deambulacion is None else round(deambulacion, 1),
        "declarado": {"hay_silla": hay_silla, "carga_kg": carga_kg},
        # Paso 1: de pie 2 h seguidas o más, sin posibilidad de sentarse.
        "paso1_de_pie_2h_sin_sentarse": mayor >= DOS_HORAS and not hay_silla,
        # Paso 2.1: 3 h o más de pie con deambulación escasa.
        "paso2_3h_deambulacion_escasa": (
            mayor >= TRES_HORAS
            and deambulacion is not None
            and deambulacion <= METROS_POR_HORA_ESCASOS
        ),
        # Paso 2.2: 2 h o más de pie con portación de cargas de más de 2 kg.
        "paso2_2h_con_carga": mayor >= DOS_HORAS and con_carga,
        # La carga térmica (Paso 2.3) necesita termohigrómetro: no se mide acá.
        "paso2_2h_con_carga_termica": None,
    }


def resumen_markdown(p: dict) -> str:
    """Las líneas de la 2.D para el informe del evaluador."""
    def si_no(valor) -> str:
        return "no medido" if valor is None else ("SÍ" if valor else "no")

    deambulacion = (
        "no medida (hace falta una hora seguida de mediciones y el ancho de la escena declarado)"
        if p["deambulacion_minima_m_h"] is None
        else f"{p['deambulacion_minima_m_h']:.1f} m/h en la hora más quieta")
    horas, minutos_sueltos = divmod(round(p["corrida_de_pie_mas_larga_s"] / 60), 60)
    lineas = [
        "## Borrador para la planilla 2.D – Bipedestación",
        "",
        f"- **Jornada registrada:** {p['minutos_registrados']} min, "
        f"con cobertura de pose del {100 * p['cobertura']:.0f} %",
        f"- **De pie:** {p['minutos_de_pie']:.0f} min en total",
        f"- **Bipedestación continua más larga:** {horas} h {minutos_sueltos:02d} min",
        f"- **Deambulación:** {deambulacion}",
        f"- **Declarado por el evaluador:** silla en el puesto: "
        f"{si_no(p['declarado']['hay_silla'])}; carga manipulada: {p['declarado']['carga_kg']:.1f} kg",
        "",
        f"- Paso 1 — de pie 2 h seguidas sin poder sentarse: **{si_no(p['paso1_de_pie_2h_sin_sentarse'])}**",
        f"- Paso 2.1 — 3 h con deambulación de menos de 100 m/h: **{si_no(p['paso2_3h_deambulacion_escasa'])}**",
        f"- Paso 2.2 — 2 h con portación de cargas de más de 2 kg: **{si_no(p['paso2_2h_con_carga'])}**",
        "- Paso 2.3 — 2 h con exposición a carga térmica: **no medido** "
        "(necesita termohigrómetro)",
    ]
    if p["cobertura"] < 0.7:
        lineas += ["", "**ATENCIÓN:** la pose midió menos del 70 % de los cuadros. El tiempo de "
                   "exposición que surge de acá es un piso del real."]
    return "\n".join(lineas)
