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
        self.assertEqual(len(self.paneles()), 5)

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

    def test_ids_unicos(self):
        ids = [p["id"] for p in self.t["panels"]]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
