"""Informe en PDF, a partir del mismo Markdown que informe.md.

Usa fpdf2 (pip install fpdf2). Sólo entiende lo que genera informe_markdown:
títulos (#, ##), viñetas (-), tablas (|), negritas (**) y párrafos. Con las
fuentes que trae el PDF (Helvetica) sólo hay caracteres latinos: los que no
entran se reemplazan.
"""

from __future__ import annotations

from pathlib import Path

_REEMPLAZOS = {"–": "-", "—": "-", "≥": ">=", "≤": "<=", "“": '"', "”": '"', "’": "'"}


def _latin1(texto: str) -> str:
    for a, b in _REEMPLAZOS.items():
        texto = texto.replace(a, b)
    return texto.encode("latin-1", "replace").decode("latin-1")


def _celdas(linea: str) -> list[str]:
    return [c.strip() for c in linea.strip().strip("|").split("|")]


def guardar_pdf(markdown: str, ruta: Path) -> bool:
    """Devuelve False si fpdf2 no está instalado (el resto del informe sale igual)."""
    try:
        from fpdf import FPDF
    except ImportError:
        return False

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)

    lineas = markdown.splitlines()
    i = 0
    while i < len(lineas):
        linea = _latin1(lineas[i])
        if linea.startswith("|"):
            filas = []
            while i < len(lineas) and lineas[i].startswith("|"):
                if not set(lineas[i].replace("|", "").strip()) <= set("-: "):
                    filas.append(_celdas(_latin1(lineas[i])))
                i += 1
            pdf.set_font("Helvetica", size=9)
            with pdf.table(first_row_as_headings=True, line_height=5, markdown=True) as tabla:
                for fila in filas:
                    tabla.row(fila)
            pdf.ln(2)
            pdf.set_font("Helvetica", size=10)
            continue
        if linea.startswith("# "):
            pdf.set_font("Helvetica", "B", 16)
            pdf.multi_cell(0, 9, linea[2:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", size=10)
        elif linea.startswith("## "):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 12)
            pdf.multi_cell(0, 7, linea[3:], new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", size=10)
        elif linea.startswith("- "):
            pdf.multi_cell(0, 5, "  - " + linea[2:], markdown=True, new_x="LMARGIN", new_y="NEXT")
        elif linea.strip() == "---":
            pdf.ln(4)
        elif linea.strip():
            pdf.multi_cell(0, 5, linea, markdown=True, new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.ln(2)
        i += 1
    pdf.output(str(ruta))
    return True
