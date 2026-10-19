"""Junta los cuadros analizados en un informe para el evaluador.

El informe es un BORRADOR de evidencias para las planillas 2.A
(levantamiento), 2.D (bipedestación), 2.E (movimientos repetitivos) y 2.F
(posturas forzadas) del
Protocolo de Ergonomía (Res. SRT 886/2015). No reproduce las planillas
oficiales: aporta las mediciones para que el profesional las complete y firme.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .esqueleto import Angulos
from .levantamiento import detectar_alcances
from .reba import NIVELES, Condiciones, calcular_reba
from .repeticiones import Repeticiones, analizar_repeticiones

# Hombros más separados que esto (en largos de tronco) = la persona no está
# de costado a la cámara y sus ángulos de flexión no se miden bien. Medido en
# videos reales: de costado da 0,0-0,3; de frente o de espaldas, 0,6-1,1.
SEPARACION_HOMBROS_PERFIL = 0.35
# Si el centro de la persona salta más que esto (fracción del ancho o alto de
# la imagen) entre dos cuadros seguidos, cambió la persona seguida o la toma.
SALTO_PERSONA = 0.15
# Por debajo de este % de cuadros de costado, el informe avisa que no es confiable.
PERFIL_MINIMO = 70.0

# Una postura que casi no cambia durante más de esto suma 1 en la actividad REBA.
SEGUNDOS_POSTURA_ESTATICA = 60.0
VARIACION_ESTATICA = 10.0  # grados


@dataclass
class Cuadro:
    tiempo_s: float
    angulos: Angulos | None  # None = no se detectó a la persona
    centro: tuple[float, float] | None = None  # cadera, en fracción del ancho y alto de la imagen
    cuerpo_entero: bool = True  # False = persona cortada, muy lejos o detección dudosa


def de_perfil(a: Angulos) -> bool:
    return a.separacion_hombros is None or a.separacion_hombros <= SEPARACION_HOMBROS_PERFIL


def contar_saltos(cuadros: list[Cuadro]) -> int:
    """Veces que el centro de la persona salta de golpe: otra persona u otra toma."""
    saltos, anterior = 0, None
    for c in cuadros:
        if c.centro is None:
            continue
        if anterior is not None and max(abs(c.centro[0] - anterior[0]), abs(c.centro[1] - anterior[1])) > SALTO_PERSONA:
            saltos += 1
        anterior = c.centro
    return saltos


def _porcentaje(condicion: list[bool]) -> float:
    return 100.0 * sum(condicion) / len(condicion) if condicion else 0.0


def _postura_estatica(cuadros: list[Cuadro], fps: float) -> float:
    """Segundos de la racha más larga con el tronco y el brazo casi quietos."""
    mejor, racha, minimos, maximos = 0, 0, None, None
    for c in cuadros:
        a = c.angulos
        if a is None or a.tronco is None or a.brazo is None:
            racha, minimos = 0, None
            continue
        valores = (a.tronco, a.brazo)
        if minimos is not None:
            nuevos_min = tuple(map(min, minimos, valores))
            nuevos_max = tuple(map(max, maximos, valores))
            if all(hi - lo <= VARIACION_ESTATICA for lo, hi in zip(nuevos_min, nuevos_max)):
                minimos, maximos, racha = nuevos_min, nuevos_max, racha + 1
                mejor = max(mejor, racha)
                continue
        # La racha arranca de nuevo desde este cuadro.
        minimos, maximos, racha = valores, valores, 1
        mejor = max(mejor, racha)
    return mejor / fps if fps > 0 else 0.0


def armar_informe(cuadros: list[Cuadro], fps: float, condiciones: Condiciones,
                  datos: dict | None = None) -> dict:
    """`fps` son los cuadros analizados por segundo (no los del video)."""
    con_persona = [c for c in cuadros if c.angulos is not None and c.angulos.tronco is not None]
    # Los cuadros donde la persona no está de costado se descartan: ahí la
    # flexión de tronco, cuello y brazo sale mal medida (en un video de
    # espaldas, un trabajador agachado daba tronco -15°).
    cuadros_totales = cuadros
    cuadros = [c if c.angulos is None or de_perfil(c.angulos) else Cuadro(c.tiempo_s, None, c.centro, c.cuerpo_entero)
               for c in cuadros]
    medidos = [c.angulos for c in cuadros if c.angulos is not None and c.angulos.tronco is not None]
    duracion = len(cuadros) / fps if fps > 0 else 0.0

    repeticiones = [
        analizar_repeticiones(segmento, [c.angulos and getattr(c.angulos, campo) for c in cuadros], fps)
        for segmento, campo in (("codo", "antebrazo"), ("hombro", "brazo"))
    ]
    principal: Repeticiones = max(repeticiones, key=lambda r: r.ciclos)
    estatica_s = _postura_estatica(cuadros, fps)
    actividad = int(principal.ciclos_por_minuto > 4) + int(estatica_s > SEGUNDOS_POSTURA_ESTATICA)

    rebas = [r for r in (calcular_reba(a, condiciones, actividad) for a in medidos) if r is not None]
    puntajes = np.array([r.puntaje for r in rebas]) if rebas else np.array([])

    def pct(campo, cond):
        valores = [getattr(a, campo) for a in medidos if getattr(a, campo) is not None]
        return round(_porcentaje([cond(v) for v in valores]), 1) if valores else None

    de_pie = [a.sentado is False for a in medidos if a.sentado is not None]
    informe = {
        "datos": datos or {},
        "duracion_s": round(duracion, 1),
        "cuadros_analizados": len(cuadros),
        "cuadros_con_postura": len(medidos),
        "calidad": {
            "porcentaje_de_perfil": round(100.0 * len(medidos) / len(con_persona), 1) if con_persona else None,
            "cuadros_sin_persona": sum(1 for c in cuadros_totales if c.angulos is None and c.cuerpo_entero),
            "cuadros_cuerpo_cortado": sum(1 for c in cuadros_totales if not c.cuerpo_entero),
            "cambios_de_persona_o_toma": contar_saltos(cuadros_totales),
        },
        "condiciones": asdict(condiciones),
        "reba": {
            "maximo": int(puntajes.max()) if len(puntajes) else None,
            "mediana": float(np.median(puntajes)) if len(puntajes) else None,
            "percentil_90": float(np.percentile(puntajes, 90)) if len(puntajes) else None,
            "actividad_sumada": actividad,
            "porcentaje_por_nivel": {
                nombre: round(_porcentaje([r.nivel == nombre for r in rebas]), 1)
                for _, nombre, _ in NIVELES
            },
            "muneca_medida": any(r.muneca_medida for r in rebas),
        },
        "posturas_porcentaje_tiempo": {
            "tronco_flexion_mas_20": pct("tronco", lambda v: v > 20),
            "tronco_flexion_mas_60": pct("tronco", lambda v: v > 60),
            "tronco_extension": pct("tronco", lambda v: v < -5),
            "cuello_flexion_mas_20_o_extension": pct("cuello", lambda v: v > 20 or v < -5),
            # Respecto del tronco, como mide REBA: agachado con los brazos
            # colgando, el brazo queda a ~90° del tronco aunque no lo levante.
            "brazo_mas_45_del_tronco": pct("brazo", lambda v: v > 45),
            "brazo_mas_90_del_tronco": pct("brazo", lambda v: v > 90),
            "muneca_desvio_mas_15": pct("muneca", lambda v: v > 15),
            "rodilla_flexion_mas_60": pct("rodilla", lambda v: v > 60),
            "manos_sobre_hombros": pct("altura_muneca", lambda v: v > 1.0),
        },
        "postura_estatica_mas_larga_s": round(estatica_s, 1),
        "repeticiones": [asdict(r) for r in repeticiones],
        "levantamientos": [asdict(a) for a in detectar_alcances(cuadros)],
        "bipedestacion": {
            "porcentaje_de_pie": round(_porcentaje(de_pie), 1) if de_pie else None,
            "minutos_de_pie": round(sum(de_pie) / fps / 60.0, 1) if de_pie and fps > 0 else None,
        },
    }
    return informe


def _f(valor, sufijo="") -> str:
    if valor is None:
        return "no medido"
    if isinstance(valor, float):
        return f"{valor:.1f}{sufijo}"
    return f"{valor}{sufijo}"


def informe_markdown(inf: dict) -> str:
    r, p, b = inf["reba"], inf["posturas_porcentaje_tiempo"], inf["bipedestacion"]
    rep = max(inf["repeticiones"], key=lambda x: x["ciclos"])
    datos = inf["datos"]
    lineas = [
        "# Informe ergonómico (borrador)",
        "",
        "Herramienta de apoyo al Protocolo de Ergonomía, Res. SRT 886/2015. "
        "Las mediciones son estimaciones por visión artificial: las revisa, "
        "completa y firma un profesional con conocimientos en ergonomía.",
        "",
        "## Datos del análisis",
        "",
    ]
    for clave, valor in datos.items():
        lineas.append(f"- **{clave}:** {valor}")
    lineas += [
        f"- **Duración analizada:** {_f(inf['duracion_s'], ' s')}",
        f"- **Cuadros con la postura medida (de costado):** {inf['cuadros_con_postura']} de {inf['cuadros_analizados']}",
        f"- **Carga informada:** {_f(inf['condiciones']['carga_kg'], ' kg')}, agarre {inf['condiciones']['agarre']}",
        "",
        "## Calidad de la toma",
        "",
    ]
    calidad = inf["calidad"]
    perfil = calidad["porcentaje_de_perfil"]
    avisos = []
    if perfil is None:
        avisos.append("no se detectó a ninguna persona")
    elif perfil < PERFIL_MINIMO:
        avisos.append(f"la persona está de costado sólo en el {perfil:.0f} % de los cuadros con persona")
    if calidad["cambios_de_persona_o_toma"]:
        avisos.append(f"la persona seguida o la toma cambió {calidad['cambios_de_persona_o_toma']} veces")
    if avisos:
        lineas += [
            "**ATENCIÓN: resultados poco confiables.** " + "; ".join(avisos).capitalize() + ". "
            "Para evaluar hace falta una toma fija, de costado, con una sola persona y el cuerpo entero.",
            "",
        ]
    lineas += [
        f"- **De costado a la cámara:** {_f(perfil, ' %')} de los cuadros con persona "
        "(sólo esos se usan para los ángulos)",
        f"- **Cuadros sin persona detectada:** {calidad['cuadros_sin_persona']}",
        f"- **Cuadros descartados por cuerpo cortado, persona muy lejos o postura imposible:** {calidad['cuadros_cuerpo_cortado']}",
        f"- **Cambios bruscos de persona o de toma:** {calidad['cambios_de_persona_o_toma']}",
        "",
        "## REBA",
        "",
        f"- **Máximo:** {_f(r['maximo'])} · **mediana:** {_f(r['mediana'])} · **percentil 90:** {_f(r['percentil_90'])}",
        f"- **Actividad sumada:** +{r['actividad_sumada']} (repetitivo más de 4/min y/o postura estática más de 1 min)",
        "",
        "| Nivel de riesgo | % del tiempo |",
        "|---|---|",
    ]
    for _, nombre, accion in NIVELES:
        lineas.append(f"| {nombre} ({accion}) | {_f(r['porcentaje_por_nivel'][nombre], ' %')} |")
    lineas += [
        "",
        "## Borrador para la planilla 2.A – Levantamiento y descenso manual de cargas",
        "",
    ]
    levantamientos = inf["levantamientos"]
    if levantamientos:
        con_espalda = sum(1 for x in levantamientos if x["tecnica"] == "con la espalda")
        lineas += [
            f"Se detectaron **{len(levantamientos)}** momentos con las manos cerca del piso; "
            f"**{con_espalda}** con la espalda doblada y las piernas casi rectas (técnica a corregir).",
            "",
            "| Momento | Tronco | Rodillas | Técnica |",
            "|---|---|---|---|",
        ]
        lineas += [f"| {x['inicio_s']:.1f}–{x['fin_s']:.1f} s | {x['tronco']:.0f}° | {x['rodilla']:.0f}° | {x['tecnica']} |"
                   for x in levantamientos]
    else:
        lineas.append("No se detectaron momentos con las manos cerca del piso.")
    lineas += [
        "",
        "Ángulos en el punto más bajo de cada momento. **A completar por el evaluador:** "
        "peso de la carga, frecuencia en la jornada y distancia horizontal de agarre.",
        "",
        "## Borrador para la planilla 2.F – Posturas forzadas",
        "",
        "| Postura | % del tiempo |",
        "|---|---|",
        f"| Tronco flexionado más de 20° | {_f(p['tronco_flexion_mas_20'], ' %')} |",
        f"| Tronco flexionado más de 60° | {_f(p['tronco_flexion_mas_60'], ' %')} |",
        f"| Tronco en extensión | {_f(p['tronco_extension'], ' %')} |",
        f"| Cuello flexionado más de 20° o en extensión | {_f(p['cuello_flexion_mas_20_o_extension'], ' %')} |",
        f"| Brazo a más de 45° del tronco | {_f(p['brazo_mas_45_del_tronco'], ' %')} |",
        f"| Brazo a más de 90° del tronco | {_f(p['brazo_mas_90_del_tronco'], ' %')} |",
        f"| Manos por encima de los hombros | {_f(p['manos_sobre_hombros'], ' %')} |",
        f"| Muñeca desviada más de 15° | {_f(p['muneca_desvio_mas_15'], ' %')} |",
        f"| Rodillas flexionadas más de 60° | {_f(p['rodilla_flexion_mas_60'], ' %')} |",
        "",
        f"Postura estática más larga: {_f(inf['postura_estatica_mas_larga_s'], ' s')}.",
        "",
        "## Borrador para la planilla 2.E – Movimientos repetitivos de miembros superiores",
        "",
        f"- **Segmento con más ciclos:** {rep['segmento']}",
        f"- **Ciclos:** {rep['ciclos']} · **por minuto:** {_f(rep['ciclos_por_minuto'])} · "
        f"**duración del ciclo:** {_f(rep['segundos_por_ciclo'], ' s')}",
        f"- **Tiempo en movimiento:** {_f(rep['fraccion_en_movimiento'] * 100, ' %')}",
        f"- **Nivel de Actividad Manual estimado (Radwin 2015):** {_f(rep['nam_estimado'])} de 10",
        "- **A completar por el evaluador:** fuerza percibida (escala de Borg) y "
        "si la tarea dura más de la mitad de la jornada.",
        "",
        "## Borrador para la planilla 2.D – Bipedestación",
        "",
        f"- **De pie:** {_f(b['porcentaje_de_pie'], ' %')} del tiempo analizado "
        f"({_f(b['minutos_de_pie'], ' min')})",
        "",
        "## Limitaciones",
        "",
        "- Cámara de costado y una sola persona en el puesto. De frente los ángulos de tronco, cuello y brazo salen subestimados.",
        "- En 2D no se miden giros ni inclinaciones laterales, abducción del brazo, hombro elevado ni desvío lateral de muñeca: no suman al REBA.",
        "- Peso de la carga, agarre y fuerza los informa el evaluador.",
        "- REBA castiga también las rodillas muy flexionadas: un levantamiento correcto en cuclillas puede salir alto. Para la técnica, ver la sección 2.A.",
        "- El video analizado es una muestra: el evaluador decide si representa la jornada.",
    ]
    if not r["muneca_medida"]:
        lineas.append("- La muñeca no se midió (el modelo usado no detecta los dedos): se tomó como neutra.")
    lineas += ["", "---", "", "Evaluó y firma: ______________________   Matrícula: __________   Fecha: __________", ""]
    return "\n".join(lineas)
