# Resultados (generado por `evaluar.py comparar`)

## 1. Ahorro por clase de tráfico (% de bytes)

| clase | slice | B/paq | solo ROHC | ROHC+huffman_estatico | ROHC+deflate | ROHC+deflate_dict | ROHC+deflate_flujo | ROHC+tic_por_paquete | ROHC+zstd_dict | tic_por_paquete (sin ROHC) | deflate_flujo (sin ROHC) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| iot_json | mMTC | 121.9 | 17.64 | 29.72 | 26.33 | 60.82 | 81.81 | 17.64 | 54.78 | -0.82 | 78.01 |
| urllc_ctrl | URLLC | 50.0 | 43.0 | 43.0 | 43.03 | 43.03 | 43.0 | 43.0 | 43.0 | -2.0 | 46.21 |
| voip_rtp | eMBB | 73.0 | 45.45 | 45.45 | 45.45 | 45.45 | 45.45 | 45.45 | 45.45 | -1.37 | 17.14 |
| video_rtp | eMBB | 1240.0 | 2.52 | 2.52 | 2.52 | 2.52 | 2.52 | 2.52 | 2.52 | -0.08 | -0.08 |
| web_tls | eMBB | 1440.0 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 |
| http_texto | eMBB | 1434.2 | -0.07 | 31.86 | 40.31 | 43.85 | 60.56 | 20.49 | 51.33 | 20.49 | 60.56 |

## 2. Flujo mezclado (3600 paquetes, 2,615,408 B)

| política | bytes enviados | razón | ahorro % | bytes que pasan por un compresor / byte | CPU emisor µs/paq (ruidoso) |
|---|---|---|---|---|---|
| sin compresión (crudo) | 2615408 | 1.0 | 0.0 | 0.0 | 0.2 |
| ROHC + ninguno | 2552120 | 1.0248 | 2.42 | 0.0 | 11.1 |
| ROHC + huffman_estatico | 2268565 | 1.1529 | 13.26 | 1.0 | 586.8 |
| ROHC + deflate | 2198296 | 1.1897 | 15.95 | 1.0 | 69.7 |
| ROHC + deflate_dict | 2142638 | 1.2206 | 18.08 | 1.0 | 90.6 |
| ROHC + deflate_flujo | 1983491 | 1.3186 | 24.16 | 1.0 | 147.4 |
| ROHC + tic_por_paquete | 2375205 | 1.1011 | 9.18 | 1.0 | 2482.8 |
| ROHC + zstd_dict | 2082713 | 1.2558 | 20.37 | 1.0 | 51.0 |
| ROHC + deflate_flujo, reinicio cada 16 | 2027615 | 1.2899 | 22.47 | 1.0 | 132.7 |
| ROHC + bandido UCB, 7 brazos, γ=0,99 (como el Lab 3) | 2056548 | 1.2717 | 21.37 | 0.667 | 281.4 |
| ROHC + bandido UCB, configuración de red, sin reinicio | 2063859 | 1.2672 | 21.09 | 0.405 | 66.2 |
| ROHC + bandido UCB, configuración de red | 2065809 | 1.266 | 21.01 | 0.405 | 66.3 |

Qué ELIGIÓ el bandido (configuración de red) en cada clase, antes del respaldo. En las clases cifradas o ya comprimidas, todo lo que no es "ninguno" es exploración:

| clase | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete | zstd_dict |
|---|---|---|---|---|---|---|---|
| voip_rtp | 552 | 0 | 0 | 0 | 24 | 0 | 24 |
| http_texto | 1 | 0 | 0 | 0 | 1 | 0 | 598 |
| urllc_ctrl | 552 | 0 | 0 | 0 | 24 | 0 | 24 |
| video_rtp | 550 | 0 | 0 | 0 | 27 | 0 | 23 |
| iot_json | 1 | 0 | 0 | 0 | 598 | 0 | 1 |
| web_tls | 558 | 0 | 0 | 0 | 19 | 0 | 23 |

## 3. Latencia por paquete en µs (mediana, Python puro; comprimir / descomprimir)

| códec | iot_json comp/desc | urllc_ctrl comp/desc | voip_rtp comp/desc | video_rtp comp/desc | web_tls comp/desc | http_texto comp/desc |
|---|---|---|---|---|---|---|
| ninguno | 10.9 / 12.6 | 10.9 / 12.5 | 14.5 / 17.0 | 16.3 / 18.5 | 2.5 / 1.0 | 2.4 / 0.9 |
| huffman_estatico | 44.0 / 327.4 | 16.1 / 10.1 | 22.0 / 13.8 | 1147.0 / 36.2 | 1338.8 / 2.6 | 870.3 / 5617.8 |
| deflate | 35.4 / 23.1 | 26.4 / 13.5 | 27.6 / 14.9 | 82.2 / 16.5 | 72.5 / 1.2 | 77.3 / 19.2 |
| deflate_dict | 49.7 / 15.8 | 45.7 / 11.1 | 52.5 / 15.0 | 135.5 / 21.0 | 129.0 / 1.4 | 115.0 / 21.1 |
| deflate_flujo | 47.0 / 17.6 | 84.7 / 14.8 | 93.7 / 20.0 | 161.1 / 21.6 | 152.4 / 1.5 | 209.6 / 21.8 |
| tic_por_paquete | 326.4 / 15.8 | 100.5 / 12.0 | 226.3 / 21.9 | 4478.3 / 41.9 | 5387.2 / 4.8 | 3704.4 / 5916.1 |
| zstd_dict | 21.6 / 17.7 | 21.6 / 12.9 | 20.7 / 17.5 | 50.8 / 19.6 | 40.4 / 1.3 | 104.6 / 12.5 |

## 4. Pérdida de 1 % de los paquetes (38 perdidos)

| caso | recibidos | mal reconstruidos | % mal | ahorro % |
|---|---|---|---|---|
| ROHC + deflate_dict (sin estado) | 3562 | 5 | 0.14 | 18.08 |
| ROHC + zstd_dict (sin estado) | 3562 | 5 | 0.14 | 20.37 |
| ROHC + deflate_flujo (con estado, sin reinicio) | 3562 | 942 | 26.45 | 24.16 |
| ROHC + deflate_flujo (reinicio cada 16 paq.) | 3562 | 109 | 3.06 | 22.47 |
| ROHC + ninguno | 3562 | 5 | 0.14 | 2.42 |
| ROHC + bandido, config. de red sin reinicio | 3562 | 413 | 11.59 | 21.09 |
| ROHC + bandido, config. de red (reinicio cada 16 paq.) | 3562 | 64 | 1.8 | 21.01 |
