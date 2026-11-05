"""Pruebas del compresor de paquetes:  python -m unittest compresion/test_compresores.py"""
import random, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compresores as C
import trafico as T
from bandido import EmisorAdaptativo

MODELO = C.Modelo.cargar(Path(__file__).with_name("modelo.json"))
PRUEBA = T.conjunto(9, 40, 2, prueba=True)


class IdaYVuelta(unittest.TestCase):
    def test_todo_codec_y_clase_reconstruye_bit_a_bit(self):
        for clase, fs in PRUEBA.items():
            for cod, nom in C.CODECS.items():
                for hc in (False, True):
                    tx, rx = C.Compresor(MODELO, hc, reinicio_flujo=8), C.Descompresor(MODELO)
                    for f in fs:
                        for p in f:
                            marco, _ = tx.empaquetar(p, cod)
                            self.assertEqual(rx.desempaquetar(marco), p, f"{clase} {nom} hc={hc}")

    def test_nunca_expande_mas_que_el_marco(self):
        tx = C.Compresor(MODELO, cabeceras=False)
        for p in PRUEBA["web_tls"][0]:
            for cod in C.CODECS:
                marco, _ = tx.empaquetar(p, cod)
                self.assertLessEqual(len(marco), len(p) + 1)

    def test_emisor_adaptativo(self):
        em, rx = EmisorAdaptativo(MODELO), C.Descompresor(MODELO)
        for _, _, p in T.intercalar(PRUEBA, 1):
            self.assertEqual(rx.desempaquetar(em.enviar(p)[0]), p)


class Estrategias(unittest.TestCase):
    def test_los_ids_del_formato_no_cambian(self):
        self.assertEqual(C.CODECS, {0: "ninguno", 1: "huffman_estatico", 2: "deflate",
                                    3: "deflate_dict", 4: "deflate_flujo", 5: "tic_por_paquete"})

    def test_un_codec_nuevo_se_enchufa_sin_tocar_nada_mas(self):
        @C.registrar
        class Invertir(C.Codec):  # de juguete: no comprime, solo prueba el mecanismo
            id, nombre = 15, "invertir_prueba"

            def comprimir(self, carga, clave):
                return carga[::-1][:-1], None  # 1 byte menos para que no caiga a NINGUNO

            def descomprimir(self, datos):
                return datos[::-1]
        try:
            self.assertEqual(C.CODECS[15], "invertir_prueba")
            tx, rx = C.Compresor(MODELO), C.Descompresor(MODELO)
            p = PRUEBA["iot_json"][0][0]
            marco, usado = tx.empaquetar(p, 15)
            self.assertEqual(usado, 15)
            self.assertNotEqual(rx.desempaquetar(marco), p)  # pierde el último byte, a propósito
        finally:
            del C.REGISTRO[15], C.CODECS[15]

    def test_rechaza_ids_repetidos_o_fuera_de_rango(self):
        for id_ in (C.DEFLATE, 16, -1):
            with self.assertRaises(ValueError):
                C.registrar(type("Malo", (C.Codec,), {"id": id_, "nombre": "malo"}))

    def test_cada_extremo_tiene_su_propio_estado(self):
        a, b = C.Compresor(MODELO), C.Compresor(MODELO)
        self.assertIsNot(a.codecs[C.DEFLATE_FLUJO], b.codecs[C.DEFLATE_FLUJO])


class Balance(unittest.TestCase):
    def test_mg1_coincide_con_md1(self):
        import balance as B
        # servicio fijo S = 1, λ = 0,5: M/D/1 da T = S + λS²/(2(1−ρ)) = 1,5
        self.assertAlmostEqual(B._mg1(0.5, 1.0, 1.0), 1.5)
        self.assertEqual(B._mg1(1.0, 1.0, 1.0), float("inf"))   # ρ = 1: no da abasto

    def test_comprimir_gana_en_enlace_lento_y_pierde_en_rapido(self):
        import balance as B
        ref = {"crudo_bits": 8000.0, "L1": 8000.0, "L2": 8000.0 ** 2, "c1": 0, "c2": 0, "d1": 0, "d2": 0}
        mitad = dict(ref, L1=4000.0, L2=4000.0 ** 2, c1=50e-6, c2=(50e-6) ** 2, d1=10e-6, d2=(10e-6) ** 2)
        v = B.equilibrio(mitad, ref, 0.5)
        self.assertTrue(1e6 < v < 1e9)
        self.assertLess(B.latencia(mitad, v / 10, 0.5, 8000), B.latencia(ref, v / 10, 0.5, 8000))
        self.assertGreater(B.latencia(mitad, v * 10, 0.5, 8000), B.latencia(ref, v * 10, 0.5, 8000))


class Cabeceras(unittest.TestCase):
    def test_voip_baja_de_40_a_5_bytes(self):
        hc = C.CompresorCabeceras()
        largos = [len(hc.comprimir(p)[0]) for p in PRUEBA["voip_rtp"][0]]
        self.assertEqual(largos[0], 42)             # IR: contexto completo
        self.assertEqual(largos[1], 9)              # aún no conoce el paso del timestamp: lo manda
        self.assertTrue(all(l == 5 for l in largos[2:C.REFRESCO]))

    def test_tolera_perdidas_sin_ir(self):
        """Perder paquetes que no son IR no rompe el contexto (bits menos significativos)."""
        tx, rx = C.CompresorCabeceras(), C.DescompresorCabeceras()
        rng = random.Random(0)
        for i, p in enumerate(PRUEBA["voip_rtp"][0]):
            cab, resto = tx.comprimir(p)
            es_ir = cab[0] in (C.IR_RTP, C.IR_UDP)
            if not es_ir and rng.random() < 0.2:
                continue
            pos, armar = rx.descomprimir(cab, 0)
            self.assertEqual(armar(resto), p)


if __name__ == "__main__":
    unittest.main()
