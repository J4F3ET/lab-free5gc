#!/usr/bin/env python3
"""Verifica coherencia de PLMN / TAC / S-NSSAI / DNN / nodeID
entre los YAML de free5GC y de UERANSIM. Sale con codigo 1 si hay
cualquier discrepancia."""
import sys, pathlib, yaml

MCC = "732"
MNC = "101"
SST = 1
SD_STR = "010203"      # notacion free5GC
SD_INT = 0x010203      # notacion UERANSIM
TAC_STR = "000001"     # notacion free5GC
TAC_INT = 1            # notacion UERANSIM
DNN = "internet"
UPF_NODE_ID = "10.100.200.20"
UPF_N3_ADDR = "10.100.201.20"
UE_CIDR = "10.60.0.0/16"

errors = []

def load(p):
    path = pathlib.Path(p)
    if not path.exists():
        errors.append(f"[FALTA] {p}")
        return {}
    return yaml.safe_load(path.read_text()) or {}

def walk_plmn(node, origin):
    """Recorre el arbol y valida cualquier dict con mcc/mnc."""
    if isinstance(node, dict):
        if "mcc" in node and "mnc" in node:
            mcc, mnc = node["mcc"], node["mnc"]
            if not isinstance(mcc, str) or not isinstance(mnc, str):
                errors.append(
                    f"[TIPO] {origin}: mcc/mnc deben ir entrecomillados "
                    f"(recibido mcc={mcc!r} mnc={mnc!r})")
            if str(mcc) != MCC or str(mnc) != MNC:
                errors.append(f"[PLMN] {origin}: {mcc}/{mnc} != {MCC}/{MNC}")
        for v in node.values():
            walk_plmn(v, origin)
    elif isinstance(node, list):
        for v in node:
            walk_plmn(v, origin)

def walk_snssai(node, origin, sd_expected):
    if isinstance(node, dict):
        if "sst" in node and "sd" in node:
            if node["sst"] != SST or node["sd"] != sd_expected:
                fmt = lambda v: (f"0x{v:06x} ({v})" if isinstance(v, int)
                                 else repr(v))
                errors.append(
                    f"[SNSSAI] {origin}: sst={node['sst']} sd={fmt(node['sd'])} "
                    f"!= sst={SST} sd={fmt(sd_expected)}")
        for v in node.values():
            walk_snssai(v, origin, sd_expected)
    elif isinstance(node, list):
        for v in node:
            walk_snssai(v, origin, sd_expected)

# ---- free5GC ----
for f in ["amfcfg", "smfcfg", "nssfcfg", "nrfcfg"]:
    cfg = load(f"config/{f}.yaml")
    walk_plmn(cfg, f)
    walk_snssai(cfg, f, SD_STR)

amf = load("config/amfcfg.yaml").get("configuration", {})
for tai in amf.get("supportTaiList", []):
    if str(tai.get("tac")) != TAC_STR:
        errors.append(f"[TAC] amfcfg: {tai.get('tac')!r} != {TAC_STR!r}")
if DNN not in amf.get("supportDnnList", []):
    errors.append(f"[DNN] amfcfg.supportDnnList no contiene {DNN!r}")

# ---- nodeID PFCP: la causa de F3 ----
smf = load("config/smfcfg.yaml").get("configuration", {})
upf = load("config/upfcfg.yaml")
smf_upf = smf.get("userplaneInformation", {}).get("upNodes", {}).get("UPF", {})
if str(smf_upf.get("nodeID")) != UPF_NODE_ID:
    errors.append(f"[PFCP] smfcfg nodeID={smf_upf.get('nodeID')!r} != {UPF_NODE_ID!r}")
if str(upf.get("pfcp", {}).get("nodeID")) != UPF_NODE_ID:
    errors.append(f"[PFCP] upfcfg nodeID={upf.get('pfcp',{}).get('nodeID')!r} != {UPF_NODE_ID!r}")

# ---- endpoint N3: la causa de "uesimtun0 sube pero no pasa trafico" ----
for iface in smf_upf.get("interfaces", []):
    if iface.get("interfaceType") == "N3":
        if UPF_N3_ADDR not in [str(e) for e in iface.get("endpoints", [])]:
            errors.append(
                f"[N3] smfcfg endpoints={iface.get('endpoints')} "
                f"no contiene la IP de n3_net {UPF_N3_ADDR}")
for iface in upf.get("gtpu", {}).get("ifList", []):
    if str(iface.get("addr")) != UPF_N3_ADDR:
        errors.append(f"[N3] upfcfg gtpu addr={iface.get('addr')!r} != {UPF_N3_ADDR!r}")

# ---- pool de UEs coherente entre SMF y UPF ----
pools = [p.get("cidr") for u in smf_upf.get("sNssaiUpfInfos", [])
         for d in u.get("dnnUpfInfoList", []) for p in d.get("pools", [])]
if UE_CIDR not in pools:
    errors.append(f"[POOL] smfcfg pools={pools} no contiene {UE_CIDR}")
if UE_CIDR not in [d.get("cidr") for d in upf.get("dnnList", [])]:
    errors.append(f"[POOL] upfcfg dnnList no contiene {UE_CIDR}")

# ---- UERANSIM ----
gnb = load("ueransim/gnb.yaml")
ue  = load("ueransim/ue.yaml")
for name, cfg in (("gnb", gnb), ("ue", ue)):
    if str(cfg.get("mcc")) != MCC or str(cfg.get("mnc")) != MNC:
        errors.append(f"[PLMN] {name}.yaml: {cfg.get('mcc')}/{cfg.get('mnc')} != {MCC}/{MNC}")
if gnb.get("tac") != TAC_INT:
    errors.append(f"[TAC] gnb.yaml tac={gnb.get('tac')!r} != {TAC_INT} "
                  f"(equivalente decimal de '{TAC_STR}')")
walk_snssai(gnb.get("slices"), "gnb.yaml", SD_INT)
walk_snssai(ue.get("sessions"), "ue.yaml", SD_INT)
walk_snssai(ue.get("configured-nssai"), "ue.yaml", SD_INT)

# ---- IMSI: longitud y prefijo ----
supi = str(ue.get("supi", ""))
if not supi.startswith("imsi-"):
    errors.append(f"[SUPI] {supi!r} debe empezar por 'imsi-'")
else:
    imsi = supi[5:]
    if len(imsi) != 15:
        errors.append(f"[SUPI] IMSI {imsi!r} tiene {len(imsi)} digitos, deben ser 15 "
                      f"(MCC {len(MCC)} + MNC {len(MNC)} + MSIN {15-len(MCC)-len(MNC)})")
    if not imsi.startswith(MCC + MNC):
        errors.append(f"[SUPI] IMSI {imsi!r} no empieza por {MCC+MNC}")

# ---- DNN / APN ----
for s in ue.get("sessions", []):
    if s.get("apn") != DNN:
        errors.append(f"[DNN] ue.yaml apn={s.get('apn')!r} != {DNN!r}")

if errors:
    print(f"\n  {len(errors)} inconsistencia(s):\n")
    for e in errors:
        print(f"   {e}")
    sys.exit(1)
print(f"  Configuracion coherente: PLMN {MCC}/{MNC}, "
      f"S-NSSAI {SST}/{SD_STR}, TAC {TAC_STR}, DNN {DNN}")