"""Pruebas del banco FEC (rápidas):  python -m unittest compresion/test_banco_fec.py"""
from __future__ import annotations

import itertools, random, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import banco_fec as BF
import canal
import fec
from fec import HAMMING_7_4 as HAM, SECDED_8_4 as SEC, HAMMING_15_11 as HAM15, SECDED_16_11 as SEC16

CODIGOS = ("sin_codigo", "repeticion_5_1", "hamming_7_4", "hamming_15_11", "secded_8_4", "secded_16_11")


def fila(datos, codigo, p):
    return next(f for f in datos["barrido"] if f["codigo"] == codigo and f["p"] == p)


class Determinismo(unittest.TestCase):
    def test_misma_semilla_mismos_resultados(self):
        a = BF.correr(paquetes=20, largo=50, semilla=5, ps=[1e-3, 1e-2])
        b = BF.correr(paquetes=20, largo=50, semilla=5, ps=[1e-3, 1e-2])
        self.assertEqual(a, b)


class SinErrores(unittest.TestCase):
    def test_p_cero_residual_cero_en_todos(self):
        d = BF.correr(paquetes=20, largo=50, semilla=2, ps=[0.0])
        self.assertEqual({f["codigo"] for f in d["barrido"]}, set(CODIGOS))
        for f in d["barrido"]:
            self.assertEqual(f["ber_residual"], 0, f["codigo"])
            self.assertEqual(f["ber_entrada"], 0, f["codigo"])
            self.assertEqual((f["limpios"], f["corregidos_bien"], f["corregidos_mal"]), (20, 0, 0), f["codigo"])


class BER(unittest.TestCase):
    def test_sin_codigo_residual_es_entrada(self):
        d = BF.correr(paquetes=100, largo=200, semilla=1, ps=[1e-2])
        f = fila(d, "sin_codigo", 1e-2)
        self.assertGreater(f["flips"], 0)
        self.assertAlmostEqual(f["ber_residual"], f["ber_entrada"], places=12)

    def test_codigos_bajan_el_ber_a_1e3(self):
        d = BF.correr(paquetes=100, largo=200, semilla=1, ps=[1e-3])
        sin = fila(d, "sin_codigo", 1e-3)["ber_residual"]
        self.assertGreater(sin, 0)
        for cod in ("hamming_7_4", "secded_8_4", "hamming_15_11", "secded_16_11"):
            self.assertLess(fila(d, cod, 1e-3)["ber_residual"], sin, cod)


class Categorias(unittest.TestCase):
    def test_suman_paquetes(self):
        d = BF.correr(paquetes=30, largo=50, semilla=3, ps=[1e-3, 3e-2])
        for f in d["barrido"] + d["rafaga"]:
            total = f["limpios"] + f["corregidos_bien"] + f["corregidos_mal"] + f["no_corregibles"] + f["invalidos"]
            self.assertEqual(total, 30, (f["codigo"], f["p"]))


class Markdown(unittest.TestCase):
    def test_contiene_las_seis_filas_de_codigo_y_la_seccion_de_cable(self):
        md = BF.a_markdown(BF.correr(paquetes=10, largo=40, semilla=1, ps=[1e-3]))
        lineas = md.splitlines()
        for nombre in CODIGOS:
            self.assertTrue(any(l.startswith(f"| {nombre} |") for l in lineas), nombre)
        self.assertIn("## 4. Bytes en el aire por código", md)
        for etiqueta in ("Hamming 15/11", "SECDED 16/11"):
            self.assertIn(etiqueta, md)


class SecdedDirigido(unittest.TestCase):
    def test_un_bit_se_corrige_dos_se_detectan(self):
        datos = random.Random(4).randbytes(40)
        marco = fec.codificar(datos, SEC, secuencia=1)
        # palabras 0..7 son la cabecera (32 bits = 8 palabras de k=4): se usan palabras de datos (>= 8)
        salida, res = fec.decodificar(canal.inyectar(marco, [8 + 10 * 8 + 2]))
        self.assertEqual(BF.clasificar_paquete(datos, salida, res), "corregidos_bien")
        self.assertEqual(salida, datos)
        salida, res = fec.decodificar(canal.inyectar(marco, [8 + 10 * 8 + 2, 8 + 10 * 8 + 6]))
        self.assertEqual(BF.clasificar_paquete(datos, salida, res), "no_corregibles")
        self.assertEqual((res.corregidas, res.no_corregibles), (0, 1))

    def test_secded_nunca_corrige_mal_con_1_o_2_bits(self):
        datos = random.Random(9).randbytes(24)
        marco = fec.codificar(datos, SEC, secuencia=3)
        for palabra in range(8, 20, 3):  # >= 8: fuera de la cabecera
            for k in (1, 2):
                for bits in itertools.combinations(range(8), k):
                    pos = [8 + 8 * palabra + b for b in bits]
                    salida, res = fec.decodificar(canal.inyectar(marco, pos))
                    cat = BF.clasificar_paquete(datos, salida, res)
                    self.assertNotEqual(cat, "corregidos_mal", (palabra, bits))
                    self.assertEqual(cat, "corregidos_bien" if k == 1 else "no_corregibles", (palabra, bits))

    def test_hamming_dos_errores_corrige_mal_sin_aviso(self):
        datos = random.Random(4).randbytes(40)
        marco = fec.codificar(datos, HAM, secuencia=1)
        # bits 3 y 5 de la palabra 10 (bits de mensaje, fuera de la cabecera): s ≠ 0, se "corrige" otro bit
        salida, res = fec.decodificar(canal.inyectar(marco, [8 + 10 * 7 + 3, 8 + 10 * 7 + 5]))
        self.assertEqual(res.no_corregibles, 0)
        self.assertEqual(BF.clasificar_paquete(datos, salida, res), "corregidos_mal")


class Dirigido15y16(unittest.TestCase):
    def test_secded_16_11_detecta_las_120_parejas_de_una_palabra(self):
        datos = random.Random(11).randbytes(40)
        marco = fec.codificar(datos, SEC16, secuencia=5)
        w = 5  # palabra de datos (>= 3: las 3 primeras contienen la cabecera de 32 bits)
        parejas = list(itertools.combinations(range(16), 2))
        self.assertEqual(len(parejas), 120)
        for b1, b2 in parejas:
            salida, res = fec.decodificar(canal.inyectar(marco, [8 + 16 * w + b1, 8 + 16 * w + b2]))
            self.assertEqual(BF.clasificar_paquete(datos, salida, res), "no_corregibles", (b1, b2))
            self.assertEqual((res.corregidas, res.no_corregibles), (0, 1), (b1, b2))

    def test_hamming_15_11_con_dos_errores_corrige_mal_en_alguna_pareja(self):
        datos = random.Random(11).randbytes(40)
        marco = fec.codificar(datos, HAM15, secuencia=5)
        w = 5
        categorias = []
        for b1, b2 in itertools.combinations(range(15), 2):
            salida, res = fec.decodificar(canal.inyectar(marco, [8 + 15 * w + b1, 8 + 15 * w + b2]))
            categorias.append(BF.clasificar_paquete(datos, salida, res))
        self.assertGreater(categorias.count("corregidos_mal"), 0)
        self.assertNotIn("no_corregibles", categorias)  # las 15 columnas de H son todas las síndromes no nulas


class Cable(unittest.TestCase):
    def test_tamano_medido_coincide_con_tamano_en_cable(self):
        for f in BF.tabla_cable():
            self.assertEqual(f["marco"], f["marco_real"], (f["codigo"], f["carga"]))

    def test_factor_1400_cerca_de_n_sobre_k(self):
        filas = [f for f in BF.tabla_cable(cargas=(1400,)) if f["codigo"] in ("hamming_15_11", "secded_16_11",
                                                                             "hamming_7_4", "secded_8_4")]
        self.assertEqual(len(filas), 4)
        for f in filas:
            teorico = f["n"] / f["k"]
            self.assertLess(abs(f["f_marco"] / teorico - 1), 0.05, f["codigo"])
            self.assertLess(abs(f["f_cable"] / teorico - 1), 0.05, f["codigo"])


if __name__ == "__main__":
    unittest.main()
