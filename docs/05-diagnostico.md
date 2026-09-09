# 05 — Diagnóstico

Qué hacer cuando algo no funciona. Está ordenado de lo general a lo concreto:
primero decidir **en qué mundo** está el problema, luego el árbol de decisión, y
luego el comando exacto para cada síntoma.

---

## Antes de nada: las dos preguntas

Contéstalas en este orden y habrás descartado el 80% de las posibilidades:

**Pregunta 1 — ¿Existe el túnel?**

```bash
COMPOSE_PROFILES=ran docker compose exec ue ip addr show uesimtun0
```

- **No existe** → el problema está en el **plano de control**. Nada de mirar el
  UPF ni capturar GTP-U: el teléfono ni siquiera ha llegado a pedir datos.
- **Existe** → el plano de control funciona entero. El problema está en el
  **plano de usuario**.

**Pregunta 2 — Si existe, ¿pasa tráfico?**

```bash
COMPOSE_PROFILES=ran docker compose exec ue ping -c3 -I uesimtun0 10.100.204.10
```

- **No responde** → el túnel está montado pero los paquetes se pierden por el
  camino. Sospecha del endpoint N3 o del NAT del UPF.
- **Responde, pero las transferencias grandes se cuelgan** → es el MTU. Salta
  directamente a **[S11]**.

---

## Los siete modos de fallo

Prácticamente todos los problemas de un laboratorio free5GC caen en una de estas
siete categorías. Reconocer cuál es acorta muchísimo la búsqueda:

| # | Modo de fallo | Síntoma típico | Capa |
|---|---|---|---|
| F1 | `gtp5g` no cargado o de versión incompatible | El UPF sale con `failed to create gtp5g device` o `netlink: family not found` | 0 |
| F2 | LXC no privilegiado o sin `/dev/net/tun` | UPF y UERANSIM fallan con `operation not permitted` al crear interfaces | 1 |
| F3 | El `nodeID` de PFCP no coincide entre `smfcfg` y `upfcfg` | El SMF reintenta la asociación indefinidamente; el teléfono se registra pero no obtiene sesión de datos | 4 |
| F4 | PLMN / TAC / S-NSSAI incoherentes entre free5GC, UERANSIM y MongoDB | `NGSetupFailure`, o `Registration Reject`, o `PDU Session Establishment Reject` | 4-6 |
| F5 | `DOCKER-USER` bloquea el tráfico que viene de la LAN | Funciona dentro del contenedor, no desde tu portátil | 3 |
| F6 | Falta `ip_forward` o el `MASQUERADE` del pool de teléfonos | `uesimtun0` obtiene IP pero no hay salida | 3-6 |
| F7 | MTU: los 36 bytes de GTP-U no caben | El `ping` va, el TCP grande se cuelga | 3 |

El `ci/preflight.sh` detecta F1, F2, F5 y F6 antes de desplegar. F3 y F4 los
detecta `ci/check-plmn.py`. F7 no lo detecta nadie: hay que conocerlo.

---


## 1. La secuencia que debe ocurrir

Depurar sin este mapa mental es adivinar. Ocho etapas, en este orden estricto. Cada una **falla de forma distinta** y tiene un punto de observación distinto.

| # | Etapa | Protocolo / puerto | Dónde mirar | Señal de éxito |
|---|---|---|---|---|
| 1 | Registro de NFs en el NRF | HTTP/2, TCP 8000 | logs NRF | `NF register success` por cada NF |
| 2 | **PFCP Association** SMF↔UPF | PFCP, UDP 8805 (N4) | logs SMF | `Association Setup Response` |
| 3 | SCTP INIT gNB→AMF | SCTP 38412 (N2) | `tcpdump` en gnb | 4-way handshake SCTP completo |
| 4 | **NG Setup** | NGAP sobre SCTP | logs gNB | `NG Setup procedure is successful` |
| 5 | Enlace radio emulado UE↔gNB | UDP 4997 (UERANSIM) | logs UE | `Connection setup for PLMN ...` |
| 6 | Registro NAS + 5G-AKA | NAS sobre NGAP | logs UE + AMF | `Registration is successful` |
| 7 | **PDU Session Establishment** | NAS-SM + PFCP + NGAP | logs SMF | `PDU Session Establishment Accept` |
| 8 | Túnel de datos | GTP-U, UDP 2152 (N3) | `ip addr` en ue | `uesimtun0` con IP de 10.60.0.0/16 |

Las tres etapas en negrita son donde se rompe el 90% de los laboratorios.

## 2. Árbol de decisión

```
¿Existe uesimtun0?
│
├─ NO ──> ¿El UE se registró? (log ue: "Registration is successful")
│         │
│         ├─ NO ──> ¿Hubo NG Setup? (log gnb: "NG Setup ... successful")
│         │         │
│         │         ├─ NO ──> ¿Llega SCTP al AMF?
│         │         │         tcpdump -i any sctp port 38412
│         │         │         ├─ Sin paquetes ──> [S1] conectividad N2
│         │         │         ├─ INIT sin ACK ──> [S2] módulo sctp / AMF no escucha
│         │         │         └─ NGSetupFailure ─> [S3] PLMN / TAC
│         │         │
│         │         └─ SÍ ──> ¿El UE ve al gNB? (UDP 4997)
│         │                   ├─ NO ──> [S4] gnbSearchList / radio_net
│         │                   └─ SÍ ──> [S5] fallo de autenticación / suscriptor
│         │
│         └─ SÍ ──> [S6] PDU Session Establishment Reject
│                   └─ ¿Asociación PFCP establecida?
│                      ├─ NO ──> [S7] nodeID PFCP / UPF caído
│                      └─ SÍ ──> [S8] DNN / S-NSSAI / pool de IPs
│
└─ SÍ ──> ¿ping -I uesimtun0 al DN funciona?
          ├─ NO ──> ¿Se ven paquetes GTP-U en N3?
          │         tcpdump -i any udp port 2152
          │         ├─ NO ──> [S9] endpoint N3 mal configurado
          │         └─ SÍ ──> [S10] NAT / forwarding en el UPF
          │
          └─ SÍ ──> ¿iperf3 grande funciona?
                    ├─ NO ──> [S11] MTU
                    └─ SÍ ──> laboratorio operativo
```

## 3. Diagnóstico por síntoma

---

**[S1] No llega ningún paquete SCTP al AMF**

```bash
# ¿Están en la misma red?
docker compose exec gnb ip -br addr
docker compose exec amf ip -br addr
docker compose exec gnb ping -c2 10.100.200.11

# ¿El gNB está usando la IP correcta para N2?
docker compose exec gnb grep ngapIp /ueransim/config/gnb.yaml
```

Causa habitual: `ngapIp` en `gnb.yaml` apunta a la IP de `n3_net` en lugar de la de `sbi_net`. Recuerda: el gNB tiene **tres** IPs y cada una tiene un propósito. Consulta la tabla de direccionamiento en [03 — Referencia de servicios](03-servicios.md#2-direccionamiento-completo).

---

**[S2] SCTP INIT enviado, sin respuesta**

```bash
docker compose exec amf ss -lntu | grep 38412
docker compose logs amf | grep -i 'ngap\|sctp\|listen'
grep '^sctp ' /proc/modules       # en el LXC
```

Dos causas:
1. **Módulo `sctp` no cargado en el host Proxmox.** El AMF arranca sin quejarse, pero el `bind()` de SCTP falla silenciosamente o el paquete se descarta a nivel de kernel. `modprobe sctp` en el host.
2. **`ngapIpList` con `0.0.0.0`** en algunas versiones no bindea correctamente en un contenedor multihomed. Pon la IP explícita: `10.100.200.11`.

---

**[S3] `NGSetupFailure`**

```bash
docker compose logs amf | grep -i -A5 'ng setup'
docker compose logs gnb | grep -i -A5 'ng setup'
```

El AMF rechaza el NG Setup cuando el gNB anuncia un PLMN o TAC que no está en `supportTaiList`. Comprobación cruzada:

```bash
grep -A3 supportTaiList config/amfcfg.yaml     # mcc/mnc + tac hex
grep -E '^(mcc|mnc|tac):' ueransim/gnb.yaml    # mcc/mnc + tac decimal
```

Recuerda la conversión: `tac: '000001'` (free5GC, hex) ≡ `tac: 1` (UERANSIM, decimal). Si en `amfcfg` pusiste `tac: '000007'` y en `gnb.yaml` `tac: 7`, correcto. Si pusiste `tac: '000010'` y `tac: 10`, **incorrecto** — `0x000010` es 16 decimal, no 10. Es una trampa real.

`ci/check-plmn.py` detecta esto antes de desplegar.

---

**[S4] El UE no encuentra al gNB**

```bash
docker compose logs ue | grep -i 'plmn\|search\|cell'
docker compose exec ue ping -c2 10.100.203.30
docker compose exec gnb ss -lnu | grep 4997
```

`gnbSearchList` en `ue.yaml` debe contener el **`linkIp`** del gNB (`radio_net`), no su `ngapIp` ni su `gtpIp`. UERANSIM usa UDP 4997 para el enlace radio emulado; si apuntas a la IP equivocada, el UE queda buscando indefinidamente sin error explícito.

---

**[S5] Fallo de registro / autenticación**

```bash
docker compose logs ue   | grep -iE 'reject|cause|auth|failure'
docker compose logs amf  | grep -iE 'reject|cause|authentication'
docker compose logs ausf | tail -50
docker compose logs udm  | tail -50
```

Tabla de causas 5GMM (⚠️ contrasta con 3GPP TS 24.501 §9.11.3.2; los códigos son estables pero el texto varía):

| Causa | Significado | Origen probable en este lab |
|---|---|---|
| #3 | Illegal UE | Fallo de autenticación: K/OPc no coinciden con el suscriptor |
| #6 | Illegal ME | IMEI rechazado (raro en lab) |
| #7 | 5GS services not allowed | Suscriptor no existe en MongoDB, o IMSI no coincide |
| #9 | UE identity cannot be derived | SUCI mal formado; revisa `protectionScheme` y `routingIndicator` |
| #11 | PLMN not allowed | PLMN del UE fuera de `plmnSupportList` |
| #12 | Tracking area not allowed | TAC no está en `supportTaiList` |
| #15 | No suitable cells in TA | Combinación PLMN+TAC inconsistente |
| #22 | Congestion | UPF caído o AMF sin recursos |

Verificación directa del suscriptor:

```bash
docker compose exec -T mongodb mongosh --quiet free5gc --eval '
  const id="imsi-732101000000001";
  printjson(db.subscriptionData.authenticationData
              .authenticationSubscription.findOne({ueId:id}));
  printjson(db.subscriptionData.provisionedData.amData.findOne({ueId:id}));'
```

Si devuelve `null`, el suscriptor no está aprovisionado (o el `servingPlmnId` no coincide con el PLMN que anuncia el AMF).

Comprueba también `opType`: el valor `8e27b6af...` de ejemplo es un **OPc**, no un OP. Si en `ue.yaml` pones `opType: 'OP'`, la derivación de claves da un resultado distinto y la autenticación falla con causa #3, sin ninguna pista de que el problema sea ese campo.

---

**[S6] / [S8] PDU Session Establishment Reject**

```bash
docker compose logs ue  | grep -i -A5 'pdu session'
docker compose logs smf | grep -iE 'reject|cause|error' | tail -40
```

Causas 5GSM relevantes (⚠️ TS 24.501 §9.11.4.2):

| Causa | Significado | Corrección |
|---|---|---|
| #8 | Operator determined barring | Falta la política en `policyData.ues.smData` |
| #26 | Insufficient resources | Pool de IPs agotado o UPF sin capacidad |
| #27 | Missing or unknown DNN | `apn` en `ue.yaml` ≠ `dnn` en `smfcfg`/`upfcfg`/suscriptor |
| #29 | User auth/authorization failed | El suscriptor no tiene ese DNN en `smData` |
| #31 | Request rejected, unspecified | Genérico; ve a los logs del SMF |
| #33 | Requested service option not subscribed | S-NSSAI no suscrita |
| #67 | Insufficient resources for slice and DNN | Combinación S-NSSAI+DNN no configurada en el UPF |

La causa #67 es la más frecuente y casi siempre significa lo mismo: **el `sd` no coincide**. Verifica los cuatro sitios simultáneamente:

```bash
grep -A2 'sd' config/smfcfg.yaml       # '010203'  (string)
grep -A2 'sd' config/amfcfg.yaml       # '010203'  (string)
grep -A2 'sd' ueransim/ue.yaml         # 0x010203  (hex)
docker compose exec -T mongodb mongosh --quiet free5gc --eval '
  printjson(db.subscriptionData.provisionedData.smData.findOne())'
```

---

**[S7] La asociación PFCP nunca se establece**

Este es el fallo F3, y es el que más tiempo consume porque el UE se registra correctamente y todo *parece* bien hasta que pides una sesión.

```bash
# ¿El UPF está vivo?
docker compose ps upf
docker compose logs upf | tail -50

# ¿Existe la interfaz que crea gtp5g?
docker compose exec upf ip -d link show upfgtp

# ¿Hay tráfico PFCP en N4?
docker compose exec upf timeout 20 tcpdump -i any -n udp port 8805

# Comparación de nodeID — deben ser idénticos
grep -A3 'nodeID' config/upfcfg.yaml
grep -A3 'nodeID' config/smfcfg.yaml
```

Interpretación:

- **Sin paquetes en 8805** → el SMF no está intentando; revisa `userplaneInformation.upNodes.UPF.addr`.
- **Request sin Response** → el UPF recibe pero no responde: casi siempre está muerto porque `gtp5g` no se pudo inicializar. Mira `docker compose logs upf` desde la primera línea.
- **Response con `Cause: Request Rejected`** → `nodeID` desalineado.

Si `ip link show upfgtp` da `Device does not exist`:

```bash
grep '^gtp5g ' /proc/modules          # ¿cargado?
dmesg | grep -i gtp5g | tail -20      # ¿errores del módulo?
docker compose exec upf capsh --print | grep -o net_admin
```

---

**[S9] `uesimtun0` existe pero no pasa tráfico**

Este es el síntoma que quieres eliminar. La causa es casi siempre el **endpoint N3**.

```bash
# ¿Sale GTP-U del gNB?
docker compose exec gnb timeout 20 tcpdump -i any -n udp port 2152

# ¿Llega al UPF?
docker compose exec upf timeout 20 tcpdump -i any -n udp port 2152
```

Cuatro escenarios:

| gNB envía | UPF recibe | Diagnóstico |
|---|---|---|
| No | No | El UE no tiene sesión activa; vuelve a [S6] |
| Sí, a IP X | No | **`endpoints` de N3 en `smfcfg` apunta a la IP equivocada.** El gNB envía a donde el SMF le dijo. Debe ser `10.100.201.20`, la IP del UPF en `n3_net` |
| Sí | Sí | El GTP-U llega; el problema es post-desencapsulación → [S10] |
| Sí, ida y vuelta | Sí | Funciona; el problema está en el DN |

Comprobación cruzada de las tres IPs del UPF:

```bash
docker compose exec upf ip -br addr
# Debe mostrar 10.100.200.20 (N4), 10.100.201.20 (N3), 10.100.204.20 (DN)
grep -A4 'gtpu' config/upfcfg.yaml           # addr debe ser 10.100.201.20
grep -A6 'interfaceType: N3' config/smfcfg.yaml
```

---

**[S10] GTP-U llega al UPF pero el ping no vuelve**

```bash
docker compose exec upf ip -d link show upfgtp
docker compose exec upf ip route
docker compose exec upf iptables -t nat -L POSTROUTING -n -v
docker compose exec upf sysctl net.ipv4.ip_forward

# ¿Se ve tráfico desencapsulado saliendo por upfgtp?
docker compose exec upf timeout 15 tcpdump -i upfgtp -n

# ¿Y saliendo hacia el DN?
docker compose exec upf timeout 15 tcpdump -i any -n \
  'host 10.100.204.10 and icmp'
```

Lista de verificación:

1. `net.ipv4.ip_forward = 1` dentro del contenedor UPF (el `sysctls:` del compose lo fija).
2. Regla `MASQUERADE` para `10.60.0.0/16` presente, **y aplicada a la interfaz correcta**. Los contadores de `iptables -t nat -L -n -v` deben incrementarse al hacer ping. Si están a cero, la regla existe pero no coincide → interfaz equivocada. Aquí es donde `upf-nat.sh` gana sobre asumir `eth0`.
3. `iptables -L FORWARD -n -v` en el UPF no debe estar descartando.
4. El destino (`dn-iperf`) debe tener ruta de vuelta. Como haces MASQUERADE, la ve como si viniera de `10.100.204.20` y responde a la interfaz correcta automáticamente.

---

**[S11] Ping OK, transferencias grandes se cuelgan**

El fallo F7, MTU.

```bash
# Encuentra el MTU real con ping incremental y DF activado
for s in 1300 1400 1450 1464 1472 1500; do
  echo -n "size $s: "
  docker compose exec -T ue ping -I uesimtun0 -M do -s $s -c1 -W2 \
    10.100.204.10 >/dev/null 2>&1 && echo OK || echo "FRAGMENTACION"
done

docker compose exec ue ip link show uesimtun0    # MTU actual
docker network inspect n3_net -f \
  '{{index .Options "com.docker.network.driver.mtu"}}'
```

Corrección: MTU de `uesimtun0` = MTU de la red Docker − 36 bytes de overhead GTP-U. Con redes a 1500 → `uesimtun0` a **1464**. Ya lo hace `ue-entrypoint.sh`; si estás depurando a mano:

```bash
docker compose exec ue ip link set dev uesimtun0 mtu 1464
```

Con la LAN directa no hace falta MSS clamping: el camino son 1500 bytes completos de extremo a extremo. Solo sería necesario si volvieras a meter el laboratorio detrás de una VPN.

## 4. Referencia de captura de paquetes

| Interfaz | Filtro `tcpdump` | Contenedor | Qué revela |
|---|---|---|---|
| N2 (NGAP) | `sctp port 38412` | gnb o amf | NG Setup, NAS encapsulado |
| N3 (GTP-U) | `udp port 2152` | gnb o upf | Plano de usuario, TEIDs |
| N4 (PFCP) | `udp port 8805` | smf o upf | Association, Session Establishment, PDRs/FARs |
| Radio emulada | `udp port 4997` | ue o gnb | Enlace UERANSIM UE↔gNB |
| SBI | `tcp port 8000` | cualquier NF | HTTP/2 entre NFs |
| Post-túnel | `-i upfgtp` | upf | Paquetes IP del UE ya desencapsulados |

Captura a fichero para Wireshark:

```bash
docker compose exec -T upf timeout 60 tcpdump -i any -s0 -w - \
  'udp port 2152 or udp port 8805' > /tmp/upf.pcap
```

En Wireshark: `Analyze → Decode As` → puerto 38412 como **NGAP**, 8805 como **PFCP**, 2152 como **GTP**. Con `cipheringOrder: [NEA0]` en `amfcfg`, el NAS aparece en claro dentro del NGAP, lo que hace legibles los `Registration Request`, `Authentication Request` y los códigos de causa exactos.

## 5. Comandos de un vistazo

```bash
# Estado global
docker compose ps
docker compose logs -f --tail=50 amf smf upf gnb ue

# Todos los rechazos, en una sola pasada
docker compose logs 2>&1 | grep -iE 'reject|fail|error|cause' | tail -40

# Estado del UE (UERANSIM expone una CLI)
docker compose exec ue ./nr-cli --dump
docker compose exec ue ./nr-cli imsi-732101000000001 -e status
docker compose exec ue ./nr-cli imsi-732101000000001 -e ps-list

# Estado del gNB
docker compose exec gnb ./nr-cli --dump
docker compose exec gnb ./nr-cli UERANSIM-gnb-732-101-1 -e status

# Forzar re-registro del UE sin recrear el contenedor
docker compose exec ue ./nr-cli imsi-732101000000001 -e deregister normal
docker compose restart ue

# NFs registradas en el NRF
docker compose exec -T mongodb bash -c \
  'curl -s http://10.100.200.10:8000/nnrf-nfm/v1/nf-instances' | head -c 2000
```

## 6. Reinicio limpio

El orden importa. Levantar la antena antes de que la asociación PFCP esté hecha
produce fallos que parecen de configuración y son de temporización.

La versión antigua de este documento intercalaba `sleep` entre etapas. Ya no
hace falta: `--wait` y `ci/wait-ready.sh` esperan a condiciones reales en vez de
a un número de segundos inventado.

```bash
docker compose --profile ran --profile obs --profile probes --profile tools down
```

```bash
docker compose up -d --wait --wait-timeout 600 mongodb
```

```bash
docker compose up -d && bash ci/wait-ready.sh
```

```bash
bash ci/provision-subscriber.sh && docker compose --profile ran up -d
```

Y, solo si el núcleo ya responde y hay memoria de sobra:

```bash
docker compose --profile obs --profile probes up -d
```

`ci/wait-ready.sh` espera, con un presupuesto **por comprobación**, a que
MongoDB responda, a que el NRF y el AMF estén arriba, a que cada pieza se
registre, y a que la asociación PFCP se establezca. Esa última es la que
justifica todo el orden.

> **Fíjate en que no lleva `-v`.** Con `-v` se borra el volumen de MongoDB y con
> él el abonado, así que habría que reprovisionarlo. El comando de arriba lo
> conserva.

---

**Anterior:** [04 — Operación diaria](04-operacion.md) ·
**Siguiente:** [06 — PLMN e identidades](06-plmn-e-identidades.md)
