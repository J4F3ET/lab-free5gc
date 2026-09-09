# 06 — PLMN, TAC, S-NSSAI e identidades

Los tres números que tienen que coincidir en once ficheros y en MongoDB. Este
documento explica qué son, dónde aparecen y cómo cambiarlos sin romper nada.

> Si solo vas a leer una línea: **nunca cambies uno de estos valores en un solo
> sitio**. Ejecuta `python3 ci/check-plmn.py` después de cada cambio.

---

## 1. Qué PLMN usa este laboratorio, y por qué

El valor por defecto de free5GC es **MCC 208 / MNC 93**, que corresponde a
Francia. Este laboratorio usa otro:

```
MCC 732 / MNC 101      →  Colombia
IMSI 732101000000001
```

Hay dos opciones razonables y conviene conocer la diferencia:

| Opción | MCC | MNC | Dígitos del MNC | Dígitos del MSIN | IMSI de ejemplo |
|---|---|---|---|---|---|
| Red de pruebas | `001` | `01` | 2 | 10 | `001010000000001` |
| **Colombia (la de este repo)** | `732` | `101` | 3 | 9 | `732101000000001` |

**`001/01` está reservado por la ITU-T** (Recomendación E.212) precisamente para
redes de prueba: no está asignado a ningún operador real, y es lo que esperan la
mayoría de guías y herramientas.

Este repositorio usa `732/101` porque el proyecto representa específicamente una
red colombiana. Como el laboratorio es puramente emulado —no hay
radiofrecuencia— no causa interferencia de ningún tipo, pero tiene una
consecuencia técnica muy real:

> ⚠️ **El MNC de Colombia tiene 3 dígitos.** Eso cambia la aritmética del IMSI y
> el comportamiento de varios parsers. Casi todos los ejemplos que encontrarás
> por internet asumen un MNC de 2 dígitos, y copiarlos tal cual produce un IMSI
> de 16 dígitos que falla en silencio.

`101` está asignado a un operador real, así que documéntalo siempre como
"simulación de", nunca como una red propia.

---

## 2. La aritmética del IMSI

Un IMSI son **siempre 15 dígitos**: `MCC (3) + MNC (2 ó 3) + MSIN (rellena hasta 15)`.

```
001 01  0000000001   ->  001010000000001   (MSIN de 10 dígitos)
732 101 000000001    ->  732101000000001   (MSIN de 9 dígitos)
```

Un IMSI de 16 dígitos porque copiaste el MSIN de 10 dígitos del ejemplo por defecto y le pusiste un MNC de 3 dígitos es un fallo silencioso: el UE envía el SUCI, el UDM no encuentra el suscriptor, y recibes un `Registration Reject`. Es exactamente el tipo de error que produce el síntoma F4.

## 3. Inventario: dónde aparece el PLMN

Cambiar el MCC/MNC en un solo sitio es la causa número uno de `NGSetupFailure`. Estos son **todos** los puntos que deben cambiar en conjunto:

| Fichero | Bloque | Notas |
|---|---|---|
| `config/nrfcfg.yaml` | `configuration.DefaultPlmnId` | |
| `config/amfcfg.yaml` | `servedGuamiList[].plmnId` | |
| `config/amfcfg.yaml` | `supportTaiList[].plmnId` + `tac` | |
| `config/amfcfg.yaml` | `plmnSupportList[].plmnId` + `snssaiList` | |
| `config/nssfcfg.yaml` | `supportedPlmnList` | |
| `config/nssfcfg.yaml` | `supportedNssaiInPlmnList[].plmnId` | |
| `config/nssfcfg.yaml` | `nsiList[]`, `amfSetList[]`, `taList[]` | Fácil de olvidar |
| `config/smfcfg.yaml` | `configuration.snssaiInfos` + `plmnList` | |
| `config/udrcfg.yaml` / `udmcfg.yaml` | (normalmente no llevan PLMN) | |
| `ueransim/gnb.yaml` | `mcc`, `mnc`, `tac`, `slices` | |
| `ueransim/ue.yaml` | `supi`, `mcc`, `mnc`, `sessions`, `configured-nssai` | |
| `config/upfcfg.yaml` | El mismo `nodeID` que el SMF, el DNN y el pool de IP | No lleva PLMN, pero falla igual |
| `config/uerouting.yaml` | Se refiere al abonado por su **IMSI completo** | |
| `.env` | `MCC`, `MNC`, `IMSI` | Los que usa el script de aprovisionamiento |
| **MongoDB** (vía WebConsole) | PLMN del suscriptor | **No es un fichero.** Se pierde al recrear el volumen |

Ese último punto merece énfasis: el suscriptor vive en MongoDB, no en el repositorio. Si tu pipeline de CI recrea el volumen de Mongo, el suscriptor desaparece y el UE deja de registrarse aunque los YAML estén perfectos. Lo resuelve `ci/provision-subscriber.sh`.

## 4. Las tres trampas de formato

Estas son incompatibilidades de notación entre free5GC y UERANSIM, no errores tuyos, y no producen mensajes de error legibles:

| Parámetro | free5GC | UERANSIM | Valor equivalente |
|---|---|---|---|
| **SD** (Slice Differentiator) | `sd: '010203'` (string hex, 6 chars, entrecomillado) | `sd: 0x010203` (entero hex) | 66051 decimal |
| **TAC** | `tac: '000001'` (string hex de 3 octetos) | `tac: 1` (entero decimal) | 1 |
| **MCC/MNC** | `mcc: '001'` (string, comillas obligatorias) | `mcc: '001'` (string) | — |

Si escribes `mcc: 001` sin comillas, YAML lo interpreta como el entero `1` y pierdes los ceros a la izquierda. Ese es probablemente el error más común de todo el ecosistema free5GC. **Entrecomilla siempre `mcc`, `mnc`, `sd` y `tac` en los ficheros de free5GC.**


---

## 5. Los dos valores que no son PLMN pero fallan igual

**`nodeID` de PFCP.** Tiene que ser idéntico en `smfcfg.yaml` y `upfcfg.yaml`.
Si no lo es, el SMF reintenta la asociación indefinidamente y el teléfono se
registra pero nunca consigue datos. Es el modo de fallo **F3**.

**El endpoint N3.** El `smfcfg.yaml` le dice a la antena a qué IP mandar los
datos. Debe ser `10.100.201.20`, la IP del UPF en `n3_net`. Si apunta a otra, el
túnel se crea, `uesimtun0` aparece, y no pasa ni un paquete.

---

## 6. Cómo cambiar el PLMN sin romper nada

Cinco pasos, en este orden:

**1. Calcula el IMSI nuevo.** 15 dígitos exactos:

```
MCC + MNC + MSIN = 15
```

**2. Cambia los ficheros.** Los diez de la tabla anterior. Recuerda las
convenciones distintas: `'732'` con comillas en free5GC, `0x010203` sin comillas
en UERANSIM.

**3. Valida antes de arrancar nada:**

```bash
python3 ci/check-plmn.py
```

**4. Rehaz el abonado**, porque el que hay en MongoDB lleva el PLMN viejo:

```bash
bash ci/provision-subscriber.sh
```

**5. Reinicia lo que lee esos ficheros:**

```bash
COMPOSE_PROFILES=ran docker compose restart amf smf nssf nrf gnb ue
```

Y comprueba de extremo a extremo:

```bash
COMPOSE_PROFILES=ran bash ci/smoke-ue.sh
```

---

## 7. El validador

[`ci/check-plmn.py`](../ci/check-plmn.py) es la red de seguridad de todo esto.
Compara entre ficheros:

| Comprueba | Modo de fallo que evita |
|---|---|
| PLMN idéntico en free5GC y UERANSIM | F4 — `NGSetupFailure` |
| TAC coherente entre su forma hexadecimal y la decimal | F4 |
| S-NSSAI (`sst` + `sd`) igual en los cinco sitios | F4 — `Registration Reject` |
| `nodeID` de PFCP idéntico entre SMF y UPF | **F3** — el más frecuente |
| Endpoint N3 apuntando a la IP correcta del UPF | "el túnel sube pero no pasa tráfico" |
| Pool de IP de los teléfonos coherente entre SMF y UPF | Sesiones rechazadas |
| IMSI de 15 dígitos y con el prefijo correcto | `Registration Reject` sin causa clara |
| DNN idéntico en `smfcfg`, `upfcfg` y `ue.yaml` | Sesión de datos rechazada |

Salida esperada:

```
PLMN 732/101, S-NSSAI 1/010203, TAC 000001, DNN internet
```

Necesita `python3-yaml` como paquete del sistema. Se ejecuta también en CI, como
tercer paso del job `validate`, así que un cambio incoherente **no llega a
desplegarse**.

---

**Anterior:** [05 — Diagnóstico](05-diagnostico.md) ·
**Siguiente:** [07 — CI/CD](07-cicd.md)
