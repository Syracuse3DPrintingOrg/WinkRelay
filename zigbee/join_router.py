#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["bellows==1.1.0"]
# ///
"""Join the Wink Relay's Zigbee radio to an existing network as a router.

  zigbee/join_router.py --channel 25 --pan-id 0x1A2B            # join
  zigbee/join_router.py --channel 25 --pan-id 0x1A2B --scan-only # just look

Run it once, with tcpser running on the Relay, `adb forward tcp:16638
tcp:6638` on this computer, and permit join open on your coordinator
(docs/7-zigbee-router.md). Your coordinator's channel and PAN id are in its
settings (Zigbee2MQTT: Settings > Advanced; ZHA: the network page).

It scans the channel, picks your network by PAN id (every router answers the
scan, so a network shows up more than once), and joins it as a router with
the well-known Home Automation link key, which Zigbee 3.0 coordinators
accept from older devices by default. The radio keeps the network in its
flash; zbrouter resumes it after every boot.
"""
import argparse
import asyncio
import logging

import bellows.types as t
from bellows.cli import util


async def main(a):
    s = await util.setup(a.connect, 57600)
    print("EZSP version", s.ezsp_version, "EUI64", (await s.getEui64())[0])
    print("stored network", await s.getNetworkParameters())
    for cfg, val in ((t.EzspConfigId.CONFIG_STACK_PROFILE, 2), (t.EzspConfigId.CONFIG_SECURITY_LEVEL, 5)):
        await s.setConfigurationValue(cfg, val)

    print(f"scanning channel {a.channel}")
    found = [n[0] for n in await s.startScan(t.EzspNetworkScanType.ACTIVE_SCAN, 1 << a.channel, 3)]
    nets = {}
    for n in found:
        nets.setdefault(str(n.extendedPanId), n)
    for n in nets.values():
        print(f"  PAN 0x{int(n.panId):04X} extended {n.extendedPanId} allowing join: {bool(n.allowingJoin)}")
    mine = [n for n in nets.values() if int(n.panId) == a.pan_id]
    if a.scan_only or not mine:
        if not mine:
            print(f"PAN 0x{a.pan_id:04X} not found on channel {a.channel}")
        await s.disconnect()
        return
    net = mine[0]
    if not net.allowingJoin:
        print("that network is not allowing joins: open permit join on the coordinator and run this again")
        await s.disconnect()
        return

    isc = t.EmberInitialSecurityState()
    isc.bitmask = (t.EmberInitialSecurityBitmask.HAVE_PRECONFIGURED_KEY
                   | t.EmberInitialSecurityBitmask.REQUIRE_ENCRYPTED_KEY)
    isc.preconfiguredKey = t.KeyData(b"ZigBeeAlliance09")
    isc.networkKey = t.KeyData(bytes(16))
    isc.networkKeySequenceNumber = t.uint8_t(0)
    isc.preconfiguredTrustCenterEui64 = t.EUI64.convert("00:00:00:00:00:00:00:00")
    print("security", await s.setInitialSecurityState(isc))

    p = t.EmberNetworkParameters()
    p.extendedPanId = net.extendedPanId
    p.panId = net.panId
    p.radioTxPower = t.uint8_t(8)
    p.radioChannel = t.uint8_t(net.channel)
    p.joinMethod = t.EmberJoinMethod.USE_MAC_ASSOCIATION
    p.nwkManagerId = t.EmberNodeId(0)
    p.nwkUpdateId = t.uint8_t(0)
    p.channels = t.uint32_t(0)

    fut = asyncio.get_running_loop().create_future()

    def cb(name, resp):
        if name == "stackStatusHandler" and not fut.done():
            fut.set_result(resp)

    cbid = s.add_callback(cb)
    print("joining as router:", await s.joinNetwork(t.EmberNodeType.ROUTER, p))
    try:
        print("stack status", await asyncio.wait_for(fut, 60))
    except asyncio.TimeoutError:
        print("no answer within 60 s: check permit join and the channel")
    print("network state", await s.networkState(), "node id", await s.getNodeId())
    s.remove_callback(cbid)
    await s.disconnect()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--channel", type=int, required=True, help="your network's channel, 11 to 26")
    ap.add_argument("--pan-id", type=lambda v: int(v, 0), required=True, help="your network's PAN id, for example 0x1A2B")
    ap.add_argument("--connect", default="socket://127.0.0.1:16638", help="where the radio is reachable")
    ap.add_argument("--scan-only", action="store_true", help="list the networks on the channel and stop")
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main(ap.parse_args()))
