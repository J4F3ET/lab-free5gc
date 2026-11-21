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
        esperado = {0: "ninguno", 1: "huffman_estatico", 2: "deflate", 3: "deflate_dict", 4: "deflate_flujo",
                    5: "tic_por_paquete"}
        if C.ZSTD:
            esperado[6] = "zstd_dict"
        esperado[8] = "predictivo_audio"
        self.assertEqual(C.CODECS, esperado)

    def test_todo_codec_tiene_titulo_y_descripcion(self):
        for cls in C.REGISTRO.values():
            self.assertTrue(cls.titulo and cls.descripcion, cls.nombre)

    @unittest.skipUnless(C.ZSTD, "sin zstd (Python < 3.14 y sin el paquete zstandard)")
    def test_zstd_sin_estado_aguanta_perdidas_y_desorden(self):
        tx, rx = C.Compresor(MODELO), C.Descompresor(MODELO)
        marcos = [(tx.empaquetar(p, C.ZSTD_DICT), p) for p in PRUEBA["http_texto"][0]]
        self.assertTrue(any(u == C.ZSTD_DICT for (_, u), _ in marcos))
        for (marco, _), p in list(reversed(marcos))[::2]:  # la mitad, y al revés
            self.assertEqual(rx.desempaquetar(marco), p)

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


class BandidoPorLatencia(unittest.TestCase):
    def test_recompensa(self):
        from bandido import recompensa
        # 1000 B de carga que quedan en 500, 100 µs de CPU
        self.assertAlmostEqual(recompensa(1000, 500, C.DEFLATE_DICT), 0.49)          # por ahorro, como antes
        self.assertAlmostEqual(recompensa(1000, 500, C.DEFLATE_DICT, 100e-6, 1e6), 0.5 - 0.0125)  # 1 Mbps
        self.assertEqual(recompensa(1000, 500, C.DEFLATE_DICT, 100e-6, 1e9), -1.0)  # 1 Gbps: la CPU pesa más
        self.assertEqual(recompensa(1000, 1000, C.NINGUNO, 0.0, 1e9), 0.0)

    def test_enlace_lento_comprime_y_enlace_rapido_no(self):
        mezcla = T.intercalar(PRUEBA, 1)
        pct_ninguno = {}
        for v in (1e5, 1e11):
            em = EmisorAdaptativo(MODELO, velocidad_bps=v)
            for _, _, p in mezcla:
                em.enviar(p)
            pct_ninguno[v] = em.elegidos.get(C.NINGUNO, 0) / sum(em.elegidos.values())
        self.assertLess(pct_ninguno[1e5], 0.6)
        self.assertGreater(pct_ninguno[1e11], 0.9)


class MetricasGrafana(unittest.TestCase):
    def test_textos_para_los_menus(self):
        import informacion as I
        self.assertEqual(I.textos_algoritmo("deflate_dict")[0], "ROHC + DEFLATE con diccionario")
        self.assertTrue(I.textos_algoritmo("bandido_ucb")[0].startswith("Bandido UCB"))
        for alg in ["sin_compresion", "solo_rohc", "bandido_ucb"] + [n for n in C.CODECS.values() if n != "ninguno"]:
            titulo, desc = I.textos_algoritmo(alg)
            self.assertTrue(titulo and desc and '"' not in titulo + desc, alg)


class ConPerdidaIoT(unittest.TestCase):
    """PredictivoIoT: con pérdida acotada, fuera de CODECS y del bandido."""
    IOT = [p for f in PRUEBA["iot_json"] for p in f]

    def _ida_y_vuelta(self, pasos, perder=()):
        tx, rx = C.Compresor(MODELO, perdida_iot=pasos), C.Descompresor(MODELO)
        est = tx.codecs[C.PREDICTIVO_IOT]
        out, self.clave = [], {}
        for i, p in enumerate(self.IOT):
            ent = est.tx.get(C.clave_flujo(p))  # ¿va marco completo? (sin esquema o tocó reiniciar)
            self.clave[i] = ent is None or ent[1].cuenta >= est.REINICIO
            marco, usado = tx.empaquetar(p, C.PREDICTIVO_IOT)
            self.assertEqual(usado, C.PREDICTIVO_IOT)
            if i not in perder:
                out.append((i, p, rx.desempaquetar(marco)))
        return out

    def test_no_entra_en_codecs_ni_en_el_bandido(self):
        self.assertNotIn(C.PREDICTIVO_IOT, C.CODECS)
        self.assertEqual(C.CODECS_CON_PERDIDA, {C.PREDICTIVO_IOT: "predictivo_iot"})
        self.assertNotIn(C.PREDICTIVO_IOT, EmisorAdaptativo(MODELO).bandido.brazos)

    def test_error_acotado_por_medio_paso(self):
        for escala in (0.5, 1, 4):
            pasos = {k: v * escala for k, v in C.PASOS_IOT.items()}
            for _, p, r in self._ida_y_vuelta(pasos):
                e = C.PredictivoIoT.error_campos(p[28:], r[28:])
                self.assertIsNotNone(e)
                for k, d in pasos.items():
                    self.assertLessEqual(e[k], d / 2 + 1e-9, (escala, k))
                self.assertEqual((e["ts"], e["rssi"]), (0, 0))  # enteros: exactos

    def test_el_paquete_reconstruido_es_un_udp_valido(self):
        for _, p, r in self._ida_y_vuelta(C.PASOS_IOT):
            self.assertEqual(r[:12], p[:2] + r[2:4] + p[4:10] + r[10:12])  # misma IP salvo largo y checksum
            self.assertEqual(C._ck(r[:20]), 0)                              # checksum IP correcto

    def test_con_carga_igual_devuelve_el_mismo_paquete(self):
        for p in self.IOT[:20]:
            self.assertEqual(C._con_carga(p, p[28:]), p)

    def test_se_recupera_en_el_siguiente_paquete_completo(self):
        res = self._ida_y_vuelta(C.PASOS_IOT, perder={3, 4})
        resinc = min(i for i, k in self.clave.items() if k and i > 4)
        self.assertLessEqual(resinc, 4 + C.PredictivoIoT.REINICIO + 1)
        for i, p, r in res:
            if i >= resinc:  # desde el siguiente marco completo: otra vez dentro de tolerancia
                e = C.PredictivoIoT.error_campos(p[28:], r[28:])
                self.assertTrue(all(e[k] <= v / 2 + 1e-9 for k, v in C.PASOS_IOT.items()), i)

    def test_sin_perdida_iot_todo_sigue_exacto(self):
        tx = C.Compresor(MODELO)
        p = self.IOT[0]
        self.assertEqual(tx.preparar(p), (p, None))

    def test_marco_clave_mas_grande_que_el_json_igual_se_acepta(self):
        """Muchos campos numéricos (como el Intel Lab: 6) hacen el marco clave más grande que el
        JSON. Antes eso lo mandaba sin comprimir sin guardar el esquema y nunca había deltas."""
        tx, rx = C.Compresor(MODELO, perdida_iot=C.PASOS_IOT), C.Descompresor(MODELO)
        largos = []
        for i in range(20):
            c = (f'{{"dev":"intel-22","ts":{1077929956 + 31 * i},"seq":{3 + i},"temp":{19.6356 + 0.0123 * i!r},'
                 f'"hum":{37.0246 - 0.031 * i!r},"luz":{82.8 + i!r},"bat":{2.65143 - 1e-5 * i!r}}}').encode()
            p = T.paquete_udp(c, 41022, 5683, i)
            marco, usado = tx.empaquetar(p, C.PREDICTIVO_IOT)
            self.assertEqual(usado, C.PREDICTIVO_IOT)
            r = rx.desempaquetar(marco)
            e = C.PredictivoIoT.error_campos(p[28:], r[28:])
            self.assertTrue(all(e[k] <= v / 2 + 1e-9 for k, v in C.PASOS_IOT.items()), i)
            largos.append(len(marco))
        self.assertLess(sum(largos[1:16]) / 15, 30)   # deltas, no marcos completos


class VariablesDelBandido(unittest.TestCase):
    """Lo que el bandido deja ver para Grafana (bandido.texto_bandido) no cambia lo que manda."""

    def test_la_sombra_no_cambia_ni_un_byte_de_lo_enviado(self):
        mezcla = T.intercalar(PRUEBA, 1)
        sin, con = EmisorAdaptativo(MODELO), EmisorAdaptativo(MODELO, sombra_cada=1)
        for _, _, p in mezcla:
            self.assertEqual(sin.enviar(p)[0], con.enviar(p)[0])
        s = con.sombra
        self.assertEqual(s["paquetes"], len(mezcla))
        # el mejor por paquete nunca es peor que el bandido, ni que ningún códec fijo
        self.assertLessEqual(s["mejor"], s["bandido"])
        self.assertTrue(all(s["mejor"] <= b <= s["original"] for b in s["bytes"].values()))
        self.assertEqual(s["bytes"][C.NINGUNO], s["original"])

    def test_contadores_por_brazo_cuadran(self):
        em = EmisorAdaptativo(MODELO)
        mezcla = T.intercalar(PRUEBA, 1)
        usados = {}
        for _, _, p in mezcla:
            cod = em.enviar(p)[1]
            usados[cod] = usados.get(cod, 0) + 1
        self.assertEqual(sum(v[0] for v in em.por_brazo.values()), len(mezcla))
        self.assertEqual(sum(em.por_contexto.values()), len(mezcla))
        self.assertEqual(em.por_brazo[C.NINGUNO][1], 0)   # "ninguno" no tiene respaldo
        for cod, (elegido, respaldos, entro, salio, _, _) in em.por_brazo.items():
            self.assertLessEqual(salio, entro)
            if cod != C.NINGUNO:                          # lo que no cayó al respaldo salió con ese códec
                self.assertEqual(elegido - respaldos, usados.get(cod, 0))

    def test_el_indice_es_media_mas_bono_y_el_texto_es_prometheus(self):
        import math, re
        from bandido import texto_bandido
        em = EmisorAdaptativo(MODELO, sombra_cada=4)
        for _, _, p in T.intercalar(PRUEBA, 1):
            em.enviar(p)
        b = em.bandido
        for ctx, (N, S) in b.estado.items():
            for (n, media, indice), nj, sj in zip(b.indices(ctx), N, S):
                if nj > 0:
                    self.assertAlmostEqual(media, sj / nj)
                    self.assertAlmostEqual(indice, sj / nj + b.c * math.sqrt(max(math.log(sum(N)), 0) / nj))
        linea = re.compile(r'^lab5g_cmp_[a-z_]+\{lado="ue"(,[a-z]+="[^"]*")*\} -?[0-9.]+(e[+-]?\d+)?$')
        texto = texto_bandido(em, 'lado="ue"')
        self.assertTrue(all(linea.match(l) for l in texto.splitlines()), texto)
        for nombre in ("elegidos", "respaldos", "carga_bytes", "cpu_segundos", "recompensa_suma", "contexto_paquetes",
                       "bandido_n", "bandido_media", "bandido_indice", "sombra_bytes", "sombra_paquetes"):
            self.assertIn(f"lab5g_cmp_{nombre}{{", texto)


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


def _pcm(n_muestras: int, semilla: int = 3) -> bytes:
    """Audio de prueba: dos senos con ruido, PCM 16 bit little-endian."""
    import math, struct
    rng = random.Random(semilla)
    return b"".join(struct.pack("<h", max(-32768, min(32767, int(9000 * math.sin(i / 7) + 4000 * math.sin(i / 23)
                                                                  + rng.gauss(0, 150))))) for i in range(n_muestras))


class PredictivoAudio(unittest.TestCase):
    def test_ida_y_vuelta_con_cualquier_corte_y_largo(self):
        c = C.REGISTRO[C.PREDICTIVO_AUDIO](MODELO)
        pcm, rng = _pcm(20000), random.Random(9)
        for _ in range(300):
            i, n = rng.randrange(len(pcm) - 1500), rng.randrange(16, 1400)  # cortes pares e impares
            s = pcm[i:i + n]
            z, conf = c.comprimir(s, None)
            self.assertIsNone(conf)                       # sin estado
            if z is not s:
                self.assertEqual(c.descomprimir(z), s)
        for s in (bytes(16), bytes(17), b"\xff" * 31, bytes(range(256)) * 5 + b"x", b"\x00\x80" * 40,
                  b"\xff\x7f\x00\x80" * 100):           # extremos de int16: errores de 17+ bits
            z, _ = c.comprimir(s, None)
            self.assertTrue(z is s or c.descomprimir(z) == s)

    def test_comprime_audio_y_no_azar(self):
        c = C.REGISTRO[C.PREDICTIVO_AUDIO](MODELO)
        pcm = _pcm(700)[:1360]
        self.assertLess(len(c.comprimir(pcm, None)[0]), 0.8 * len(pcm))
        azar = random.Random(1).randbytes(1360)
        self.assertIs(c.comprimir(azar, None)[0], azar)  # ni lo arma: el Compresor manda NINGUNO

    def test_por_el_marco_completo_y_con_perdidas(self):
        tx, rx = C.Compresor(MODELO), C.Descompresor(MODELO)
        pcm = _pcm(30000)
        pkts = [T.paquete_tcp(pcm[i:i + 1360], 40000, 7800, i, 1, i) for i in range(0, len(pcm) - 1360, 1360)]
        for j, p in enumerate(pkts):
            marco, usado = tx.empaquetar(p, C.PREDICTIVO_AUDIO)
            self.assertEqual(usado, C.PREDICTIVO_AUDIO)
            if j % 3:                                     # se pierde 1 de cada 3: no arrastra nada
                self.assertEqual(rx.desempaquetar(marco), p)

    def test_el_bandido_lo_aprende_con_audio(self):
        em, rx = EmisorAdaptativo(MODELO), C.Descompresor(MODELO)
        self.assertIn(C.PREDICTIVO_AUDIO, em.bandido.brazos)
        pcm, usados = _pcm(200000), {}
        for i in range(0, len(pcm) - 1360, 1360):
            p = T.paquete_tcp(pcm[i:i + 1360], 40000, 7800, i, 1, i // 1360)
            marco, cod = em.enviar(p)
            self.assertEqual(rx.desempaquetar(marco), p)
            if i > len(pcm) // 2:
                usados[cod] = usados.get(cod, 0) + 1
        self.assertGreater(usados.get(C.PREDICTIVO_AUDIO, 0), 0.8 * sum(usados.values()))


class Rafagas(unittest.TestCase):
    def test_deflate_flujo_se_sostiene_y_nada_cambia_en_el_receptor(self):
        mezcla = T.intercalar(PRUEBA, 1)
        em, rx = EmisorAdaptativo(MODELO, rafaga=8), C.Descompresor(MODELO)
        for _, _, p in mezcla:
            self.assertEqual(rx.desempaquetar(em.enviar(p)[0]), p)
        self.assertGreater(em.por_rafaga, 0)
        self.assertTrue(all(0 <= r[1] < 8 and r[0] in (C.DEFLATE_FLUJO,) for r in em.en_rafaga.values()))

    def test_sin_rafaga_siempre_consulta(self):
        em = EmisorAdaptativo(MODELO, rafaga=0)
        for _, _, p in T.intercalar(PRUEBA, 1)[:500]:
            em.enviar(p)
        self.assertEqual(em.por_rafaga, 0)
        self.assertEqual(em.en_rafaga, {})


if __name__ == "__main__":
    unittest.main()
