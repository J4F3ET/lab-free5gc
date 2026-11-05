# Resultados (generado por `evaluar.py comparar`)

## 1. Ahorro por clase de tráfico (% de bytes)

| clase | slice | B/paq | solo ROHC | ROHC+Huffman estático | ROHC+DEFLATE | ROHC+DEFLATE dicc. | ROHC+DEFLATE flujo | ROHC+TIC por paquete | TIC por paquete (sin ROHC) | DEFLATE flujo (sin ROHC) |
|---|---|---|---|---|---|---|---|---|---|---|
| iot_json | mMTC | 121.9 | 17.64 | 29.72 | 26.33 | 60.82 | 81.81 | 17.64 | -0.82 | 78.01 |
| urllc_ctrl | URLLC | 50.0 | 43.0 | 43.0 | 43.03 | 43.03 | 43.0 | 43.0 | -2.0 | 46.21 |
| voip_rtp | eMBB | 73.0 | 45.45 | 45.45 | 45.45 | 45.45 | 45.45 | 45.45 | -1.37 | 17.14 |
| video_rtp | eMBB | 1240.0 | 2.52 | 2.52 | 2.52 | 2.52 | 2.52 | 2.52 | -0.08 | -0.08 |
| web_tls | eMBB | 1440.0 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 | -0.07 |
| http_texto | eMBB | 1434.2 | -0.07 | 31.86 | 40.31 | 43.85 | 60.56 | 20.49 | 20.49 | 60.56 |

## 2. Flujo mezclado (3600 paquetes, 2,615,408 B)

| política | bytes enviados | razón | ahorro % | bytes que pasan por un compresor / byte | CPU emisor µs/paq (ruidoso) |
|---|---|---|---|---|---|
| sin compresión (crudo) | 2615408 | 1.0 | 0.0 | 0.0 | 0.2 |
| ROHC + ninguno | 2552120 | 1.0248 | 2.42 | 0.0 | 9.7 |
| ROHC + huffman_estatico | 2268565 | 1.1529 | 13.26 | 1.0 | 474.8 |
| ROHC + deflate | 2198296 | 1.1897 | 15.95 | 1.0 | 54.8 |
| ROHC + deflate_dict | 2142638 | 1.2206 | 18.08 | 1.0 | 81.6 |
| ROHC + deflate_flujo | 1983491 | 1.3186 | 24.16 | 1.0 | 114.9 |
| ROHC + tic_por_paquete | 2375205 | 1.1011 | 9.18 | 1.0 | 2080.8 |
| ROHC + deflate_flujo, reinicio cada 16 | 2027615 | 1.2899 | 22.47 | 1.0 | 114.6 |
| ROHC + bandido UCB, 6 brazos, γ=0,99 (como el Lab 3) | 2037326 | 1.2837 | 22.1 | 0.803 | 376.4 |
| ROHC + bandido UCB, configuración de red, sin reinicio | 1985347 | 1.3174 | 24.09 | 0.513 | 82.5 |
| ROHC + bandido UCB, configuración de red | 2029179 | 1.2889 | 22.41 | 0.513 | 72.5 |

Qué ELIGIÓ el bandido (configuración de red) en cada clase, antes del respaldo. En las clases cifradas o ya comprimidas, todo lo que no es "ninguno" es exploración:

| clase | ninguno | huffman_estatico | deflate | deflate_dict | deflate_flujo | tic_por_paquete |
|---|---|---|---|---|---|---|
| voip_rtp | 446 | 0 | 0 | 77 | 77 | 0 |
| http_texto | 1 | 0 | 0 | 3 | 596 | 0 |
| urllc_ctrl | 438 | 0 | 0 | 86 | 76 | 0 |
| video_rtp | 437 | 0 | 0 | 82 | 81 | 0 |
| iot_json | 1 | 0 | 0 | 3 | 596 | 0 |
| web_tls | 469 | 0 | 0 | 65 | 66 | 0 |

## 3. Latencia por paquete en µs (mediana, Python puro; comprimir / descomprimir)

| códec | iot_json comp/desc | urllc_ctrl comp/desc | voip_rtp comp/desc | video_rtp comp/desc | web_tls comp/desc | http_texto comp/desc |
|---|---|---|---|---|---|---|
| ninguno | 12.0 / 13.3 | 9.7 / 11.0 | 10.8 / 12.6 | 11.8 / 12.8 | 3.4 / 1.3 | 3.4 / 1.3 |
| huffman_estatico | 31.9 / 252.5 | 14.4 / 8.9 | 19.9 / 12.2 | 1131.6 / 24.9 | 1326.6 / 4.0 | 699.2 / 4731.1 |
| deflate | 27.9 / 17.8 | 21.7 / 10.6 | 26.8 / 14.0 | 80.8 / 15.6 | 81.1 / 1.5 | 66.9 / 16.7 |
| deflate_dict | 43.8 / 13.9 | 40.1 / 9.8 | 45.1 / 13.2 | 126.9 / 25.5 | 85.4 / 1.1 | 148.2 / 26.7 |
| deflate_flujo | 31.8 / 12.1 | 56.3 / 10.1 | 61.8 / 13.6 | 121.7 / 16.0 | 104.9 / 1.1 | 145.3 / 15.9 |
| tic_por_paquete | 354.8 / 17.4 | 112.8 / 13.9 | 147.0 / 14.7 | 3643.5 / 36.0 | 4289.3 / 4.0 | 2853.9 / 4950.5 |

## 4. Pérdida de 1 % de los paquetes (38 perdidos)

| caso | recibidos | mal reconstruidos | % mal | ahorro % |
|---|---|---|---|---|
| ROHC + deflate_dict (sin estado) | 3562 | 5 | 0.14 | 18.08 |
| ROHC + deflate_flujo (con estado, sin reinicio) | 3562 | 942 | 26.45 | 24.16 |
| ROHC + deflate_flujo (reinicio cada 16 paq.) | 3562 | 109 | 3.06 | 22.47 |
| ROHC + ninguno | 3562 | 5 | 0.14 | 2.42 |
| ROHC + bandido, config. de red sin reinicio | 3562 | 942 | 26.45 | 24.09 |
| ROHC + bandido, config. de red (reinicio cada 16 paq.) | 3562 | 126 | 3.54 | 22.41 |
