"""Genera los tres dashboards de compresión (el tercero, lab5g-cmp-fec, usa lab5g_cmp_fec_*) de Grafana a partir de las métricas lab5g_cmp_eval_*.

    python observability/grafana/dashboards_compresion.py observability/grafana/provisioning/dashboards/json

  · lab5g-cmp-algoritmo:   un algoritmo a la vez (selector $algoritmo y $clase): original,
                           comprimido, recuperado e información mutua.
  · lab5g-cmp-comparacion: todos los algoritmos lado a lado, más el bandido en vivo.

Cada gráfica explica en su «i» (descripción del panel), siempre con el mismo formato:
Significado · Cómo funciona (la fórmula) · Eje X · Eje Y · A qué responde. Ver ctx().

Los algoritmos salen de la etiqueta `algoritmo` de las métricas, así que uno nuevo registrado
en compresion/compresores.py aparece solo, sin tocar este archivo. El texto de los menús y las
descripciones vienen de lab5g_cmp_eval_algoritmo_info y lab5g_cmp_eval_clase_info (el título y
la descripción de cada clase Codec, más los de informacion.TEXTOS). Los datos los publica
`compresion/evaluar.py informacion --publicar http://10.100.205.14:9091`. Los del bandido en vivo
los publica shim.py (bandido.texto_bandido).
"""
import json, sys
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
SALIDA = Path(sys.argv[1])
M = "lab5g_cmp_eval_"


def ctx(significado, como, eje_x, eje_y, responde):
    """La descripción de cada gráfica, siempre en este formato. Para un número suelto, eje_x=None."""
    if eje_x is None:
        eje_x = "no aplica: es un solo número."
    return (f"**Significado:** {significado}\n\n**Cómo funciona:** {como}\n\n**Eje X:** {eje_x}\n\n"
            f"**Eje Y:** {eje_y}\n\n**A qué responde:** {responde}")


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


def recordar(expr, ventana="30d"):
    """Envuelve cada selector lab5g_* en last_over_time(sel[30d]). Una consulta instantánea solo mira 5 min
    atrás y las lab5g_cmp_eval_* se publican una vez: sin esto el panel dice «No data» aunque el dato exista.
    No toca los selectores que ya llevan rango [..] ni el texto entre comillas."""
    salida, i, n = [], 0, len(expr)
    while i < n:
        c = expr[i]
        if c in "\"'`":                       # literal de texto: se copia tal cual
            j = i + 1
            while j < n and expr[j] != c:
                j += 2 if (expr[j] == "\\" and c != "`") else 1
            salida.append(expr[i:j + 1]); i = j + 1
        elif c.isalpha() or c in "_:":
            j = i
            while j < n and (expr[j].isalnum() or expr[j] in "_:"):
                j += 1
            nombre = expr[i:j]
            if not nombre.startswith("lab5g_"):
                salida.append(nombre); i = j; continue
            k = j
            if k < n and expr[k] == "{":        # llaves: respeta comillas y \" dentro
                k += 1
                while k < n and expr[k] != "}":
                    if expr[k] in "\"'":
                        q_ = expr[k]; k += 1
                        while k < n and expr[k] != q_:
                            k += 2 if expr[k] == "\\" else 1
                    k += 1
                k += 1
            sel = expr[i:k]
            salida.append(sel if (k < n and expr[k] == "[") else f"last_over_time({sel}[{ventana}])")
            i = k
        else:
            salida.append(c); i += 1
    return "".join(salida)


def q(expr, ref="A", leyenda="", tabla=False, instante=True):
    t = {"refId": ref, "expr": recordar(expr) if instante else expr, "instant": instante, "datasource": DS}
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


def barras(titulo, desc, consultas, unidad="none", dec=2, log=False, x="algoritmo", nombres=None, ordenar=False):
    """Bar chart con consultas instantáneas en formato tabla, unidas por `x`.
    ordenar=True (una sola magnitud): barras horizontales de mayor a menor, con su número. Se leen
    como un ranking y los nombres no quedan girados en el celular."""
    tr = [{"id": "merge", "options": {}}]
    if ordenar:
        tr.append({"id": "sortBy", "options": {"sort": [{"field": "Value", "desc": True}]}})
    tr.append({"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": nombres or {}}})
    p = {"type": "barchart", "title": titulo, "description": desc,
         "targets": [q(e, chr(65 + i), tabla=True) for i, e in enumerate(consultas)],
         "transformations": tr,
         "options": {"xField": x, "orientation": "horizontal" if ordenar else "auto",
                     "showValue": "always" if ordenar else "auto", "groupWidth": 0.7,
                     "barWidth": 0.9, "legend": {"showLegend": len(consultas) > 1, "displayMode": "list",
                                                 "placement": "bottom"},
                     "tooltip": {"mode": "multi", "sort": "none"}, "xTickLabelRotation": 0 if ordenar else -30},
         "fieldConfig": {"defaults": {"unit": unidad, "decimals": dec, "color": {"mode": "palette-classic"},
                                      "custom": {"fillOpacity": 80, "lineWidth": 1, "gradientMode": "none",
                                                 "axisSoftMin": 0}},
                         "overrides": []}}
    if log:
        p["fieldConfig"]["defaults"]["custom"]["scaleDistribution"] = {"type": "log", "log": 10}
    return p


def calor(titulo, desc, expr, fila, columna, unidad="percent", dec=1, minimo=0, maximo=100,
          colores="continuous-RdYlGr"):
    """Mapa de calor como tabla: una fila por `fila`, una columna por `columna`, y cada celda
    pintada según su valor. Se lee en el celular y deja ver el número exacto de cada celda."""
    return {"type": "table", "title": titulo, "description": desc, "targets": [q(expr, tabla=True)],
            "transformations": [{"id": "groupingToMatrix", "options": {
                "columnField": columna, "rowField": fila, "valueField": "Value", "emptyValue": "null"}}],
            "options": {"showHeader": True, "cellHeight": "sm"},
            "fieldConfig": {"defaults": {"unit": unidad, "decimals": dec, "min": minimo, "max": maximo,
                                         "color": {"mode": colores},
                                         "custom": {"align": "center", "minWidth": 55,
                                                    "cellOptions": {"type": "color-background", "mode": "basic"}}},
                            "overrides": [{"matcher": {"id": "byType", "options": "string"},
                                           "properties": [{"id": "custom.cellOptions", "value": {"type": "auto"}},
                                                          {"id": "custom.minWidth", "value": 110},
                                                          {"id": "custom.align", "value": "left"}]}]}}


def dispersion(titulo, desc, consulta_x, consulta_y, nombre_x, nombre_y, unidad_x, unidad_y, log_x=False,
               y_min=None, y_max=None, etiqueta="algoritmo", nombre_etiqueta="Algoritmo"):
    """Un punto por algoritmo: x = consulta_x, y = consulta_y, todos en una sola serie. Al tocar un
    punto, el globo dice de qué algoritmo es (Grafana no escribe el nombre junto al punto).
    Se probó separar una serie por algoritmo (partitionByValues) para tener colores en la leyenda:
    en Grafana 13 con modo auto dibujaba solo el primero y en modo manual no dibujaba nada."""
    p = {"type": "xychart", "title": titulo, "description": desc,
         "targets": [q(consulta_x, "A", tabla=True), q(consulta_y, "B", tabla=True)],
         "transformations": [{"id": "merge", "options": {}},
                             {"id": "organize", "options": {"excludeByName": {"Time": True},
                                                            "indexByName": {etiqueta: 0, "Value #A": 1, "Value #B": 2},
                                                            "renameByName": {"Value #A": nombre_x, "Value #B": nombre_y,
                                                                             etiqueta: nombre_etiqueta}}}],
         "options": {"mapping": "auto", "series": [{}],
                     "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"},
                     "tooltip": {"mode": "single", "sort": "none"}},
         "fieldConfig": {"defaults": {"color": {"mode": "fixed", "fixedColor": "green"},
                                      "custom": {"show": "points", "pointSize": {"fixed": 12}, "pointShape": "circle",
                                                 "axisPlacement": "auto"}},
                         "overrides": [{"matcher": {"id": "byName", "options": nombre_x},
                                        "properties": [{"id": "unit", "value": unidad_x}]},
                                       {"matcher": {"id": "byName", "options": nombre_y},
                                        "properties": [{"id": "unit", "value": unidad_y}]}]}}
    for clave, valor in (("custom.axisSoftMin", y_min), ("custom.axisSoftMax", y_max)):
        if valor is not None:
            p["fieldConfig"]["overrides"][1]["properties"].append({"id": clave, "value": valor})
    if log_x:
        p["fieldConfig"]["overrides"][0]["properties"].append(
            {"id": "custom.scaleDistribution", "value": {"type": "log", "log": 10}})
    return p


def texto(titulo, md):
    return {"type": "text", "title": titulo, "options": {"mode": "markdown", "content": md}}


def variable(nombre, etiqueta, metrica_info, clave, defecto, desc):
    """Menú cuyo valor es la etiqueta `clave` (lo que usan las consultas) y cuyo texto visible es
    la etiqueta `texto` de la métrica *_info que publica informacion.py. Así el menú dice
    "05 · ROHC + DEFLATE con diccionario" y no "deflate_dict"; un algoritmo nuevo trae su texto."""
    consulta = f"query_result({recordar(metrica_info)})"
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
    """Qué es cada algoritmo: id (como sale en las gráficas), título y descripción."""
    return {"type": "table", "title": "Qué es cada algoritmo",
            "description": "El id es el nombre que sale en las gráficas de este tablero.",
            "targets": [q(f"{M}algoritmo_info", tabla=True)],
            "transformations": [{"id": "organize", "options": {
                "excludeByName": {"Time": True, "Value": True, "__name__": True, "instance": True, "job": True},
                "indexByName": {"texto": 0, "algoritmo": 1, "descripcion": 2},
                "renameByName": {"texto": "Algoritmo", "algoritmo": "Id en las gráficas", "descripcion": "Qué hace"}}},
                {"id": "sortBy", "options": {"sort": [{"field": "Algoritmo"}]}}],
            "options": {"showHeader": True, "cellHeight": "md"},
            "fieldConfig": {"defaults": {"custom": {"align": "left", "cellOptions": {"type": "auto", "wrapText": True}}},
                            "overrides": [
                                {"matcher": {"id": "byName", "options": "Algoritmo"},
                                 "properties": [{"id": "custom.width", "value": 330}]},
                                {"matcher": {"id": "byName", "options": "Id en las gráficas"},
                                 "properties": [{"id": "custom.width", "value": 170}]}]}}


def tablero(uid, titulo, desc, lienzo, variables):
    return {"uid": uid, "title": titulo, "description": desc, "tags": ["lab5g", "compresion"],
            "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True,
            "refresh": "1m", "time": {"from": "now-6h", "to": "now"},
            "templating": {"list": variables}, "annotations": {"list": []},
            "links": [{"type": "dashboards", "tags": ["compresion"], "asDropdown": True, "title": "Compresión",
                       "includeVars": False}],
            "panels": lienzo.paneles}


# Fórmulas que se repiten en los textos.
F_H = "H = − Σ p(b) · log2 p(b), sumando sobre los 256 valores posibles de un byte; p(b) = qué fracción de los bytes vale b."
F_I = ("I(O;R) = H(O) + H(R) − H(O,R), comparando byte a byte el original O con el recuperado R; un byte que no "
       "llegó cuenta como el símbolo «borrado». Si R = O, da I(O;R) = H(O).")
F_PERD = "el canal pierde 1 de cada 100 paquetes al azar (semilla fija, la misma para todos los algoritmos)."
SEL = 'algoritmo="$algoritmo",clase="$clase"'
EXPLICA = (
    "**Qué es esto.** El punto (c) del Lab 3 aplicado a la red 5G: para cada algoritmo, el paquete "
    "**original** (O), el **comprimido** (C) y el **recuperado** (R), y la **información mutua** I(O;R) "
    "entre el original y lo recuperado.\n\n"
    "- **H** = entropía de orden 0, en bits por byte (de 0 a 8).\n"
    "- **I(O;R)** con canal perfecto = H(O) en todo algoritmo sin pérdida. Con 1 % de paquetes "
    "perdidos baja; **H(O) − I(O;R)** es la información que el receptor ya no tiene.\n"
    "- Cada gráfica explica en su **ⓘ**: significado, cómo funciona, eje X, eje Y y a qué responde.\n\n"
    "Datos: `python3 evaluar.py informacion --publicar http://10.100.205.14:9091` (ver "
    "`compresion/informacion.py` y `docs/08-compresion-paquetes.md`). Son de la última corrida, no "
    "series en el tiempo.")

# ═══════════════════════════ 1. por algoritmo ═══════════════════════════
L = Lienzo()
L.poner([(texto("Cómo leer este tablero", EXPLICA), 16),
         (stat("Última evaluación", ctx("Cuándo se publicaron los datos de este tablero.",
                                        "Hora en que terminó `evaluar.py informacion`.", None, "tiempo transcurrido.",
                                        "¿Estos números son de hoy o de una corrida vieja?"),
               f"{M}ultima_corrida_segundos * 1000", "dateTimeFromNow", 0), 8)], 6)
L.poner([(descripcion("Qué hace este algoritmo", f'{M}algoritmo_info{{algoritmo="$algoritmo"}}', "{{descripcion}}"),
          16),
         (descripcion("Clase de tráfico", f'{M}clase_info{{clase="$clase"}}', "{{texto}}"), 8)], 4)
L.fila("Original")
L.poner([(stat("H(O) — entropía del original", ctx(
                "Cuánta información trae cada byte del paquete original.", F_H, None,
                "bits por byte, de 0 (todos los bytes iguales) a 8 (bytes al azar).",
                "¿Qué tan comprimible es este tráfico? Cerca de 8 (cifrado, voz, video) casi no se puede comprimir."),
               f'{M}entropia_bits_por_byte{{{SEL},etapa="original"}}', "none", 3), 12),
         (stat("Bytes originales", ctx("Tamaño total de los paquetes originales evaluados.",
                                       "Suma de los largos de todos los paquetes de la clase elegida.", None, "bytes.",
                                       "¿Sobre cuántos datos se midió? Es el 100 % contra el que se calcula el ahorro."),
               f'{M}bytes{{{SEL},etapa="original"}}', "decbytes", 1), 12)], 5)
L.fila("Comprimido")
L.poner([(stat("Bytes comprimidos", ctx("Lo que de verdad viaja por la red.",
                                        "Suma de los largos de los marcos: cabecera comprimida + id del códec + carga comprimida.",
                                        None, "bytes.", "¿Cuántos bytes ocupa el tráfico después de comprimir?"),
               f'{M}bytes{{{SEL},etapa="comprimido"}}', "decbytes", 1), 6),
         (stat("Redundancia que sacó: H(C) − H(O)", ctx(
                "Cuánta redundancia por byte le quitó el algoritmo al tráfico.",
                "H(C) − H(O), las dos con " + F_H + " Si es positiva, C se parece más a ruido que O: se sacó "
                "redundancia. Si es negativa, el algoritmo agregó estructura (cabeceras, marcos) sin achicar.",
                None, "bits por byte (0 = no cambió nada).",
                "¿Cuánta redundancia sacó? Junto con el ahorro dice si achicó quitando redundancia o solo cabeceras."),
               f'sum({M}entropia_bits_por_byte{{{SEL},etapa="comprimido"}}) - '
               f'sum({M}entropia_bits_por_byte{{{SEL},etapa="original"}})', "none", 3), 6),
         (stat("Ahorro", ctx("Porcentaje de bytes que ya no viajan.", "ahorro = 100 · (1 − bytes C / bytes O).",
                             None, "% (0 = nada; rojo < 1 %, verde ≥ 15 %).", "¿Vale la pena comprimir esta clase con este algoritmo?"),
               f'{M}ahorro_pct{{{SEL}}}', "percent", 2,
               {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "yellow", "value": 1},
                                              {"color": "green", "value": 15}]}), 6),
         (stat("H(C) — entropía del comprimido", ctx(
                "Qué tan parecido a ruido quedó lo comprimido.", F_H + " Aquí sobre los bytes de C.", None,
                "bits por byte, de 0 a 8.",
                "¿Queda redundancia por sacar? Cerca de 8 = el compresor ya sacó casi todo lo que se podía."),
               f'{M}entropia_bits_por_byte{{{SEL},etapa="comprimido"}}', "none", 3), 6)], 5)
L.fila("Recuperado e información mutua")
pct = {"mode": "absolute", "steps": [{"color": "red", "value": None}, {"color": "yellow", "value": 97},
                                     {"color": "green", "value": 99}]}
L.poner([(stat("Información perdida: H(O) − I(O;R)", ctx(
                "Lo que el receptor ya no puede saber del original cuando la red pierde 1 % de los paquetes.",
                "H(O|R) = H(O) − I(O;R), con H(O) = " + F_H + " I(O;R) = " + F_I + " Aquí " + F_PERD
                + " Con canal perfecto da 0 en todos los algoritmos sin pérdida, porque C es invertible: I(O;C) = H(O).",
                None, "bits por byte del original (0 = no se perdió nada; verde < 0,1, rojo ≥ 0,3).",
                "¿Cuánta información se pierde de verdad? Comparar con «sin_compresion» dice cuánto pone el canal y "
                "cuánto agrega el algoritmo."),
               f'sum({M}entropia_bits_por_byte{{{SEL},etapa="original"}}) - '
               f'sum({M}info_mutua_bits_por_byte{{{SEL},canal="perdida_1pct"}})', "none", 3,
               {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 0.1},
                                              {"color": "red", "value": 0.3}]}, "background"), 6),
         (stat("I(O;R) — 1 % de pérdida", ctx(
                "Información del original que llega cuando la red pierde paquetes.", F_I + " Aquí " + F_PERD, None,
                "bits por byte del original.",
                "¿Cuánta información sobrevive a una red que pierde paquetes? La diferencia con H(O) es la información "
                "perdida, H(O) − I(O;R)."),
               f'{M}info_mutua_bits_por_byte{{{SEL},canal="perdida_1pct"}}', "none", 3), 6),
         (stat("Información entregada — 1 % de pérdida", ctx(
                "Fracción de la información del original que llega.", "100 · I(O;R) / H(O), con " + F_PERD, None,
                "% (verde ≥ 99 %, amarillo ≥ 97 %, rojo menos).",
                "¿El algoritmo agrega daño a la pérdida del canal? Si el canal pierde 1 %, lo esperable es ~99 %; "
                "menos es daño del algoritmo."),
               f'{M}info_entregada_pct{{{SEL},canal="perdida_1pct"}}', "percent", 2, pct, "background"), 6),
         (stat("Paquetes que llegan mal — 1 % de pérdida", ctx(
                "Confiabilidad: paquetes que llegan pero salen distintos del original.",
                "100 · (recibidos que no son idénticos al original) / recibidos, con " + F_PERD, None,
                "% de los paquetes recibidos (verde < 0,5 %, rojo ≥ 2 %).",
                "¿Una pérdida arrastra a otros paquetes? Pasa en los algoritmos que recuerdan los paquetes anteriores "
                "del flujo."),
               f'{M}mal_reconstruidos_pct{{{SEL},canal="perdida_1pct"}}', "percent", 2,
               {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "yellow", "value": 0.5},
                                              {"color": "red", "value": 2}]}, "background"), 6)], 5)
L.fila("Velocidad")
L.poner([(dispersion("Ahorro contra tiempo de CPU, por clase (dispersión)", ctx(
                      "Para el algoritmo elegido: en qué clase de tráfico gasta más CPU y si ese gasto le rinde en "
                      "ahorro. Cada punto es una clase; no depende del menú de clase.",
                      "x = µs de comprimir + µs de descomprimir por paquete (tiempo total / paquetes, Python puro, en la "
                      "máquina de la evaluación); y = 100 · (1 − bytes C / bytes O).",
                      "microsegundos por paquete, en escala logarítmica (cada marca es 10 veces la anterior).",
                      "ahorro %.",
                      "¿Dónde le conviene a este algoritmo trabajar? Arriba a la izquierda: rápido y ahorra. Abajo a la "
                      "derecha: caro y no ahorra. Al tocar un punto sale de qué clase es."),
                     f'sum by (clase) ({M}comprimir_us{{algoritmo="$algoritmo",clase!="todas"}}) + '
                     f'sum by (clase) ({M}descomprimir_us{{algoritmo="$algoritmo",clase!="todas"}})',
                     f'sum by (clase) ({M}ahorro_pct{{algoritmo="$algoritmo",clase!="todas"}})',
                     "µs por paquete", "Ahorro %", "µs", "percent", log_x=True,
                     etiqueta="clase", nombre_etiqueta="Clase"), 24)], 9)
L.fila("Este algoritmo en cada clase de tráfico")
L.poner([(barras("Entropía e información mutua por clase", ctx(
                  "Para el algoritmo elegido: cuánta información trae cada clase, qué tan comprimido queda y cuánta llega "
                  "con pérdida.",
                  "H(O) y H(C) con " + F_H + " I(O;R) con " + F_I, "clase de tráfico.", "bits por byte, de 0 a 8.",
                  "¿En qué clases comprime (H(C) por encima de H(O)) y en cuáles pierde información (I(O;R) por debajo "
                  "de H(O))?"),
                 [f'sum by (clase) ({M}entropia_bits_por_byte{{algoritmo="$algoritmo",etapa="original"}})',
                  f'sum by (clase) ({M}entropia_bits_por_byte{{algoritmo="$algoritmo",etapa="comprimido"}})',
                  f'sum by (clase) ({M}info_mutua_bits_por_byte{{algoritmo="$algoritmo",canal="perdida_1pct"}})'],
                 "none", 2, x="clase",
                 nombres={"Value #A": "H(O) original", "Value #B": "H(C) comprimido",
                          "Value #C": "I(O;R) con 1 % de pérdida"}), 12),
         (dispersion("Ahorro contra daño por clase (dispersión)", ctx(
                      "Para el algoritmo elegido: si ahorrar más en una clase se paga con más paquetes dañados. Cada "
                      "punto es una clase de tráfico.",
                      "x = 100 · (1 − bytes C / bytes O); y = 100 · recibidos distintos del original / recibidos, con "
                      + F_PERD,
                      "ahorro % (más a la derecha = ahorra más).", "paquetes que llegan mal % (más abajo = más confiable).",
                      "¿En qué clase conviene este algoritmo (abajo a la derecha) y en cuál es arriesgado (arriba)? Al "
                      "tocar un punto sale de qué clase es."),
                     f'sum by (clase) ({M}ahorro_pct{{algoritmo="$algoritmo",clase!="todas"}})',
                     f'sum by (clase) ({M}mal_reconstruidos_pct{{algoritmo="$algoritmo",clase!="todas",canal="perdida_1pct"}})',
                     "Ahorro %", "Llegan mal %", "percent", "percent", y_min=0,
                     etiqueta="clase", nombre_etiqueta="Clase"), 12)], 9)
var_alg = variable("algoritmo", "Algoritmo", f"{M}algoritmo_info", "algoritmo", "bandido_ucb",
                   "Qué algoritmo de compresión mostrar. La descripción sale en el panel de arriba.")
var_clase = variable("clase", "Clase de tráfico", f"{M}clase_info", "clase", "todas",
                     "Una clase de tráfico (slice) o todas mezcladas, como en una celda real.")
d1 = tablero("lab5g-cmp-algoritmo", "lab5g · Compresión — por algoritmo",
             "Original, comprimido, recuperado e información mutua de un algoritmo.", L, [var_alg, var_clase])

# ═══════════════════════════ 2. comparación ═══════════════════════════
L = Lienzo()
C = 'clase="$clase"'
TODAS_NO = 'clase!="todas"'
L.poner([(texto("Cómo leer este tablero", EXPLICA + "\n\nCada algoritmo es una barra, una fila o un punto. "
                "`sin_compresion` es la referencia; `bandido_ucb` es la IA que elige el algoritmo por paquete. La "
                "tabla de la derecha dice qué es cada uno. Los mapas de calor muestran todas las clases a la vez; "
                "el resto, la clase del menú."), 10),
         (tabla_algoritmos(), 14)], 10)
L.fila("Tasa de compresión")
L.poner([(calor("Ahorro por algoritmo y clase (mapa de calor)", ctx(
                 "Dónde sirve cada algoritmo.",
                 "cada celda = 100 · (1 − bytes C / bytes O) de ese algoritmo en esa clase. Color de rojo (0 %) a "
                 "verde (80 %).",
                 "clase de tráfico (columnas).", "algoritmo (filas).",
                 "¿Qué algoritmo conviene para cada tipo de tráfico? Una columna toda roja = tráfico que nadie comprime "
                 "(cifrado, voz, video): ahí lo correcto es no comprimir."),
                 f"sum by (algoritmo, clase) ({M}ahorro_pct{{{TODAS_NO}}})", "algoritmo", "clase", maximo=80), 14),
         (barras("Redundancia que sacó: H(C) − H(O)", ctx(
                  "Cuánto se acercó lo comprimido a ser ruido: la redundancia que el algoritmo eliminó.",
                  "H(C) − H(O), con " + F_H + " 0 = no comprimió nada; el máximo posible es 8 − H(O).",
                  "bits por byte (desde 0).", "algoritmo, de más a menos.",
                  "¿Cuánta redundancia saca cada algoritmo? Más largo = deja los bytes más «aleatorios», o sea más "
                  "comprimidos."),
                 [f'sum by (algoritmo) ({M}entropia_bits_por_byte{{{C},etapa="comprimido"}}) - '
                  f'sum by (algoritmo) ({M}entropia_bits_por_byte{{{C},etapa="original"}})'], "none", 3, ordenar=True), 10)], 10)
L.fila("Información mutua y confiabilidad")
EJE7 = (" El eje empieza en 7 bits por byte para que se vean las diferencias; en una clase con entropía "
        "menor (URLLC, IoT, texto) baja hasta el menor valor: lee el número de cada barra.")
im = barras("I(O;R): canal perfecto vs 1 % de pérdida", ctx(
                "Información del original que llega al receptor, sin y con pérdida de paquetes.",
                F_I + " Con pérdida, " + F_PERD + " Información perdida = H(O) − I(O;R): la distancia entre las dos barras.",
                "algoritmo.", "bits por byte del original." + EJE7,
                "¿Qué algoritmo conserva la información cuando la red pierde paquetes? Con canal perfecto todas las "
                "barras son iguales (todos son sin pérdida); con pérdida, la más baja es la que más información pierde."),
            [f'sum by (algoritmo) ({M}info_mutua_bits_por_byte{{{C},canal="perfecto"}})',
             f'sum by (algoritmo) ({M}info_mutua_bits_por_byte{{{C},canal="perdida_1pct"}})'], "none", 3,
            nombres={"Value #A": "perfecto", "Value #B": "1 % de pérdida"})
im["fieldConfig"]["defaults"]["custom"]["axisSoftMin"] = 7
L.poner([(im, 12),
         (calor("Información entregada por algoritmo y clase (mapa de calor)", ctx(
                 "Qué fracción de la información llega, para cada algoritmo en cada clase, con pérdida de paquetes.",
                 "cada celda = 100 · I(O;R) / H(O), con " + F_PERD + " Color de rojo (95 %) a verde (100 %).",
                 "clase de tráfico (columnas).", "algoritmo (filas).",
                 "¿Dónde agrega daño cada algoritmo? Lo esperable es ~99 % (lo que borra el canal); una celda más roja "
                 "es daño del algoritmo en esa clase."),
                 f'sum by (algoritmo, clase) ({M}info_entregada_pct{{{TODAS_NO},canal="perdida_1pct"}})',
                 "algoritmo", "clase", dec=2, minimo=95, maximo=100), 12)], 10)
L.poner([(dispersion("Ahorro contra información entregada (dispersión)", ctx(
                      "El compromiso entre ahorrar bytes y no perder información. Cada punto es un algoritmo.",
                      "x = 100 · (1 − bytes C / bytes O); y = 100 · I(O;R) / H(O) con " + F_PERD,
                      "ahorro % (más a la derecha = ahorra más).", "información entregada % (más arriba = pierde menos).",
                      "¿Cuál ahorra más sin perder información? Los mejores quedan arriba a la derecha. Al tocar un "
                      "punto sale de qué algoritmo es."),
                      f'sum by (algoritmo) ({M}ahorro_pct{{{C}}})',
                      f'sum by (algoritmo) ({M}info_entregada_pct{{{C},canal="perdida_1pct"}})',
                      "Ahorro %", "Información entregada %", "percent", "percent", y_min=95, y_max=100), 14),
         (barras("Paquetes que llegan mal (1 % de pérdida)", ctx(
                  "Confiabilidad: de los paquetes que llegan, cuántos salen distintos del original.",
                  "100 · recibidos distintos del original / recibidos, con " + F_PERD,
                  "% de los paquetes recibidos.", "algoritmo, de más a menos.",
                  "¿Qué algoritmo convierte una pérdida en varios paquetes dañados?"),
                 [f'sum by (algoritmo) ({M}mal_reconstruidos_pct{{{C},canal="perdida_1pct"}})'], "percent", 2,
                 ordenar=True), 10)], 10)
L.fila("Velocidad de procesamiento")
L.poner([(dispersion("Ahorro contra tiempo de CPU (dispersión)", ctx(
                      "Lo que cuesta en CPU cada punto de ahorro. Cada punto es un algoritmo.",
                      "x = µs de comprimir + µs de descomprimir por paquete (Python puro, en la máquina de la "
                      "evaluación); y = 100 · (1 − bytes C / bytes O).",
                      "microsegundos por paquete, en escala logarítmica (cada marca es 10 veces la anterior).",
                      "ahorro %.",
                      "¿Cuál es rápido y además ahorra? Los útiles quedan arriba a la izquierda; abajo a la derecha están "
                      "los lentos que ahorran poco. Al tocar un punto sale de qué algoritmo es."),
                      f'sum by (algoritmo) ({M}comprimir_us{{{C}}}) + sum by (algoritmo) ({M}descomprimir_us{{{C}}})',
                      f'sum by (algoritmo) ({M}ahorro_pct{{{C}}})',
                      "µs por paquete", "Ahorro %", "µs", "percent", log_x=True), 24)], 10)
L.fila("Resumen")
cols = [("ahorro_pct", "", "Ahorro %"), ("razon", "", "Razón"),
        ("entropia_bits_por_byte", ',etapa="original"', "H(O)"),
        ("entropia_bits_por_byte", ',etapa="comprimido"', "H(C)"),
        ("info_mutua_bits_por_byte", ',canal="perfecto"', "I(O;R) perfecto"),
        ("info_mutua_bits_por_byte", ',canal="perdida_1pct"', "I(O;R) 1 % pérdida"),
        ("info_entregada_pct", ',canal="perdida_1pct"', "Info. entregada %"),
        ("mal_reconstruidos_pct", ',canal="perdida_1pct"', "Llegan mal %"),
        ("comprimir_us", "", "Comprimir µs"), ("descomprimir_us", "", "Descomprimir µs")]
consultas = [f'sum by (algoritmo) ({M}{m}{{{C}{f}}})' for m, f, _ in cols]
# Información perdida H(O) − I(O;R): no se publica, se calcula aquí.
consultas.append(f'sum by (algoritmo) ({M}entropia_bits_por_byte{{{C},etapa="original"}}) - '
                 f'sum by (algoritmo) ({M}info_mutua_bits_por_byte{{{C},canal="perdida_1pct"}})')
nombres = [n for _, _, n in cols] + ["H(O) − I(O;R) perdida"]
tabla = {"type": "table", "title": "Todos los algoritmos", "description": ctx(
            "Todas las medidas de la clase elegida, un algoritmo por fila.",
            "las fórmulas de cada columna están en la ⓘ de las gráficas de arriba. La última columna es la información "
            "perdida con 1 % de pérdida: H(O) − I(O;R).",
            "las columnas (se ordena tocando el encabezado).", "un algoritmo por fila.",
            "¿Cómo se compara cada algoritmo en todo a la vez?"),
         "targets": [q(e, chr(65 + i), tabla=True) for i, e in enumerate(consultas)],
         "transformations": [{"id": "merge", "options": {}},
                             {"id": "organize", "options": {
                                 "excludeByName": {"Time": True},
                                 "renameByName": dict({"algoritmo": "Algoritmo"},
                                                      **{f"Value #{chr(65 + i)}": n for i, n in enumerate(nombres)})}}],
         "options": {"showHeader": True, "sortBy": [{"displayName": "Ahorro %", "desc": True}]},
         "fieldConfig": {"defaults": {"decimals": 3, "custom": {"align": "auto"}}, "overrides": []}}
L.poner([(tabla, 24)], 10)
# Con pérdida: no depende de $clase (solo la telemetría iot_json). x = método (predictivo ×escala o un sin pérdida).
TD = M + "td_"
METODO = ("método: «predictivo ×e» = PredictivoIoT con los pasos de cuantización multiplicados por e (×0 ≈ sin "
          "pérdida); zstd_dict y deflate_flujo = los mejores sin pérdida, como referencia.")
L.fila("Tasa–distorsión (IoT, compresión con pérdida)")
L.poner([(dispersion("Curva tasa–distorsión: bytes contra información entregada", ctx(
                      "El compromiso de la compresión con pérdida: cuántos bytes cuesta cada lectura IoT y cuánta "
                      "información de sus valores llega. Cada punto es un método.",
                      "x = bytes de todos los marcos / paquetes (cabecera ROHC + id del códec + carga; el original "
                      "IP/UDP/JSON mide ~120 B); y = 100 · I(X;Y) / H(X) con canal perfecto, X = lectura original, "
                      "Y = recibida, media de temp, hum y bat. " + METODO,
                      "bytes por paquete (más a la izquierda = más barato).",
                      "información entregada % (más arriba = menos distorsión).",
                      "¿Cuánta información se cede por cada byte ahorrado? Los sin pérdida quedan arriba, en 100 %; el "
                      "predictivo se corre a la izquierda y baja a medida que crece el paso. Al tocar un punto sale el "
                      "método."),
                     f'sum by (metodo) ({TD}bytes_por_paquete{{canal="perfecto"}})',
                     f'sum by (metodo) ({TD}info_entregada_pct{{canal="perfecto"}})',
                     "Bytes por paquete", "Información entregada %", "decbytes", "percent", y_max=100,
                     etiqueta="metodo", nombre_etiqueta="Método"), 8),
         (barras("Información entregada I(X;Y)/H(X)", ctx(
                  "Información mutua entre la lectura original y la recibida, sobre los VALORES de temp, hum y bat "
                  "(no los bytes).",
                  "100 · I(X;Y) / H(X), X = lectura original, Y = lectura recibida; media de los tres campos.",
                  METODO, "%.",
                  "¿Cuánta información se decide no mandar? Con pérdida baja aun con canal perfecto: es el precio del "
                  "ahorro."),
                 [f'sum by (metodo) ({TD}info_entregada_pct{{canal="perfecto"}})',
                  f'sum by (metodo) ({TD}info_entregada_pct{{canal="perdida_1pct"}})'], "percent", 1, x="metodo",
                 nombres={"Value #A": "perfecto", "Value #B": "1 % de pérdida"}), 8),
         (barras("Dentro de tolerancia (error ≤ paso/2)", ctx(
                  "Paquetes recibidos con todas sus medidas dentro del error permitido.",
                  "100 · paquetes con |original − recibido| ≤ paso/2 en temp, hum y bat / recibidos.",
                  METODO, "%.",
                  "¿Se cumple el error prometido? Tras un delta perdido el receptor predice mal hasta el siguiente "
                  "marco completo (cada 16 paquetes)."),
                 [f'sum by (metodo) ({TD}dentro_tolerancia_pct{{canal="perfecto"}})',
                  f'sum by (metodo) ({TD}dentro_tolerancia_pct{{canal="perdida_1pct"}})'], "percent", 1, x="metodo",
                 nombres={"Value #A": "perfecto", "Value #B": "1 % de pérdida"}), 8)], 9)
L.poner([(barras("Error máximo por campo (canal perfecto)", ctx(
                  "El peor error de cada medida.", "max |original − recibido| de temp, hum y bat.", METODO,
                  "unidades de cada medida (°C, %, V).",
                  "¿El error queda en paso/2? Es la distorsión que se acepta a cambio de bytes."),
                 [f'sum by (metodo) ({TD}error_max{{canal="perfecto",campo="{k}"}})' for k in ("temp", "hum", "bat")],
                 "none", 3, x="metodo", nombres={"Value #A": "temp °C", "Value #B": "hum %", "Value #C": "bat V"}), 24)], 9)
L.fila("En vivo: el bandido en la red 5G (cmp-ue / cmp-dn)")
NOTA_VIVO = " Lo publica shim.py cada 10 s; solo hay datos con el perfil 'compresion' arriba y tráfico por cmp0."


def serie(titulo, desc, consultas, unidad, por_dia=False):
    p = {"type": "timeseries", "title": titulo, "description": desc,
         "targets": [q(e, chr(65 + i), l, instante=False) for i, (e, l) in enumerate(consultas)]}
    for t in p["targets"]:
        t["instant"] = False; t["range"] = True
    custom = {"fillOpacity": 10}
    if por_dia:
        # Un punto por hora con la ventana [1d]: cada punto es el resumen de las 24 h anteriores.
        # Con paso 1d Grafana evalúa solo a medianoche y no hay nada hasta el primer día completo.
        p["interval"] = "1h"; p["timeFrom"] = "15d"
        for t in p["targets"]:
            t["interval"] = "1h"
    p["fieldConfig"] = {"defaults": {"unit": unidad, "custom": custom}, "overrides": []}
    return p


L.poner([(serie("Subida: bytes originales vs bytes en el túnel", ctx(
                 "Tráfico que entra al compresor y tráfico que sale comprimido, en la red real." + NOTA_VIVO,
                 "tasa de cada contador en ventanas de 2 min: rate(bytes[2m]).", "hora.", "bytes por segundo.",
                 "¿Cuánto tráfico pasa y cuánto se reduce en este momento? La distancia entre las líneas es lo ahorrado."),
                [('rate(lab5g_cmp_bytes{lado="ue",sentido="tx",tipo="orig"}[2m])', "original"),
                 ('rate(lab5g_cmp_bytes{lado="ue",sentido="tx",tipo="marco"}[2m])', "comprimido")], "Bps"), 12),
         (serie("Ahorro en vivo (subida)", ctx(
                 "Ahorro acumulado desde que arrancó el compresor." + NOTA_VIVO,
                 "100 · (1 − bytes en el túnel / bytes originales), con los contadores acumulados.", "hora.", "%.",
                 "¿Cuánto está ahorrando la red real, con el tráfico que de verdad pasa?"),
                [('100 * (1 - lab5g_cmp_bytes{lado="ue",sentido="tx",tipo="marco"} / ignoring(tipo) '
                  'lab5g_cmp_bytes{lado="ue",sentido="tx",tipo="orig"})', "acumulado")], "percent"), 6),
         (serie("Errores de descompresión", ctx(
                 "Marcos que el receptor no pudo descomprimir." + NOTA_VIVO,
                 "contador acumulado de cada lado.", "hora.", "número de errores.",
                 "¿El compresor está dañando tráfico? Un salto suele coincidir con un corte de la red."),
                [('lab5g_cmp_errores', "{{lado}}")], "none"), 6)], 8)
L.poner([(serie("Qué algoritmo elige el bandido (paquetes por segundo)", ctx(
                 "Con qué códec sale cada paquete." + NOTA_VIVO,
                 "tasa de paquetes por códec en ventanas de 2 min: rate(paquetes{codec}[2m]).", "hora.",
                 "paquetes por segundo, una línea por códec.",
                 "¿Qué decide el bandido según el tráfico? Voz, video y HTTPS salen con «ninguno»; texto y JSON, "
                 "comprimidos."),
                [('rate(lab5g_cmp_paquetes{lado="ue"}[2m])', "{{codec}}")], "pps"), 12),
         (calor("Lo que aprendió el bandido: recompensa media por contexto y brazo", ctx(
                 "Lo que el bandido cree que rinde cada códec (brazo) en cada tipo de paquete (contexto). Qué optimiza: "
                 "ahorrar bytes sin gastar CPU en vano. Brazos: ninguno, zstd_dict, deflate_flujo y predictivo_audio "
                 "(este último solo gana con audio PCM sin comprimir)." + NOTA_VIVO,
                 "recompensa de un paquete r = ahorro − 0,01 si comprime (0 si es «ninguno»), con ahorro = (bytes que "
                 "entran − bytes que salen) / bytes que entran. Celda = media reciente S/N, donde S y N se multiplican "
                 "por γ = 0,999 en cada paquete. El bandido manda el brazo de mayor media + 0,02 · √(ln ΣN / N).",
                 "brazo (columnas).",
                 "contexto (filas) = tamaño de la carga (chico < 64 B, mediano 64-511 B, grande ≥ 512 B) / entropía de "
                 "sus primeros 256 B (baja < 0,75, media 0,75-0,9, alta ≥ 0,9).",
                 "¿Por qué el bandido elige lo que elige? En cada fila gana la celda más verde. Negativo = comprimir ahí "
                 "cuesta más de lo que ahorra (tráfico cifrado)."),
                 'sum by (contexto, codec) (label_join(lab5g_cmp_bandido_media{lado="ue"}, "contexto", " / ", "tam", "ent"))',
                 "contexto", "codec", "none", 3, -0.05, 0.8), 12)], 9)
L.poner([(serie("Retroalimentación diaria: ahorro de cada algoritmo con el tráfico real", ctx(
                 "Cuánto ahorró (o habría ahorrado) cada algoritmo con el tráfico real de cada día." + NOTA_VIVO,
                 "1 de cada 100 paquetes reales se comprime además con todos los códecs rápidos, sin mandarlo "
                 "(medición «sombra», bandido.py). Cada hora: 100 · (1 − Δ bytes de ese algoritmo / Δ bytes originales), "
                 "con Δ = lo de las 24 h anteriores a ese momento. Las primeras horas son pocos paquetes y saltan. «bandido_ucb» = lo que de verdad se mandó; «mejor» = el mejor códec de cada "
                 "paquete (techo). Bytes de carga, sin cabeceras.",
                 "tiempo, los últimos 15 días; cada punto resume las 24 h anteriores.", "ahorro %, una línea por algoritmo.",
                 "¿Cómo le fue a cada algoritmo cada día, y qué tan cerca del techo estuvo el bandido? Si el orden de "
                 "las líneas cambia, cambió el tráfico."),
                [('100 * (1 - sum by (codec) (increase(lab5g_cmp_sombra_bytes{lado="ue",codec!="original"}[1d])) / '
                  'scalar(sum(increase(lab5g_cmp_sombra_bytes{lado="ue",codec="original"}[1d]))))', "{{codec}}")],
                "percent", por_dia=True), 24)], 9)
d2 = tablero("lab5g-cmp-comparacion", "lab5g · Compresión — comparación de algoritmos",
             "Tasa, información mutua, confiabilidad y velocidad de todos los algoritmos.", L, [var_clase])

# ═══════════════════════════ 3. codificación de canal (FEC) ═══════════════════════════
# Contadores lab5g_cmp_fec_* que publica shim.py (CapaFec.texto) al pushgateway, con lado=ue|dn y codigo.
# Reinician en 0 si el shim se reinicia: por eso increase()/rate() y no el valor crudo. Los selectores con
# rango [..] los respeta recordar(); el histórico lo conserva el pushgateway.
L = Lienzo()
F = 'lado=~"$lado",codigo=~"$codigo"'
ESTADOS = [("limpio", "#9aa0a6"), ("corregido", "#73BF69"), ("corregido_mal", "#FA6400"),
           ("no_corregible", "#F2495C"), ("invalido", "#B877D9")]
PAQ = "lab5g_cmp_fec_paquetes_total"
NOTA_FEC = " Solo hay datos si el shim tiene la FEC activa (página de envío, pestaña «Laboratorio de síndrome»)."


def color_estado(p, nombres):
    """Un color fijo por estado, para que el mismo estado se vea igual en todos los paneles."""
    p["fieldConfig"]["overrides"] = [
        {"matcher": {"id": "byName", "options": e}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
        for e, c in ESTADOS if e in nombres]
    return p


L.poner([(texto("Cómo leer este tablero",
                "**Idea de clase.** Se envía una palabra código *r = c + e* (e = el error que mete el canal). El receptor "
                "calcula el **síndrome** s = r·Hᵀ = e·Hᵀ: no depende del mensaje, solo del error.\n\n"
                "- **s = 0**: no se ve error (paquete *limpio*).\n"
                "- **s ≠ 0**: s coincide con una **columna de H**, y esa columna dice **qué bit se corrige** "
                "(paquete *corregido*). Con más errores de los que el código aguanta, queda *no_corregible* o *corregido_mal* («corrigió mal»: el síndrome corrigió un bit, pero el paquete no se pudo reconstruir).\n\n"
                "Con **SECDED (8,4)** 2 errores en una palabra se ven como *no_corregible* («doble detectado»), no como "
                "*corregido_mal*: SECDED detecta 2 errores y no los corrige mal.\n\n"
                "Los errores se inyectan desde la página de envío, pestaña **Laboratorio de síndrome**. "
                "Cada panel explica en su **ⓘ**: significado, cómo funciona, eje X, eje Y y a qué responde. "
                "Fórmulas y códigos: `docs/10-codificacion-canal.md`."), 24)], 6)
est = barras("Qué pasó con los paquetes", ctx(
        "Cuántos paquetes recibidos cayeron en cada estado en el rango de tiempo elegido." + NOTA_FEC,
        "increase() del contador de paquetes sumado por estado: limpio = s 0; corregido = s ≠ 0 y se arregló; "
        "corregido_mal = «corrigió mal»: s ≠ 0 y se corrigió un bit, pero el paquete no se pudo reconstruir (p. ej. 2 errores en una palabra Hamming); "
        "no_corregible = el código detecta más errores de los que corrige; invalido = el marco no se pudo leer. "
        "Los contadores reinician con el shim; increase() lo compensa.",
        "estado del paquete.", "número de paquetes en el rango de tiempo.",
        "¿Cómo le va al código con el canal actual? Si «corregido» crece al inyectar errores, la FEC está trabajando; "
        "«corregido_mal» es lo que corrigió mal y «no_corregible» lo que se le escapa."),
        [f'sum(increase({PAQ}{{{F},estado="{e}"}}[$__range]))' for e, _ in ESTADOS], "none", 0, x="estado")
# Una consulta por estado, cada una con su color; las filas se unen por la etiqueta `estado`.
for i, (e, _) in enumerate(ESTADOS):
    est["targets"][i] = q(f'label_replace(sum(increase({PAQ}{{{F},estado="{e}"}}[$__range])), "estado", "{e}", "", "")',
                          chr(65 + i), tabla=True)
est["fieldConfig"]["defaults"]["color"] = {"mode": "fixed", "fixedColor": "#9aa0a6"}
est["options"].update({"orientation": "horizontal", "showValue": "always", "xTickLabelRotation": 0,
                       "legend": {"showLegend": False, "displayMode": "list", "placement": "bottom"}})
est["transformations"][-1]["options"]["renameByName"] = {f"Value #{chr(65 + i)}": e for i, (e, _) in enumerate(ESTADOS)}
est["transformations"][-1]["options"]["excludeByName"] = {"Time": True}
est = color_estado(est, [e for e, _ in ESTADOS])
tiempo = serie("Estados en el tiempo", ctx(
        "Cuántos paquetes por minuto llegan en cada estado, a lo largo del tiempo." + NOTA_FEC,
        "rate() del contador de paquetes por estado en la ventana de gráfica, por 60. Barras apiladas: la altura total "
        "es el tráfico recibido.", "hora.", "paquetes por minuto, apilados por estado.",
        "¿Cuándo se inyectaron errores y cómo respondió el código? Un salto de «corregido», «corregido_mal» o «no_corregible» marca "
        "el momento de la inyección."),
        [(f'sum by (estado) (rate({PAQ}{{{F}}}[$__rate_interval])) * 60', "{{estado}}")], "none")
tiempo["fieldConfig"]["defaults"]["custom"] = {"drawStyle": "bars", "fillOpacity": 70, "lineWidth": 1,
                                               "stacking": {"mode": "normal", "group": "A"}}
tiempo = color_estado(tiempo, [e for e, _ in ESTADOS])
L.poner([(est, 12), (tiempo, 12)], 9)
sind = barras("Síndromes observados", ctx(
        "Cuántas palabras código dieron cada valor de síndrome distinto de cero en el rango elegido." + NOTA_FEC,
        "increase() del contador de síndromes por valor. Cada valor (en binario) es una **columna de H**: la columna "
        "que coincide con s es la posición del bit que el decodificador corrige. s = 0 no se cuenta.",
        "valor del síndrome s, en binario (cada uno = una columna de H).", "número de palabras en el rango de tiempo.",
        "¿Dónde caen los errores? Si un síndrome domina, el canal daña casi siempre el mismo bit; si se reparten, "
        "los errores son uniformes."),
        [f'sum by (sindrome) (increase(lab5g_cmp_fec_sindrome_total{{{F}}}[$__range]))'], "none", 0,
        x="sindrome", ordenar=True)
mapa = calor("Códec del bandido por estado", ctx(
        "Qué códec eligió el bandido y en qué estado terminaron sus paquetes." + NOTA_FEC,
        "celda = increase() del contador de paquetes, por códec (fila) y estado (columna), en el rango elegido. "
        "Color más fuerte = más paquetes.", "estado del paquete (columnas).", "códec que eligió el bandido (filas).",
        "¿El bandido sigue eligiendo códecs frágiles cuando hay errores? Un códec con estado (deflate_flujo) que "
        "acumula «corregido_mal», «no_corregible» o «invalido» se desincroniza más que uno sin estado."),
        f'sum by (codec, estado) (increase({PAQ}{{{F}}}[$__range]))', "codec", "estado", "none", 0, 0, None,
        colores="continuous-BlPu")
del mapa["fieldConfig"]["defaults"]["max"]
L.poner([(sind, 12), (mapa, 12)], 9)
ber = serie("BER de entrada del canal", ctx(
        "Fracción de los bits del aire que el canal volteó (estimación). Incluye también las inyecciones manuales "
        "(«Inyectar 1 bit», «bit exacto») que se hayan armado." + NOTA_FEC,
        "bits invertidos por el canal / bits del marco: increase() de lab5g_cmp_fec_bits_inyectados_total entre "
        "8 × increase() de lab5g_cmp_fec_bytes_total con tipo=\"fec\" (los bytes con paridad que salen al aire).",
        "hora.", "fracción de bits, escala logarítmica.",
        "¿Qué nivel de ruido está viendo el código? Con CANAL=bsc:P este panel debería quedarse cerca de P."),
        [('sum(increase(lab5g_cmp_fec_bits_inyectados_total{lado=~"$lado",codigo=~"$codigo"}[$__rate_interval])) / '
          '(8 * sum(increase(lab5g_cmp_fec_bytes_total{tipo="fec",lado=~"$lado",codigo=~"$codigo"}[$__rate_interval])))',
          "BER de entrada")], "percentunit")
ber["fieldConfig"]["defaults"]["custom"]["scaleDistribution"] = {"type": "log", "log": 10}
sobre = serie("Sobrecoste de la protección", ctx(
        "Cuántos bytes salen al aire por cada byte comprimido: la redundancia que añade la FEC. Hamming (7,4) ≈ 1,75; "
        "SECDED (8,4) ≈ 2; repetición (5,1) = 5, más la cabecera del marco." + NOTA_FEC,
        "bytes con paridad / bytes comprimidos: cociente de dos increase() de lab5g_cmp_fec_bytes_total, "
        "tipo=\"fec\" entre tipo=\"comprimido\", en la ventana de gráfica.",
        "hora.", "veces: bytes al aire por byte comprimido (1 = sin redundancia).",
        "¿Compensa la redundancia frente a lo que ahorró la compresión? Si el sobrecoste se come el ahorro, la FEC "
        "cuesta más de lo que el canal justifica."),
        [('sum(increase(lab5g_cmp_fec_bytes_total{tipo="fec",lado=~"$lado",codigo=~"$codigo"}[$__rate_interval])) / '
          'sum(increase(lab5g_cmp_fec_bytes_total{tipo="comprimido",lado=~"$lado",codigo=~"$codigo"}[$__rate_interval]))',
          "sobrecoste")], "none")
sobre["fieldConfig"]["defaults"]["decimals"] = 2
L.poner([(ber, 12), (sobre, 12)], 9)


def variable_fec(nombre, etiqueta, clave, desc):
    """Menú con todos los valores de la etiqueta `clave`; «Todos» vale «.+» y filtra con =~."""
    consulta = f"query_result(count by ({clave}) ({recordar(PAQ)}))"
    return {"name": nombre, "label": etiqueta, "description": desc, "type": "query", "datasource": DS,
            "query": {"query": consulta, "refId": "v"}, "definition": consulta,
            "regex": f'/{clave}="(?<value>[^"]+)"/', "refresh": 2, "sort": 1, "multi": False,
            "includeAll": True, "allValue": ".+", "current": {"selected": True, "text": "All", "value": "$__all"}}


d3 = tablero("lab5g-cmp-fec", "Codificación de canal (síndrome)",
             "Paquetes limpios, corregidos, corregidos mal y no corregibles por la FEC, y los síndromes observados.", L,
             [variable_fec("lado", "Lado que recibe (ue = bajada, dn = subida)", "lado", "Qué lado del shim mostrar."),
              variable_fec("codigo", "Código", "codigo", "Qué código FEC mostrar (repetición, Hamming).")])
d3["tags"] = ["lab5g", "compresion", "fec"]

for nombre, d in (("lab5g-cmp-algoritmo.json", d1), ("lab5g-cmp-comparacion.json", d2), ("lab5g-cmp-fec.json", d3)):
    (SALIDA / nombre).write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(nombre, len(d["panels"]), "paneles")
