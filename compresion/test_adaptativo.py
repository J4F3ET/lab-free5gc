"""Pruebas de la adaptación FEC: estimador de BER, política con histéresis e informe de 12 bytes."""
import math
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adaptativo
import canal
import fec


class Estimador(unittest.TestCase):
    def test_inicio_en_cero(self):
        e = adaptativo.Estimador()
        self.assertEqual(e.ber, 0.0)
        self.assertEqual(e.muestras, 0)

    def test_palabras_cero_no_rompe_ni_cambia_nada(self):
        e = adaptativo.Estimador()
        e.observar(0, 0, 0, 15)
        self.assertEqual(e.ber, 0.0)
        self.assertEqual(e.muestras, 0)
        e.observar(100, 3, 0, 15)
        ber = e.ber
        e.observar(0, 5, 5, 15)
        self.assertEqual(e.ber, ber)
        self.assertEqual(e.muestras, 100)

    def test_valores_negativos_se_rechazan(self):
        e = adaptativo.Estimador()
        with self.assertRaises(ValueError):
            e.observar(-1, 0, 0, 15)
        with self.assertRaises(ValueError):
            e.observar(10, -1, 0, 15)
        with self.assertRaises(ValueError):
            e.observar(10, 0, 0, 0)
        with self.assertRaises(ValueError):
            adaptativo.Estimador(alfa=0)

    def test_formula_de_un_lote_con_alfa_1(self):
        # bits_mal = 3 + 1 = 4 ; ber = 4 / (100 · 15)
        e = adaptativo.Estimador(alfa=1.0)
        e.observar(100, 3, 1, 15)
        self.assertAlmostEqual(e.ber, 4 / 1500, places=15)
        self.assertEqual(e.muestras, 100)

    def test_media_movil_exponencial_por_lote(self):
        e = adaptativo.Estimador(alfa=0.2)
        e.observar(100, 15, 0, 15)   # lote = 15 / 1500 = 0.01
        self.assertAlmostEqual(e.ber, 0.01, places=12)
        e.observar(100, 0, 0, 15)    # lote sin errores: los 15 errores previos pesan 0.8
        self.assertAlmostEqual(e.ber, 0.8 * 15 / (0.8 * 1500 + 1500), places=12)

    def test_converge_a_1e3_con_bsc_real(self):
        # Codifica con hamming_15_11, inyecta BSC(1e-3) con semilla y decodifica con fec real.
        codigo = fec.buscar("hamming_15_11")
        p = 1e-3
        e = adaptativo.Estimador(alfa=0.2)
        rng = random.Random(1)
        for i in range(300):
            datos = bytes(rng.randrange(256) for _ in range(1400))
            marco = fec.codificar(datos, codigo, secuencia=i)
            pos = canal.bsc(8 * len(marco), p, semilla=i + 1, desde_bit=8)
            recibido = canal.inyectar(marco, pos)
            _, res = fec.decodificar(recibido)
            e.observar(res.palabras, res.sindromes_no_cero, res.no_corregibles, codigo.n)
        self.assertGreater(e.muestras, 300_000)
        self.assertLess(abs(e.ber - p) / p, 0.3, f"estimado {e.ber:.3e}")


class Politica(unittest.TestCase):
    def test_nombres_de_la_escalera_existen_en_fec(self):
        for nombre in adaptativo.ESCALERA:
            self.assertEqual(fec.buscar(nombre).nombre, nombre)

    def test_tabla_por_defecto_en_los_puntos_esperados(self):
        casos = [
            (0.0, "hamming_15_11"),
            (5e-5, "hamming_15_11"),
            (5e-4, "hamming_15_11"),
            (1e-3, "hamming_15_11"),
            (1.5e-3, "secded_16_11"),
            (2e-3, "secded_16_11"),
            (5e-3, "secded_8_4"),
            (2e-2, "secded_8_4"),
            (5e-2, "repeticion_5_1"),
        ]
        for ber, esperado in casos:
            with self.subTest(ber=ber):
                self.assertEqual(adaptativo.escalon(ber), esperado)

    def test_hamming_7_4_no_aparece_en_la_tabla_por_defecto(self):
        for ber in (0.0, 1e-4, 1e-3, 3e-3, 1e-2, 0.1):
            self.assertNotEqual(adaptativo.escalon(ber), "hamming_7_4")

    def test_sube_protección_al_instante(self):
        pol = adaptativo.Politica(inicial="hamming_15_11")
        self.assertEqual(pol.decidir(1.5e-3), "secded_16_11")
        self.assertEqual(pol.decidir(5e-2), "repeticion_5_1")

    def test_baja_solo_tras_espera_evaluaciones(self):
        pol = adaptativo.Politica(inicial="repeticion_5_1", espera=3)
        self.assertEqual(pol.decidir(0.0), "repeticion_5_1")
        self.assertEqual(pol.decidir(0.0), "repeticion_5_1")
        self.assertEqual(pol.decidir(0.0), "hamming_15_11")
        self.assertEqual(pol.actual, "hamming_15_11")

    def test_contador_de_bajada_se_reinicia(self):
        pol = adaptativo.Politica(inicial="secded_8_4", espera=3)
        pol.decidir(0.0)
        pol.decidir(0.0)
        self.assertEqual(pol.decidir(5e-3), "secded_8_4")  # ber alto: se reinicia
        pol.decidir(0.0)
        pol.decidir(0.0)
        self.assertEqual(pol.actual, "secded_8_4")
        self.assertEqual(pol.decidir(0.0), "hamming_15_11")

    def test_no_oscila_con_ber_justo_en_el_umbral(self):
        # Justo en 1e-3 (umbral) no baja: hace falta < 0.5e-3.
        pol = adaptativo.Politica(inicial="secded_16_11", espera=3)
        for _ in range(30):
            self.assertEqual(pol.decidir(1e-3), "secded_16_11")
        # Alternando por debajo y por encima de la banda de histéresis tampoco baja.
        for _ in range(30):
            pol.decidir(4e-4)
            self.assertEqual(pol.decidir(1.2e-3), "secded_16_11")

    def test_inicial_fuera_de_tabla_o_tabla_invalida(self):
        with self.assertRaises(ValueError):
            adaptativo.Politica(inicial="no_existe")
        with self.assertRaises(ValueError):
            adaptativo.Politica(inicial="hamming_7_4")  # no está en la tabla por defecto
        with self.assertRaises(ValueError):
            adaptativo.Politica(tabla=((2e-3, "secded_16_11"), (1e-3, "hamming_15_11")),
                                inicial="secded_16_11")
        with self.assertRaises(ValueError):
            adaptativo.Politica(espera=0)

    def test_tabla_configurable_con_hamming_7_4(self):
        tabla = ((1e-2, "hamming_7_4"), (math.inf, "repeticion_5_1"))
        pol = adaptativo.Politica(tabla=tabla, inicial="hamming_7_4")
        self.assertEqual(pol.decidir(5e-3), "hamming_7_4")
        self.assertEqual(pol.decidir(5e-2), "repeticion_5_1")


class Informe(unittest.TestCase):
    def test_ida_y_vuelta(self):
        datos = adaptativo.codificar_informe(1_234_567, 987_654, 4242)
        self.assertEqual(len(datos), adaptativo.LARGO_INFORME)
        self.assertEqual(datos[:2], adaptativo.MAGIA)
        self.assertEqual(adaptativo.decodificar_informe(datos), (1_234_567, 987_654, 4242))

    def test_valores_extremos_ida_y_vuelta(self):
        datos = adaptativo.codificar_informe(0, 0, 0)
        self.assertEqual(adaptativo.decodificar_informe(datos), (0, 0, 0))
        datos = adaptativo.codificar_informe(0xFFFFFFFF, 0xFFFFFF, 0xFFFF)
        self.assertEqual(adaptativo.decodificar_informe(datos), (0xFFFFFFFF, 0xFFFFFF, 0xFFFF))

    def test_byte_alterado_se_rechaza(self):
        datos = bytearray(adaptativo.codificar_informe(100, 200, 300))
        for i in range(len(datos)):
            alterado = bytearray(datos)
            alterado[i] ^= 0x10
            with self.subTest(indice=i):
                with self.assertRaises(ValueError):
                    adaptativo.decodificar_informe(bytes(alterado))

    def test_magia_mala_se_rechaza_aunque_el_crc_sea_valido(self):
        cuerpo = b"\xF0S" + bytes(9)
        datos = cuerpo + bytes([adaptativo.crc8(cuerpo)])
        with self.assertRaises(ValueError):
            adaptativo.decodificar_informe(datos)

    def test_largo_malo_se_rechaza(self):
        datos = adaptativo.codificar_informe(1, 2, 3)
        for bad in (datos[:-1], datos + b"\x00", b"", b"\xF0R"):
            with self.subTest(largo=len(bad)):
                with self.assertRaises(ValueError):
                    adaptativo.decodificar_informe(bad)

    def test_parametros_fuera_de_rango_se_rechazan(self):
        with self.assertRaises(ValueError):
            adaptativo.codificar_informe(-1, 0, 0)
        with self.assertRaises(ValueError):
            adaptativo.codificar_informe(0, 1 << 24, 0)
        with self.assertRaises(ValueError):
            adaptativo.codificar_informe(0, 0, 65536)

    def test_crc8_vector_conocido(self):
        # CRC-8 (polinomio 0x07, init 0) de "123456789" es 0xF4.
        self.assertEqual(adaptativo.crc8(b"123456789"), 0xF4)


class BerTeorico(unittest.TestCase):
    def test_valores_conocidos(self):
        self.assertAlmostEqual(adaptativo.ber_teorico_palabra_mal(1, 0.3), 0.0, places=12)
        self.assertAlmostEqual(adaptativo.ber_teorico_palabra_mal(15, 0.0), 0.0, places=12)
        self.assertAlmostEqual(adaptativo.ber_teorico_palabra_mal(5, 1.0), 1.0, places=12)
        self.assertAlmostEqual(adaptativo.ber_teorico_palabra_mal(2, 0.5), 0.25, places=12)
        self.assertAlmostEqual(adaptativo.ber_teorico_palabra_mal(3, 0.1), 0.028, places=12)

    def test_coincide_con_suma_binomial(self):
        n, p = 15, 0.02
        suma = sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(2, n + 1))
        self.assertAlmostEqual(adaptativo.ber_teorico_palabra_mal(n, p), suma, places=12)


if __name__ == "__main__":
    unittest.main()
