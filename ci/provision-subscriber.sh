#!/usr/bin/env bash
set -euo pipefail

MCC="${MCC:-732}"
MNC="${MNC:-101}"
IMSI="${IMSI:-732101000000001}"
K="${K:-8baf473f2f8fd09487cccbd7097c6862}"
OPC="${OPC:-8e27b6af0e692e750f32667a3b14605d}"
SST="${SST:-1}"
SD="${SD:-010203}"
DNN="${DNN:-internet}"

echo "[provision] suscriptor imsi-${IMSI} en PLMN ${MCC}/${MNC}"

docker compose exec -T mongodb mongosh --quiet free5gc <<EOF
const ueId   = "imsi-${IMSI}";
const plmnID = "${MCC}${MNC}";
const snssai = { sst: ${SST}, sd: "${SD}" };

db.subscriptionData.authenticationData.authenticationSubscription.replaceOne(
  { ueId: ueId },
  {
    ueId: ueId,
    authenticationMethod: "5G_AKA",
    permanentKey: { permanentKeyValue: "${K}",
                    encryptionKey: 0, encryptionAlgorithm: 0 },
    sequenceNumber: "16f3b3f70fc2",
    authenticationManagementField: "8000",
    milenage: { op: { opValue: "", encryptionKey: 0, encryptionAlgorithm: 0 } },
    opc: { opcValue: "${OPC}", encryptionKey: 0, encryptionAlgorithm: 0 }
  },
  { upsert: true }
);

db.subscriptionData.provisionedData.amData.replaceOne(
  { ueId: ueId, servingPlmnId: plmnID },
  {
    ueId: ueId, servingPlmnId: plmnID,
    gpsis: [ "msisdn-0900000000" ],
    subscribedUeAmbr: { uplink: "1 Gbps", downlink: "2 Gbps" },
    nssai: { defaultSingleNssais: [ snssai ], singleNssais: [ snssai ] }
  },
  { upsert: true }
);

db.subscriptionData.provisionedData.smData.replaceOne(
  { ueId: ueId, servingPlmnId: plmnID, singleNssai: snssai },
  {
    ueId: ueId, servingPlmnId: plmnID, singleNssai: snssai,
    dnnConfigurations: {
      "${DNN}": {
        pduSessionTypes: {
          defaultSessionType: "IPV4",
          allowedSessionTypes: [ "IPV4" ]
        },
        sscModes: { defaultSscMode: "SSC_MODE_1",
                    allowedSscModes: [ "SSC_MODE_2", "SSC_MODE_3" ] },
        "5gQosProfile": { "5qi": 9, arp: { priorityLevel: 8,
                          preemptCap: "", preemptVuln: "" }, priorityLevel: 8 },
        sessionAmbr: { uplink: "200 Mbps", downlink: "400 Mbps" }
      }
    }
  },
  { upsert: true }
);

db.policyData.ues.smData.replaceOne(
  { ueId: ueId },
  { ueId: ueId,
    smPolicySnssaiData: {
      "0${SST}${SD}": {
        snssai: snssai,
        smPolicyDnnData: { "${DNN}": { dnn: "${DNN}" } }
      }
    }
  },
  { upsert: true }
);

print("subscriptionData.authenticationSubscription: " +
      db.subscriptionData.authenticationData.authenticationSubscription
        .countDocuments({ueId: ueId}));
print("provisionedData.amData: " +
      db.subscriptionData.provisionedData.amData.countDocuments({ueId: ueId}));
print("provisionedData.smData: " +
      db.subscriptionData.provisionedData.smData.countDocuments({ueId: ueId}));
EOF

echo "[provision] listo"
