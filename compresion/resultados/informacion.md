# Original, comprimido y recuperado por algoritmo (generado por `evaluar.py informacion`)

3600 paquetes del flujo mezclado de PRUEBA. H = entropía de orden 0 en bits por byte. I(O;R) = información mutua entre el original y el recuperado, en bits por byte del original (ver `informacion.py`). Canal con pérdida: 1% de los paquetes al azar.

## 1. Flujo mezclado completo

| algoritmo | H(O) | bytes O | bytes C | razón | ahorro % | H(C) | I(O;R) perfecto | I(O;R) con pérdida | info. entregada con pérdida % | mal con pérdida % | comprimir µs | descomprimir µs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sin_compresion | 7.5694 | 2615408 | 2615408 | 1.0 | 0.0 | 7.5694 | 7.5694 | 7.5048 | 99.146 | 0.0 | 1.5 | 1.3 |
| solo_rohc | 7.5694 | 2615408 | 2552120 | 1.0248 | 2.42 | 7.5672 | 7.5694 | 7.5047 | 99.145 | 0.14 | 38.8 | 29.0 |
| huffman_estatico | 7.5694 | 2615408 | 2268565 | 1.1529 | 13.26 | 7.9919 | 7.5694 | 7.5047 | 99.145 | 0.14 | 971.2 | 1812.2 |
| deflate | 7.5694 | 2615408 | 2198296 | 1.1897 | 15.95 | 7.9955 | 7.5694 | 7.5047 | 99.145 | 0.14 | 152.8 | 45.4 |
| deflate_dict | 7.5694 | 2615408 | 2142638 | 1.2206 | 18.08 | 7.9943 | 7.5694 | 7.5047 | 99.145 | 0.14 | 192.6 | 45.0 |
| deflate_flujo | 7.5694 | 2615408 | 2027615 | 1.2899 | 22.47 | 7.9938 | 7.5694 | 7.3432 | 97.012 | 3.06 | 267.0 | 46.4 |
| tic_por_paquete | 7.5694 | 2615408 | 2375205 | 1.1011 | 9.18 | 7.9714 | 7.5694 | 7.5047 | 99.145 | 0.14 | 4326.2 | 1698.2 |
| zstd_dict | 7.5694 | 2615408 | 2082713 | 1.2558 | 20.37 | 7.9919 | 7.5694 | 7.5047 | 99.145 | 0.14 | 145.8 | 42.4 |
| bandido_ucb | 7.5694 | 2615408 | 2065809 | 1.266 | 21.01 | 7.9928 | 7.5694 | 7.5007 | 99.093 | 1.8 | 165.9 | 50.9 |

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
| zstd_dict | 99.059 | 98.497 | 98.767 | 99.334 | 98.69 | 99.503 |
| bandido_ucb | 99.059 | 98.497 | 98.767 | 99.334 | 96.714 | 99.503 |

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
| zstd_dict | 45.45 (H 7.8285) | 51.33 (H 7.9761) | 43.0 (H 5.1223) | 2.52 (H 7.9995) | 54.78 (H 6.6963) | -0.07 (H 7.994) |
| bandido_ucb | 45.45 (H 7.8285) | 51.23 (H 7.9765) | 43.0 (H 5.1223) | 2.52 (H 7.9995) | 79.0 (H 6.9403) | -0.07 (H 7.994) |

## 4. Con pérdida: tasa-distorsión de la telemetría IoT (`predictivo_iot`)

Solo los paquetes iot_json. «×e» = pasos de cuantización × e (×1 = temp 0.05, hum 0.5, bat 0.005); ×0 = pasos de 1e-6, en la práctica sin pérdida. «Dentro de tolerancia» = error ≤ paso/2 en temp, hum y bat. «I/H» = información mutua entre la lectura original y la recibida, sobre los valores.

| método | canal | bytes/paquete | ahorro % | error máx temp / hum / bat | dentro de tolerancia % | dañados % | I/H medio % |
|---|---|---:|---:|---|---:|---:|---:|
| predictivo ×0 | perfecto | 20.63 | 83.07 | 0 / 0 / 0 | 100.0 | 0.0 | 100.0 |
| predictivo ×0 | perdida_1pct | 20.63 | 83.07 | 0.11 / 0.5 / 0.001 | 94.09 | 0.68 | 97.48 |
| predictivo ×0.1 | perfecto | 17.42 | 85.7 | 0 / 0 / 0 | 100.0 | 0.0 | 100.0 |
| predictivo ×0.1 | perdida_1pct | 17.42 | 85.7 | 0.11 / 0.5 / 0.001 | 94.09 | 0.68 | 97.48 |
| predictivo ×0.5 | perfecto | 17.42 | 85.7 | 0.01 / 0.1 / 0.001 | 100.0 | 0.0 | 82.77 |
| predictivo ×0.5 | perdida_1pct | 17.42 | 85.7 | 0.135 / 0.6 / 0.001 | 94.09 | 0.68 | 81.83 |
| predictivo ×1 | perfecto | 17.42 | 85.7 | 0.02 / 0.2 / 0.001 | 100.0 | 0.0 | 75.86 |
| predictivo ×1 | perdida_1pct | 17.42 | 85.7 | 0.12 / 0.3 / 0.001 | 96.62 | 0.68 | 75.31 |
| predictivo ×2 | perfecto | 17.42 | 85.7 | 0.05 / 0.5 / 0.001 | 100.0 | 0.0 | 69.57 |
| predictivo ×2 | perdida_1pct | 17.42 | 85.7 | 0.15 / 0.5 / 0.001 | 96.62 | 0.68 | 69.19 |
| predictivo ×5 | perfecto | 17.42 | 85.7 | 0.12 / 1.2 / 0.001 | 100.0 | 0.0 | 64.22 |
| predictivo ×5 | perdida_1pct | 17.42 | 85.7 | 0.31 / 1.2 / 0.001 | 97.8 | 0.68 | 64.08 |
| predictivo ×10 | perfecto | 17.42 | 85.7 | 0.25 / 1.9 / 0.001 | 100.0 | 0.0 | 61.97 |
| predictivo ×10 | perdida_1pct | 17.42 | 85.7 | 0.75 / 1.9 / 0.001 | 98.14 | 0.68 | 61.91 |
| zstd_dict | perfecto | 55.1 | 54.78 | 0 / 0 / 0 | 100.0 | 0.0 | 100.0 |
| zstd_dict | perdida_1pct | 55.1 | 54.78 | 0 / 0 / 0 | 100.0 | 0.0 | 99.37 |
| deflate_flujo | perfecto | 25.43 | 79.13 | 0 / 0 / 0 | 100.0 | 0.0 | 100.0 |
| deflate_flujo | perdida_1pct | 25.43 | 79.13 | 0.03 / 0.1 / 0 | 90.71 | 8.28 | 93.12 |
