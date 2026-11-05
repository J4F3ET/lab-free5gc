# Original, comprimido y recuperado por algoritmo (generado por `evaluar.py informacion`)

3600 paquetes del flujo mezclado de PRUEBA. H = entropía de orden 0 en bits por byte. I(O;R) = información mutua entre el original y el recuperado, en bits por byte del original (ver `informacion.py`). Canal con pérdida: 1% de los paquetes al azar.

## 1. Flujo mezclado completo

| algoritmo | H(O) | bytes O | bytes C | razón | ahorro % | H(C) | I(O;R) perfecto | I(O;R) con pérdida | info. entregada con pérdida % | mal con pérdida % | comprimir µs | descomprimir µs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sin_compresion | 7.5694 | 2615408 | 2615408 | 1.0 | 0.0 | 7.5694 | 7.5694 | 7.5048 | 99.146 | 0.0 | 0.3 | 0.4 |
| solo_rohc | 7.5694 | 2615408 | 2552120 | 1.0248 | 2.42 | 7.5672 | 7.5694 | 7.5047 | 99.145 | 0.14 | 15.8 | 13.0 |
| huffman_estatico | 7.5694 | 2615408 | 2268565 | 1.1529 | 13.26 | 7.9919 | 7.5694 | 7.5047 | 99.145 | 0.14 | 565.0 | 1043.2 |
| deflate | 7.5694 | 2615408 | 2198296 | 1.1897 | 15.95 | 7.9955 | 7.5694 | 7.5047 | 99.145 | 0.14 | 72.7 | 21.3 |
| deflate_dict | 7.5694 | 2615408 | 2142638 | 1.2206 | 18.08 | 7.9943 | 7.5694 | 7.5047 | 99.145 | 0.14 | 100.8 | 21.5 |
| deflate_flujo | 7.5694 | 2615408 | 2027615 | 1.2899 | 22.47 | 7.9938 | 7.5694 | 7.3432 | 97.012 | 3.06 | 138.6 | 22.2 |
| tic_por_paquete | 7.5694 | 2615408 | 2375205 | 1.1011 | 9.18 | 7.9714 | 7.5694 | 7.5047 | 99.145 | 0.14 | 2342.6 | 944.7 |
| bandido_ucb | 7.5694 | 2615408 | 2029179 | 1.2889 | 22.41 | 7.9938 | 7.5694 | 7.3019 | 96.466 | 3.54 | 107.9 | 24.0 |

## 2. Información entregada con 1 % de pérdida, por clase (% de H(O) que llega)

Con canal perfecto todos dan 100 %. Lo que baja de 99 % (lo que se pierde en el canal) es daño que agrega el algoritmo.

| algoritmo | voip_rtp | http_texto | urllc_ctrl | video_rtp | iot_json | web_tls |
|---|---|---|---|---|---|---|
| sin_compresion | 99.059 | 98.497 | 98.767 | 99.336 | 98.69 | 99.503 |
| solo_rohc | 99.059 | 98.497 | 98.767 | 99.334 | 98.69 | 99.503 |
| huffman_estatico | 99.059 | 98.497 | 98.767 | 99.334 | 98.69 | 99.503 |
| deflate | 99.059 | 98.497 | 98.767 | 99.334 | 98.69 | 99.503 |
| deflate_dict | 99.059 | 98.497 | 98.767 | 99.334 | 98.69 | 99.503 |
| deflate_flujo | 99.059 | 89.826 | 98.767 | 99.334 | 96.745 | 99.503 |
| tic_por_paquete | 99.059 | 98.497 | 98.767 | 99.334 | 98.69 | 99.503 |
| bandido_ucb | 99.059 | 87.513 | 98.767 | 99.334 | 96.684 | 99.503 |

## 3. Ahorro por clase (%) y H(C)

| algoritmo | voip_rtp | http_texto | urllc_ctrl | video_rtp | iot_json | web_tls |
|---|---|---|---|---|---|---|
| sin_compresion | 0.0 (H 7.2846) | 0.0 (H 5.3305) | 0.0 (H 5.1824) | 0.0 (H 7.9955) | 0.0 (H 5.3312) | 0.0 (H 7.9947) |
| solo_rohc | 45.45 (H 7.8285) | -0.07 (H 5.3324) | 43.0 (H 5.1223) | 2.52 (H 7.9995) | 17.64 (H 4.8922) | -0.07 (H 7.994) |
| huffman_estatico | 45.45 (H 7.8285) | 31.86 (H 7.9308) | 43.0 (H 5.1223) | 2.52 (H 7.9995) | 29.72 (H 7.0978) | -0.07 (H 7.994) |
| deflate | 45.45 (H 7.8285) | 40.31 (H 7.9959) | 43.03 (H 5.1771) | 2.52 (H 7.9995) | 26.33 (H 7.7886) | -0.07 (H 7.994) |
| deflate_dict | 45.45 (H 7.8285) | 43.85 (H 7.9954) | 43.03 (H 5.1776) | 2.52 (H 7.9995) | 60.82 (H 6.6314) | -0.07 (H 7.994) |
| deflate_flujo | 45.45 (H 7.8285) | 55.66 (H 7.9959) | 43.0 (H 5.1223) | 2.52 (H 7.9995) | 79.13 (H 6.9478) | -0.07 (H 7.994) |
| tic_por_paquete | 45.45 (H 7.8285) | 20.49 (H 7.8523) | 43.0 (H 5.1223) | 2.52 (H 7.9995) | 17.64 (H 4.8922) | -0.07 (H 7.994) |
| bandido_ucb | 45.45 (H 7.8285) | 55.49 (H 7.9963) | 43.01 (H 5.1349) | 2.52 (H 7.9995) | 78.94 (H 6.9403) | -0.07 (H 7.994) |
