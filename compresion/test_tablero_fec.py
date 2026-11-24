"""Prueba del tablero Grafana lab5g-cmp-fec: se genera con dashboards_compresion.py y se valida su estructura."""
import json, subprocess, sys, tempfile, unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GEN = RAIZ / "observability" / "grafana" / "dashboards_compresion.py"
PARTES = ("Significado:", "Cómo funciona:", "Eje X:", "Eje Y:", "A qué responde:")


def balanceada(expr: str) -> bool:
    n = 0
    for c in expr:
        n += (c in "([{") - (c in ")]}")
        if n < 0:
            return False
    return n == 0


import re
sys.path.insert(0, str(Path(__file__).resolve().parent))
import registro_fec  # noqa: E402

EMITIDAS = {l.split("{")[0] for l in registro_fec.CapaFec("hamming_7_4", "dn").texto('lado="dn"').splitlines()}


class TableroFec(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        subprocess.run([sys.executable, str(GEN), cls.tmp.name], check=True, capture_output=True)
        cls.t = json.loads((Path(cls.tmp.name) / "lab5g-cmp-fec.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def paneles(self):
        return [p for p in self.t["panels"] if p["type"] != "row"]

    def test_identidad_y_cantidad(self):
        self.assertEqual(self.t["uid"], "lab5g-cmp-fec")
        self.assertEqual(len(self.paneles()), 7)        # 5 originales + BER de entrada + sobrecoste

    def test_json_del_repo_coincide_con_el_generador(self):
        repo = (GEN.parent / "provisioning/dashboards/json" / "lab5g-cmp-fec.json").read_bytes()
        generado = (Path(self.tmp.name) / "lab5g-cmp-fec.json").read_bytes()
        self.assertEqual(repo, generado, "regenerar con: python observability/grafana/dashboards_compresion.py "
                                         "observability/grafana/provisioning/dashboards/json")

    def test_paneles_nuevos_ber_y_sobrecoste(self):
        pan = {p["title"]: p for p in self.paneles()}
        ber = pan["BER de entrada del canal"]
        sobre = pan["Sobrecoste de la protección"]
        self.assertEqual(ber["type"], "timeseries")
        self.assertEqual(sobre["type"], "timeseries")
        self.assertEqual(ber["fieldConfig"]["defaults"]["custom"]["scaleDistribution"]["type"], "log")
        self.assertEqual(ber["targets"][0]["expr"],
                         'sum(increase(lab5g_cmp_fec_bits_inyectados_total{lado=~"$lado",codigo=~"$codigo"}[$__rate_interval])) / '
                         '(8 * sum(increase(lab5g_cmp_fec_bytes_total{tipo="fec",lado=~"$lado",codigo=~"$codigo"}[$__rate_interval])))')
        self.assertEqual(sobre["targets"][0]["expr"],
                         'sum(increase(lab5g_cmp_fec_bytes_total{tipo="fec",lado=~"$lado",codigo=~"$codigo"}[$__rate_interval])) / '
                         'sum(increase(lab5g_cmp_fec_bytes_total{tipo="comprimido",lado=~"$lado",codigo=~"$codigo"}[$__rate_interval]))')
        for p in (ber, sobre):
            self.assertEqual(p["gridPos"]["w"], 12)
            self.assertGreaterEqual(p["gridPos"]["y"], 24)           # debajo de los actuales
            partes = [s for s in PARTES if s in p["description"]]
            self.assertEqual(partes, list(PARTES), p["title"])            # ficha completa, en orden
            for t in p["targets"]:                                        # rango, no instantánea: sin «No data» falso
                self.assertTrue(t.get("range"))
                self.assertNotIn("last_over_time", t["expr"])

    def test_texto_explica_secded(self):
        texto = next(p for p in self.t["panels"] if p["type"] == "text")["options"]["content"]
        self.assertIn("SECDED", texto)
        self.assertIn("doble detectado", texto)
        self.assertIn("corregido_mal", texto)

    def test_uid_unico(self):
        uids = set()
        for n in ("lab5g-cmp-algoritmo", "lab5g-cmp-comparacion", "lab5g-overview"):
            uids.add(json.loads((GEN.parent / "provisioning/dashboards/json" / f"{n}.json").read_text("utf-8"))["uid"])
        self.assertNotIn(self.t["uid"], uids)

    def test_descripciones_de_cinco_partes(self):
        for p in self.paneles():
            if p["type"] == "text":
                continue
            for parte in PARTES:
                self.assertIn(parte, p.get("description", ""), p["title"])

    def test_promql_balanceado_y_metricas_reales(self):
        for p in self.paneles():
            for t in p.get("targets", []):
                self.assertTrue(balanceada(t["expr"]), t["expr"])
                self.assertIn("lab5g_cmp_fec_", t["expr"])
                for nombre in re.findall(r"lab5g_cmp_fec_\w+", t["expr"]):
                    self.assertIn(nombre, EMITIDAS, nombre)

    def test_corregido_mal_en_los_paneles_de_estado(self):
        self.assertIn("corregido_mal", registro_fec.ESTADOS)
        pan = {p["title"]: p for p in self.paneles()}
        barras = pan["Qué pasó con los paquetes"]
        exprs = [t["expr"] for t in barras["targets"]]
        self.assertEqual(len(exprs), len(registro_fec.ESTADOS))                  # una consulta por estado
        for e in registro_fec.ESTADOS:
            self.assertTrue(any(f'estado="{e}"' in x for x in exprs), e)
        colores = {o["matcher"]["options"]: o["properties"][0]["value"]["fixedColor"]
                   for o in barras["fieldConfig"]["overrides"]}
        self.assertEqual(len(set(colores.values())), len(colores))               # colores todos distintos
        self.assertIn("corregido_mal", colores)
        self.assertIn("corrigió mal", barras["description"])
        self.assertIn("sum by (estado)", pan["Estados en el tiempo"]["targets"][0]["expr"])
        self.assertIn("estado", pan["Códec del bandido por estado"]["targets"][0]["expr"])

    def test_ids_unicos(self):
        ids = [p["id"] for p in self.t["panels"]]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
