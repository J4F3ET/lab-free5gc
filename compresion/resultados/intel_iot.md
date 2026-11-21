# Telemetría IoT REAL (Intel Berkeley Research Lab): los 9 algoritmos (generado por `reproducir_iot.py comparar`)

6000 paquetes reales de 3 sensores (una lectura = un paquete). H = entropía de orden 0 en bits por byte. I(O;R) = información mutua entre el original y el recuperado, en bits por byte del original (ver `informacion.py`). Canal con pérdida: 1% de los paquetes al azar.

## 1. Flujo mezclado completo

| algoritmo | H(O) | bytes O | bytes C | razón | ahorro % | H(C) | I(O;R) perfecto | I(O;R) con pérdida | info. entregada con pérdida % | mal con pérdida % | comprimir µs | descomprimir µs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sin_compresion | 5.4019 | 766706 | 766706 | 1.0 | 0.0 | 5.4019 | 5.4019 | 5.3463 | 98.971 | 0.0 | 0.4 | 0.5 |
| solo_rohc | 5.4019 | 766706 | 637031 | 1.2036 | 16.91 | 4.9442 | 5.4019 | 5.3463 | 98.971 | 0.0 | 18.9 | 18.9 |
| huffman_estatico | 5.4019 | 766706 | 565909 | 1.3548 | 26.19 | 7.07 | 5.4019 | 5.3463 | 98.971 | 0.0 | 106.8 | 717.2 |
| deflate | 5.4019 | 766706 | 565698 | 1.3553 | 26.22 | 7.8501 | 5.4019 | 5.3463 | 98.971 | 0.0 | 76.8 | 44.9 |
| deflate_dict | 5.4019 | 766706 | 462580 | 1.6575 | 39.67 | 7.0515 | 5.4019 | 5.3463 | 98.971 | 0.0 | 97.4 | 33.6 |
| deflate_flujo | 5.4019 | 766706 | 173774 | 4.4121 | 77.33 | 7.0753 | 5.4019 | 5.1155 | 94.699 | 7.28 | 114.1 | 33.5 |
| tic_por_paquete | 5.4019 | 766706 | 637031 | 1.2036 | 16.91 | 4.9442 | 5.4019 | 5.3463 | 98.971 | 0.0 | 571.2 | 33.8 |
| zstd_dict | 5.4019 | 766706 | 488661 | 1.569 | 36.26 | 7.5131 | 5.4019 | 5.3463 | 98.971 | 0.0 | 67.6 | 43.5 |
| bandido_ucb | 5.4019 | 766706 | 174332 | 4.398 | 77.26 | 7.0804 | 5.4019 | 5.1044 | 94.493 | 8.24 | 135.1 | 34.1 |

## 2. Información entregada con 1 % de pérdida, por clase (% de H(O) que llega)

Con canal perfecto todos dan 100 %. Lo que baja de 99 % (lo que se pierde en el canal) es daño que agrega el algoritmo.

| algoritmo | iot_json |
|---|---|
| sin_compresion | 98.971 |
| solo_rohc | 98.971 |
| huffman_estatico | 98.971 |
| deflate | 98.971 |
| deflate_dict | 98.971 |
| deflate_flujo | 94.699 |
| tic_por_paquete | 98.971 |
| zstd_dict | 98.971 |
| bandido_ucb | 94.493 |

## 3. Ahorro por clase (%) y H(C)

| algoritmo | iot_json |
|---|---|
| sin_compresion | 0.0 (H 5.4019) |
| solo_rohc | 16.91 (H 4.9442) |
| huffman_estatico | 26.19 (H 7.07) |
| deflate | 26.22 (H 7.8501) |
| deflate_dict | 39.67 (H 7.0515) |
| deflate_flujo | 77.33 (H 7.0753) |
| tic_por_paquete | 16.91 (H 4.9442) |
| zstd_dict | 36.26 (H 7.5131) |
| bandido_ucb | 77.26 (H 7.0804) |

## 4. Con pérdida: tasa-distorsión de la telemetría IoT (`predictivo_iot`)

Solo los paquetes iot_json. «×e» = pasos de cuantización × e (×1 = temp 0.05, hum 0.5, bat 0.005); ×0 = pasos de 1e-6, en la práctica sin pérdida. «Dentro de tolerancia» = error ≤ paso/2 en temp, hum y bat. «I/H» = información mutua entre la lectura original y la recibida, sobre los valores.

| método | canal | bytes/paquete | ahorro % | error máx temp / hum / bat | dentro de tolerancia % | dañados % | I/H medio % |
|---|---|---:|---:|---|---:|---:|---:|
| predictivo ×0 | perfecto | 23.44 | 81.66 | 0 / 0 / 0 | 100.0 | 0.0 | 100.0 |
| predictivo ×0 | perdida_1pct | 23.44 | 81.66 | 0.0686 / 0.3437 / 0.01267 | 92.52 | 0.0 | 97.37 |
| predictivo ×0.1 | perfecto | 20.35 | 84.07 | 0.0024 / 0.025 / 0.00024 | 100.0 | 0.0 | 98.56 |
| predictivo ×0.1 | perdida_1pct | 20.35 | 84.07 | 0.067 / 0.3688 / 0.01267 | 92.64 | 0.0 | 96.55 |
| predictivo ×0.5 | perfecto | 20.35 | 84.07 | 0.0124 / 0.1249 / 0.00111 | 100.0 | 0.0 | 89.76 |
| predictivo ×0.5 | perdida_1pct | 20.35 | 84.07 | 0.0832 / 0.6124 / 0.01311 | 95.84 | 0.0 | 87.98 |
| predictivo ×1 | perfecto | 20.35 | 84.07 | 0.0248 / 0.2497 / 0.00244 | 100.0 | 0.0 | 85.39 |
| predictivo ×1 | perdida_1pct | 20.35 | 84.07 | 0.0744 / 0.7432 / 0.01744 | 95.82 | 0.0 | 83.8 |
| predictivo ×2 | perfecto | 20.35 | 84.07 | 0.0494 / 0.4968 / 0.005 | 100.0 | 0.0 | 81.8 |
| predictivo ×2 | perdida_1pct | 20.35 | 84.07 | 0.149 / 0.4968 / 0.01389 | 96.36 | 0.0 | 80.2 |
| predictivo ×5 | perfecto | 20.35 | 84.07 | 0.1226 / 1.242 / 0.01244 | 100.0 | 0.0 | 70.75 |
| predictivo ×5 | perdida_1pct | 20.35 | 84.07 | 0.3332 / 1.242 / 0.03744 | 99.19 | 0.0 | 69.94 |
| predictivo ×10 | perfecto | 20.35 | 84.07 | 0.2452 / 2.4205 / 0.025 | 100.0 | 0.0 | 65.53 |
| predictivo ×10 | perdida_1pct | 20.35 | 84.07 | 0.343 / 2.4205 / 0.025 | 99.88 | 0.0 | 65.08 |
| zstd_dict | perfecto | 81.44 | 36.26 | 0 / 0 / 0 | 100.0 | 0.0 | 100.0 |
| zstd_dict | perdida_1pct | 81.44 | 36.26 | 0 / 0 / 0 | 100.0 | 0.0 | 99.22 |
| deflate_flujo | perfecto | 28.96 | 77.33 | 0 / 0 / 0 | 100.0 | 0.0 | 100.0 |
| deflate_flujo | perdida_1pct | 28.96 | 77.33 | 226035 / 357803 / 3961.35 | 92.84 | 6.11 | 93.32 |
