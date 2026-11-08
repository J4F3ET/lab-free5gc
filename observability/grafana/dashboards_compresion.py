"""Genera los dos dashboards de compresión de Grafana a partir de las métricas lab5g_cmp_eval_*.

    python observability/grafana/dashboards_compresion.py observability/grafana/provisioning/dashboards/json

  · lab5g-cmp-algoritmo:   un algoritmo a la vez (selector $algoritmo y $clase): original,
                           comprimido, recuperado e información mutua.
  · lab5g-cmp-comparacion: todos los algoritmos lado a lado, más el bandido en vivo.

Los algoritmos salen de la etiqueta `algoritmo` de las métricas, así que uno nuevo registrado
en compresion/compresores.py aparece solo, sin tocar este archivo. El texto de los menús y las
descripciones vienen de lab5g_cmp_eval_algoritmo_info y lab5g_cmp_eval_clase_info (el título y
la descripción de cada clase Codec, más los de informacion.TEXTOS). Los datos los publica
`compresion/evaluar.py informacion --publicar http://10.100.205.14:9091`.
"""
import json, sys
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
SALIDA = Path(sys.argv[1])
M = "lab5g_cmp_eval_"


class Lienzo:
    def __init__(self):
        self.paneles, self.y, self.id = [], 0, 1

    def fila(self, titulo):
        self.paneles.append({"type": "row", "title": titulo, "id": self.id, "collapsed": False,
                             "gridPos": {"h": 1, "w": 24, "x": 0, "y": self.y}})
        self.id += 1; self.y += 1

    def poner(self, paneles, h):
        x = 0
        for p, w in paneles:
            p["id"] = self.id; self.id += 1
            p["gridPos"] = {"h": h, "w": w, "x": x, "y": self.y}
            p.setdefault("datasource", DS)
            x += w
            self.paneles.append(p)
        self.y += h


def q(expr, ref="A", leyenda="", tabla=False):
    t = {"refId": ref, "expr": expr, "instant": True, "datasource": DS}
    if leyenda:
        t["legendFormat"] = leyenda
    if tabla:
        t["format"] = "table"
    return t


def stat(titulo, desc, expr, unidad="none", dec=2, umbrales=None, color="value"):
    return {"type": "stat", "title": titulo, "description": desc, "targets": [q(expr)],
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": color, "graphMode": "none", "textMode": "value", "orientation": "auto"},
            "fieldConfig": {"defaults": {"unit": unidad, "decimals": dec,
                                         "thresholds": umbrales or {"mode": "absolute",
                                                                    "steps": [{"color": "blue", "value": None}]}},
                            "overrides": []}}


def barras(titulo, desc, consultas, unidad="none", dec=2, log=False, x="algoritmo", nombres=None):
    """Bar chart con consultas instantáneas en formato tabla, unidas por `x`."""
    p = {"type": "barchart", "title": titulo, "description": desc,
         "targets": [q(e, chr(65 + i), tabla=True) for i, e in enumerate(consultas)],
         "transformations": [{"id": "merge", "options": {}},
                             {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                            "renameByName": nombres or {}}}],
         "options": {"xField": x, "orientation": "auto", "showValue": "auto", "groupWidth": 0.7,
                     "barWidth": 0.9, "legend": {"showLegend": len(consultas) > 1, "displayMode": "list",
                                                 "placement": "bottom"},
                     "tooltip": {"mode": "multi", "sort": "none"}, "xTickLabelRotation": -30},
         "fieldConfig": {"defaults": {"unit": unidad, "decimals": dec, "color": {"mode": "palette-classic"},
                                      "custom": {"fillOpacity": 80, "lineWidth": 1, "gradientMode": "none",
                                                 "axisSoftMin": 0}},
                         "overrides": []}}
    if log:
        p["fieldConfig"]["defaults"]["custom"]["scaleDistribution"] = {"type": "log", "log": 10}
    return p


def texto(titulo, md):
    return {"type": "text", "title": titulo, "options": {"mode": "markdown", "content": md}}


def variable(nombre, etiqueta, metrica_info, clave, defecto, desc):
    """Menú cuyo valor es la etiqueta `clave` (lo que usan las consultas) y cuyo texto visible es
    la etiqueta `texto` de la métrica *_info que publica informacion.py. Así el menú dice
    "05 · ROHC + DEFLATE con diccionario" y no "deflate_dict"; un algoritmo nuevo trae su texto."""
    consulta = f"query_result({metrica_info})"
    return {"name": nombre, "label": etiqueta, "description": desc, "type": "query", "datasource": DS,
            "query": {"query": consulta, "refId": "v"}, "definition": consulta,
            "regex": f'/{clave}="(?<value>[^"]+)".*texto="(?<text>[^"]+)"/', "refresh": 2,
            "sort": 1, "multi": False, "includeAll": False,
            "current": {"selected": True, "text": defecto, "value": defecto}}


def descripcion(titulo, expr, leyenda):
    """Un stat que muestra como texto la etiqueta `leyenda` de una métrica *_info."""
    t = q(expr, leyenda=leyenda)
    return {"type": "stat", "title": titulo, "targets": [t],
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "none", "graphMode": "none", "textMode": "name", "orientation": "auto",
                        "justifyMode": "center", "text": {"valueSize": 18}},
            "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "text"}}, "overrides": []}}


def tabla_algoritmos():
    """Qué es cada algoritmo: id (como sale en las barras), título y descripción."""
    return {"type": "table", "title": "Qué es cada algoritmo",
            "description": "El id es el nombre que sale en las barras de este tablero.",
            "targets": [q(f"{M}algoritmo_info", tabla=True)],
            "transformations": [{"id": "organize", "options": {
                "excludeByName": {"Time": True, "Value": True, "__name__": True, "instance": True, "job": True},
                "indexByName": {"texto": 0, "algoritmo": 1, "descripcion": 2},
                "renameByName": {"texto": "Algoritmo", "algoritmo": "Id en las barras", "descripcion": "Qué hace"}}},
                {"id": "sortBy", "options": {"sort": [{"field": "Algoritmo"}]}}],
            "options": {"showHeader": True, "cellHeight": "md"},
            "fieldConfig": {"defaults": {"custom": {"align": "left", "cellOptions": {"type": "auto", "wrapText": True}}},
                            "overrides": [
                                {"matcher": {"id": "byName", "options": "Algoritmo"},
                                 "properties": [{"id": "custom.width", "value": 330}]},
                                {"matcher": {"id": "byName", "options": "Id en las barras"},
                                 "properties": [{"id": "custom.width", "value": 170}]}]}}


def tablero(uid, titulo, desc, lienzo, variables):
    return {"uid": uid, "title": titulo, "description": desc, "tags": ["lab5g", "compresion"],
            "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True,
            "refresh": "1m", "time": {"from": "now-6h", "to": "now"},
            "templating": {"list": variables}, "annotations": {"list": []},
            "links": [{"type": "dashboards", "tags": ["compresion"], "asDropdown": True, "title": "Compresión",
                       "includeVars": False}],
            "panels": lienzo.paneles}


SEL = 'algoritmo="$algoritmo",clase="$clase"'
EXPLICA = (
    "**Qué es esto.** El punto (c) del Lab 3 aplicado a la red 5G: para cada algoritmo, el paquete "
    "**original**, el **comprimido** y el **recuperado**, y la **información mutua** I(O;R) entre el "
    "original y lo recuperado.\n\n"
    "- **H** = entropía de orden 0, en bits por byte.\n"
    "- **I(O;R)** con canal perfecto = H(O) en todo algoritmo sin pérdida. Con 1 % de paquetes "
    "perdidos baja: lo que baje de más del ~99 % es daño que agrega el algoritmo (los que guardan "
    "estado por flujo arrastran la pérdida a los paquetes siguientes).\n\n"
    "Datos: `python3 evaluar.py informacion --publicar http://10.100.205.14:9091` (ver "
    "`compresion/informacion.py` y `docs/08-compresion-paquetes.md`). Son de la última corrida, no "
    "series en el tiempo.")

# ═══════════════════════════ 1. por algoritmo ═══════════════════════════
L = Lienzo()
L.poner([(texto("Cómo leer este tablero", EXPLICA), 16),
         (stat("Última evaluación", "Cuándo se publicaron estos datos.",
               f"{M}ultima_corrida_segundos * 1000", "dateTimeFromNow", 0), 8)], 6)
L.poner([(descripcion("Qué hace este algoritmo", f'{M}algoritmo_info{{algoritmo="$algoritmo"}}', "{{descripcion}}"),
          16),
         (descripcion("Clase de tráfico", f'{M}clase_info{{clase="$clase"}}', "{{texto}}"), 8)], 4)
L.fila("Original")
L.poner([(stat("H(O) — entropía del original", "Bits de información por byte del paquete original.",
               f'{M}entropia_bits_por_byte{{{SEL},etapa="original"}}', "none", 3), 12),
         (stat("Bytes originales", "Total de bytes de los paquetes originales.",
               f'{M}bytes{{{SEL},etapa="original"}}', "decbytes", 1), 12)], 5)
L.fila("Comprimido")
L.poner([(stat("Bytes comprimidos", "", f'{M}bytes{{{SEL},etapa="comprimido"}}', "decbytes", 1), 6),
         (stat("Razón de compresión", "Bytes originales / bytes comprimidos.", f'{M}razon{{{SEL}}}', "none", 3), 6),
         (stat("Ahorro", "Porcentaje de bytes ahorrados.", f'{M}ahorro_pct{{{SEL}}}', "percent", 2,
               {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "yellow", "value": 1},
                                              {"color": "green", "value": 15}]}), 6),
         (stat("H(C) — entropía del comprimido", "Cerca de 8 = bytes casi aleatorios: el compresor ya "
               "sacó la redundancia.", f'{M}entropia_bits_por_byte{{{SEL},etapa="comprimido"}}', "none", 3), 6)], 5)
L.fila("Recuperado e información mutua")
pct = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "yellow", "value": 97},
                                     {"color": "green", "value": 99}]}
L.poner([(stat("I(O;R) — canal perfecto", "Información mutua original–recuperado, bits por byte.",
               f'{M}info_mutua_bits_por_byte{{{SEL},canal="perfecto"}}', "none", 3), 6),
         (stat("I(O;R) — 1 % de pérdida", "", f'{M}info_mutua_bits_por_byte{{{SEL},canal="perdida_1pct"}}',
               "none", 3), 6),
         (stat("Información entregada — 1 % de pérdida", "I(O;R) / H(O). Con canal perfecto es 100 %.",
               f'{M}info_entregada_pct{{{SEL},canal="perdida_1pct"}}', "percent", 2, pct, "background"), 6),
         (stat("Paquetes que llegan mal — 1 % de pérdida", "Confiabilidad: % de los que llegan pero salen "
               "distintos del original.", f'{M}mal_reconstruidos_pct{{{SEL},canal="perdida_1pct"}}', "percent", 2,
               {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 0.5},
                                              {"color": "red", "value": 2}]}, "background"), 6)], 5)
L.fila("Velocidad")
L.poner([(stat("Comprimir", "Microsegundos por paquete (Python puro).", f'{M}comprimir_us{{{SEL}}}', "µs", 1), 12),
         (stat("Descomprimir", "", f'{M}descomprimir_us{{{SEL}}}', "µs", 1), 12)], 5)
L.fila("Este algoritmo en cada clase de tráfico")
L.poner([(barras("Entropía por etapa", "H del original, del comprimido y del recuperado con pérdida.",
                 [f'sum by (clase) ({M}entropia_bits_por_byte{{algoritmo="$algoritmo",etapa="original"}})',
                  f'sum by (clase) ({M}entropia_bits_por_byte{{algoritmo="$algoritmo",etapa="comprimido"}})',
                  f'sum by (clase) ({M}info_mutua_bits_por_byte{{algoritmo="$algoritmo",canal="perdida_1pct"}})'],
                 "none", 2, x="clase",
                 nombres={"Value #A": "H(O) original", "Value #B": "H(C) comprimido",
                          "Value #C": "I(O;R) con 1 % de pérdida"}), 12),
         (barras("Ahorro y daño por clase", "",
                 [f'sum by (clase) ({M}ahorro_pct{{algoritmo="$algoritmo"}})',
                  f'sum by (clase) ({M}mal_reconstruidos_pct{{algoritmo="$algoritmo",canal="perdida_1pct"}})'],
                 "percent", 2, x="clase", nombres={"Value #A": "ahorro %", "Value #B": "llegan mal % (1 % pérdida)"}),
          12)], 9)
var_alg = variable("algoritmo", "Algoritmo", f"{M}algoritmo_info", "algoritmo", "bandido_ucb",
                   "Qué algoritmo de compresión mostrar. La descripción sale en el panel de arriba.")
var_clase = variable("clase", "Clase de tráfico", f"{M}clase_info", "clase", "todas",
                     "Una clase de tráfico (slice) o todas mezcladas, como en una celda real.")
d1 = tablero("lab5g-cmp-algoritmo", "lab5g · Compresión — por algoritmo",
             "Original, comprimido, recuperado e información mutua de un algoritmo.", L, [var_alg, var_clase])

# ═══════════════════════════ 2. comparación ═══════════════════════════
L = Lienzo()
C = 'clase="$clase"'
L.poner([(texto("Cómo leer este tablero", EXPLICA + "\n\nCada barra es un algoritmo. `sin_compresion` es la "
                "referencia; `bandido_ucb` es la IA que elige el algoritmo por paquete. La tabla de la derecha "
                "dice qué es cada uno."), 10),
         (tabla_algoritmos(), 14)], 10)
L.fila("Tasa de compresión")
L.poner([(barras("Ahorro de bytes", "", [f'sum by (algoritmo) ({M}ahorro_pct{{{C}}})'], "percent"), 12),
         (barras("Entropía: original vs comprimido", "Un buen compresor sube H(C) hacia 8 bits por byte.",
                 [f'sum by (algoritmo) ({M}entropia_bits_por_byte{{{C},etapa="original"}})',
                  f'sum by (algoritmo) ({M}entropia_bits_por_byte{{{C},etapa="comprimido"}})'], "none", 2,
                 nombres={"Value #A": "H(O)", "Value #B": "H(C)"}), 12)], 9)
# Entropía e I(O;R) viven entre ~7 y 8 bits/byte en la mezcla: con el eje desde 0 la diferencia
# que importa (p. ej. 7,50 vs 7,34 con pérdida) no se ve. Mínimo suave: baja solo si un dato es menor.
EJE7 = (" El eje va de 7 a 8 bits/byte para que se vean las diferencias; en una clase con entropía "
        "menor (URLLC, IoT, texto) baja hasta el menor valor: lee el número de cada barra.")


def eje_desde_7(p):
    p["fieldConfig"]["defaults"]["custom"]["axisSoftMin"] = 7
    p["fieldConfig"]["defaults"]["custom"]["axisSoftMax"] = 8  # tope teórico; deja sitio a los números
    p["options"]["showValue"] = "always"
    p["description"] += EJE7


eje_desde_7(L.paneles[-1])
L.fila("Información mutua y confiabilidad")
L.poner([(barras("I(O;R): canal perfecto vs 1 % de pérdida", "Bits por byte del original que llegan al receptor.",
                 [f'sum by (algoritmo) ({M}info_mutua_bits_por_byte{{{C},canal="perfecto"}})',
                  f'sum by (algoritmo) ({M}info_mutua_bits_por_byte{{{C},canal="perdida_1pct"}})'], "none", 3,
                 nombres={"Value #A": "perfecto", "Value #B": "1 % de pérdida"}), 12),
         (barras("Información entregada con 1 % de pérdida", "I(O;R) / H(O).",
                 [f'sum by (algoritmo) ({M}info_entregada_pct{{{C},canal="perdida_1pct"}})'], "percent", 2), 6),
         (barras("Paquetes que llegan mal (1 % de pérdida)", "",
                 [f'sum by (algoritmo) ({M}mal_reconstruidos_pct{{{C},canal="perdida_1pct"}})'], "percent", 2), 6)], 9)
eje_desde_7(L.paneles[-3])
L.paneles[-2]["fieldConfig"]["defaults"]["custom"]["axisSoftMin"] = 85
L.fila("Velocidad de procesamiento")
L.poner([(barras("Comprimir y descomprimir (µs por paquete, escala log)", "Python puro, en la máquina donde se corrió.",
                 [f'sum by (algoritmo) ({M}comprimir_us{{{C}}})', f'sum by (algoritmo) ({M}descomprimir_us{{{C}}})'],
                 "µs", 1, log=True, nombres={"Value #A": "comprimir", "Value #B": "descomprimir"}), 24)], 9)
L.fila("Resumen")
cols = [("ahorro_pct", "", "Ahorro %"), ("razon", "", "Razón"),
        ("entropia_bits_por_byte", ',etapa="original"', "H(O)"),
        ("entropia_bits_por_byte", ',etapa="comprimido"', "H(C)"),
        ("info_mutua_bits_por_byte", ',canal="perfecto"', "I(O;R) perfecto"),
        ("info_mutua_bits_por_byte", ',canal="perdida_1pct"', "I(O;R) 1 % pérdida"),
        ("info_entregada_pct", ',canal="perdida_1pct"', "Info. entregada %"),
        ("mal_reconstruidos_pct", ',canal="perdida_1pct"', "Llegan mal %"),
        ("comprimir_us", "", "Comprimir µs"), ("descomprimir_us", "", "Descomprimir µs")]
tabla = {"type": "table", "title": "Todos los algoritmos", "description": "Ordenable por cualquier columna.",
         "targets": [q(f'sum by (algoritmo) ({M}{m}{{{C}{f}}})', chr(65 + i), tabla=True)
                     for i, (m, f, _) in enumerate(cols)],
         "transformations": [{"id": "merge", "options": {}},
                             {"id": "organize", "options": {
                                 "excludeByName": {"Time": True},
                                 "renameByName": dict({"algoritmo": "Algoritmo"},
                                                      **{f"Value #{chr(65 + i)}": n for i, (_, _, n) in enumerate(cols)})}}],
         "options": {"showHeader": True, "sortBy": [{"displayName": "Ahorro %", "desc": True}]},
         "fieldConfig": {"defaults": {"decimals": 3, "custom": {"align": "auto"}}, "overrides": []}}
L.poner([(tabla, 24)], 10)
# Con pérdida: no depende de $clase (solo la telemetría iot_json). x = método (predictivo ×escala o un sin pérdida).
TD = M + "td_"
L.fila("Tasa–distorsión (IoT, compresión con pérdida)")
L.poner([(barras("Ahorro de bytes por método", "Solo paquetes iot_json. «predictivo ×e»: PredictivoIoT con los pasos "
                 "de cuantización × e (×0 ≈ sin pérdida). zstd_dict y deflate_flujo: los mejores sin pérdida, como "
                 "referencia.", [f'sum by (metodo) ({TD}ahorro_pct{{canal="perfecto"}})'], "percent", 1, x="metodo"), 8),
         (barras("Información entregada I(X;Y)/H(X)", "Media de temp, hum y bat, sobre los valores (no los bytes). "
                 "Con pérdida baja aun con canal perfecto: es la información que se decide no mandar.",
                 [f'sum by (metodo) ({TD}info_entregada_pct{{canal="perfecto"}})',
                  f'sum by (metodo) ({TD}info_entregada_pct{{canal="perdida_1pct"}})'], "percent", 1, x="metodo",
                 nombres={"Value #A": "perfecto", "Value #B": "1 % de pérdida"}), 8),
         (barras("Dentro de tolerancia (error ≤ paso/2)", "% de paquetes recibidos con todos los campos dentro de la "
                 "tolerancia. Con pérdida, tras un delta perdido el receptor predice mal hasta el siguiente marco "
                 "completo (cada 16).",
                 [f'sum by (metodo) ({TD}dentro_tolerancia_pct{{canal="perfecto"}})',
                  f'sum by (metodo) ({TD}dentro_tolerancia_pct{{canal="perdida_1pct"}})'], "percent", 1, x="metodo",
                 nombres={"Value #A": "perfecto", "Value #B": "1 % de pérdida"}), 8)], 9)
L.poner([(barras("Error máximo por campo (canal perfecto)", "Error absoluto máximo de cada medida.",
                 [f'sum by (metodo) ({TD}error_max{{canal="perfecto",campo="{k}"}})' for k in ("temp", "hum", "bat")],
                 "none", 3, x="metodo", nombres={"Value #A": "temp °C", "Value #B": "hum %", "Value #C": "bat V"}), 12),
         (barras("Bytes por paquete transmitidos", "Marco completo por el túnel (cabecera ROHC + códec + carga); el paquete original IP/UDP/JSON mide ~120 B.",
                 [f'sum by (metodo) ({TD}bytes_por_paquete{{canal="perfecto"}})'], "decbytes", 1, x="metodo"), 12)], 9)
L.fila("En vivo: el bandido en la red 5G (cmp-ue / cmp-dn)")
viv = {"type": "timeseries", "description": "Lo que publica shim.py cada 10 s. Solo hay datos con el perfil "
       "'compresion' arriba y tráfico por cmp0."}
def serie(titulo, consultas, unidad):
    p = dict(viv, title=titulo, targets=[q(e, chr(65 + i), l) for i, (e, l) in enumerate(consultas)])
    for t in p["targets"]:
        t["instant"] = False; t["range"] = True
    p["fieldConfig"] = {"defaults": {"unit": unidad, "custom": {"fillOpacity": 10}}, "overrides": []}
    return p
L.poner([(serie("Subida: bytes originales vs bytes en el túnel", [
            ('rate(lab5g_cmp_bytes{lado="ue",sentido="tx",tipo="orig"}[2m])', "original"),
            ('rate(lab5g_cmp_bytes{lado="ue",sentido="tx",tipo="marco"}[2m])', "comprimido")], "Bps"), 12),
         (serie("Ahorro en vivo (subida)", [
            ('100 * (1 - lab5g_cmp_bytes{lado="ue",sentido="tx",tipo="marco"} / ignoring(tipo) '
             'lab5g_cmp_bytes{lado="ue",sentido="tx",tipo="orig"})', "acumulado")], "percent"), 6),
         (serie("Errores de descompresión", [('lab5g_cmp_errores', "{{lado}}")], "none"), 6)], 8)
L.poner([(serie("Qué algoritmo elige el bandido (paquetes por segundo)", [
            ('rate(lab5g_cmp_paquetes{lado="ue"}[2m])', "{{codec}}")], "pps"), 24)], 8)
d2 = tablero("lab5g-cmp-comparacion", "lab5g · Compresión — comparación de algoritmos",
             "Tasa, información mutua, confiabilidad y velocidad de todos los algoritmos.", L, [var_clase])

for nombre, d in (("lab5g-cmp-algoritmo.json", d1), ("lab5g-cmp-comparacion.json", d2)):
    (SALIDA / nombre).write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(nombre, len(d["panels"]), "paneles")
