"""Pruebas de la recompensa en bytes del aire (bandido.recompensa_aire) y de que la recompensa
antigua (de carga) no cambia. Los valores exactos están calculados a mano en cada prueba."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bandido as B
import compresores as C
import trafico as T

MODELO = Path(__file__).resolve().parent / "modelo.json"


class RecompensaAntigua(unittest.TestCase):
    """(a) Sin factor_fec, la recompensa y las decisiones son idénticas a las de siempre."""

    def test_recompensa_sin_cambios(self):
        casos = [(128, 127, C.DEFLATE_FLUJO, 0.0, None), (1000, 400, C.ZSTD_DICT if C.ZSTD_DICT in C.CODECS
                 else C.DEFLATE_DICT, 0.0, None), (40, 40, C.NINGUNO, 0.0, None), (0, 0, C.DEFLATE_FLUJO, 0.0, None),
                 (500, 250, C.DEFLATE_FLUJO, 0.001, 1e6), (500, 600, C.DEFLATE_FLUJO, 0.5, 1e4)]
        for lc, ls, cod, t, v in casos:
            esperado = 0.0 if lc == 0 else (
                max(-1.0, (lc - ls) / lc - t * v / (8 * lc)) if v is not None and cod != C.NINGUNO
                else (0.0 if v is not None and cod == C.NINGUNO else
                      (lc - ls) / lc - (0.0 if cod == C.NINGUNO else B.COSTO_CPU)))
            self.assertAlmostEqual(B.recompensa(lc, ls, cod, t, v), esperado, places=12)

    def test_decisiones_identicas_sin_factor(self):
        m = C.Modelo.cargar(MODELO)
        mezcla = T.intercalar(T.conjunto(2, 20, 1, prueba=True), 3)
        em_a = B.EmisorAdaptativo(m)
        em_b = B.EmisorAdaptativo(m, factor_fec=None)
        for _, _, p in mezcla:
            ma, ca = em_a.enviar(p)
            mb, cb = em_b.enviar(p)
            self.assertEqual((ma, ca), (mb, cb))
        self.assertEqual(em_a.elegidos, em_b.elegidos)

    def test_factor_y_velocidad_no_se_combinan(self):
        m = C.Modelo.cargar(MODELO)
        with self.assertRaises(ValueError):
            B.EmisorAdaptativo(m, factor_fec=2.0, velocidad_bps=1e6)


class RecompensaAire(unittest.TestCase):
    """(b) Con factor_fec=2 el mismo ahorro de carga vale más, con los signos de la fórmula."""

    def test_ahorro_chico_pasa_a_positivo(self):
        # aire(L) = 33 + 2·(4+L). aire(128) = 33 + 264 = 297; aire(127) = 33 + 262 = 295.
        # ahorro_aire = 1 − 295/297 = 2/297 ; costo = 0,01/2 = 0,005
        # r = 2/297 − 0,005 = 0,00173400673...   (la antigua: (128−127)/128 − 0,01 = −0,0021875)
        r = B.recompensa_aire(128, 127, C.DEFLATE_FLUJO, 2.0)
        self.assertAlmostEqual(r, 2 / 297 - 0.005, places=12)
        self.assertGreater(r, 0)
        self.assertLess(B.recompensa(128, 127, C.DEFLATE_FLUJO), 0)

    def test_ahorro_mayor_con_factor_2(self):
        # aire(100) = 33 + 2·104 = 241 ; ahorro_aire = 1 − 241/297 = 56/297 ; r = 56/297 − 0,005 = 0,18355218...
        r = B.recompensa_aire(128, 100, C.DEFLATE_FLUJO, 2.0)
        self.assertAlmostEqual(r, 56 / 297 - 0.005, places=12)


class OverheadFijo(unittest.TestCase):
    """(c) El overhead fijo reduce el ahorro relativo en paquetes chicos."""

    def test_mismo_ahorro_absoluto_pesa_menos_en_grande(self):
        # Sin costo de CPU (para aislar el efecto). Mismos 10 B ahorrados de carga en 40→30 y 1000→990.
        # aire(40)=33+44=77, aire(30)=67 -> 10/77 = 0,12987 ; aire(1000)=1037, aire(990)=1027 -> 10/1037 = 0,009643
        chico = B.recompensa_aire(40, 30, C.DEFLATE_FLUJO, 1.0, costo_cpu=0.0)
        grande = B.recompensa_aire(1000, 990, C.DEFLATE_FLUJO, 1.0, costo_cpu=0.0)
        self.assertAlmostEqual(chico, 10 / 77, places=12)
        self.assertAlmostEqual(grande, 10 / 1037, places=12)
        self.assertGreater(chico, 10 * grande)

    def test_sin_overhead_es_solo_la_fraccion_con_encabezado(self):
        # Con overhead 0 y f=1 el ahorro es 1 − (4+Ls)/(4+Lc): 1 − 14/44 para 40→10 (no 0,75).
        r = B.recompensa_aire(40, 10, C.DEFLATE_FLUJO, 1.0, overhead_fijo=0, costo_cpu=0.0)
        self.assertAlmostEqual(r, 1 - 14 / 44, places=12)


class NingunoCero(unittest.TestCase):
    """(d) 'ninguno' siempre da 0, con cualquier factor y cualquier tamaño de salida."""

    def test_ninguno_es_cero(self):
        for f in (1.0, 1.364, 1.75, 2.0):
            for lc, ls in ((40, 40), (1000, 1000), (1000, 400), (40, 90)):
                self.assertEqual(B.recompensa_aire(lc, ls, C.NINGUNO, f), 0.0)

    def test_ahorro_acotado(self):
        # Salida mucho más grande que la carga: el ahorro se recorta en −1 (sin el costo de CPU).
        self.assertEqual(B.recompensa_aire(1, 1000, C.DEFLATE_FLUJO, 1.0, costo_cpu=0.0), -1.0)


if __name__ == "__main__":
    unittest.main()
