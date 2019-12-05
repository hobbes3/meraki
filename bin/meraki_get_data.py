#!/usr/bin/env python
# hobbes3

import logging
import json
import splunk_rest.splunk_rest as sr
from splunk_rest.splunk_rest import rest_wrapped
from datetime import datetime

def parse_meraki_response(r, var_type):
    meta = {
        "request_id": r.request_id,
        "var_type": str(var_type),
    }

    try:
        r_json = r.json()
        if "errors" not in r_json and isinstance(r_json, var_type):
            return r_json
        else:
            m = meta.copy()
            m["json"] = r_json
            logger.warning("Bad response!", extra=m)
            raise
    except json.decoder.JSONDecodeError:
        m = meta.copy()
        m["text"] = r.text
        logger.warning("Not a valid json response!", extra=m)
        raise
    except:
        raise

def update_and_send_devices(device):
    network_id = device["networkId"]
    device_serial = device["serial"]
    device_model = device["model"]

    device["tags"] = device["tags"].split() if device.get("tags") else None

    meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/uplink".format(network_id, device_serial)
    r = s.get(meraki_url, headers=meraki_headers)
    meta = {
        "request_id": r.request_id,
        "network_id": network_id,
        "device_serial": device_serial,
    }
    try:
        uplinks = parse_meraki_response(r, list)

        for i, uplink in enumerate(uplinks):
            # Convert values like "Wan 1" to "wan1"
            uplinks[i]["interface"] = uplink["interface"].lower().replace(" ", "")

        device["uplinks"] = uplinks

        if device_model.startswith("MX"):
            meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/performance".format(network_id, device_serial)
            logger.debug("Getting device performance per MX device...", extra=meta)
            rr = s.get(meraki_url, headers=meraki_headers)

            try:
                device_perf = parse_meraki_response(rr, dict)
                device.update(device_perf)
            except:
                m = meta.copy()
                m["request_id_2"] = rr.request_id
                logger.warning("", exc_info=True, extra=m)

        device["splunk_rest"] = {
            "session_id": sr.session_id,
            "request_id": r.request_id,
        }

        event = {
            "index": index,
            "sourcetype": "meraki_api_device",
            "source": __file__,
            "event": device,
        }

        data = json.dumps(event)

        logger.debug("Sending device data to Splunk...", extra=meta)
        s.post(hec_url, headers=hec_headers, data=data)
    except:
        logger.warning("", exc_info=True, extra=meta)

def get_and_send_clients(network):
    network_id = network["id"]

    meraki_url = "https://api.meraki.com/api/v0/networks/{}/clients".format(network_id)
    params = {
        "timespan": sr.config["meraki_api"]["repeat"]
    }
    r = s.get(meraki_url, headers=meraki_headers, params=params)
    meta = {
        "request_id": r.request_id,
        "network_id": network_id,
    }

    try:
        clients = parse_meraki_response(r, list)
        m = meta.copy()
        m["client_count"] = len(clients)
        logger.debug("Found clients.", extra=m)

        data = ""

        for client in clients:
            client["splunk_rest"] = {
                "session_id": sr.session_id,
                "request_id": r.request_id,
            }
            client["networkId"] = network_id

            event = {
                "index": index,
                "sourcetype": "meraki_api_client",
                "source": __file__,
                "event": client,
            }

            data += json.dumps(event)

        if data:
            logger.debug("Sending client data to Splunk...", extra=meta)
            s.post(hec_url, headers=hec_headers, data=data)
        else:
            logger.debug("No client data to send to Splunk.", extra=meta)
    except:
        logger.warning("", exc_info=True, extra=meta)

@rest_wrapped
def meraki_api():
    logger.info("Getting networks...")
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)
    try:
        networks = parse_meraki_response(r, list)
        logger.debug("Found networks.", extra={"network_count": len(networks)})

        data = ""

        # Sending data to Splunk via HEC in batch mode.
        for network in networks:
            # Improved network tags so they become multivalues in Splunk instead of a space-delimited string
            network["tags"] = network["tags"].split() if network.get("tags") else None
            network["splunk_rest"] = {
                "session_id": sr.session_id,
                "request_id": r.request_id,
            }

            event = {
                "index": index,
                "sourcetype": "meraki_api_network",
                "source": __file__,
                "event": network,
            }

            data += json.dumps(event)

        logger.info("Sending network data to Splunk...")
        s.post(hec_url, headers=hec_headers, data=data)
    except:
        logger.warning("", exc_info=True, extra={"request_id": r.request_id})

    if script_args.sample:
        networks = networks[:20]

    logger.info("Getting and sending client data per network...")
    sr.multiprocess(get_and_send_clients, networks)

    # Currently this is the only way to get uplink ip, which is needed for per-device /lossAndLatencyHistory later.
    logger.info("Getting device statuses...")
    device_statuses = []
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/deviceStatuses".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)
    try:
        device_statuses = parse_meraki_response(r, list)
        logger.info("Found device statuses.", extra={"device_status_count": len(device_statuses)})
    except:
        logger.warning("", exc_info=True, extra={"request_id": r.request_id})

    logger.info("Getting devices...")
    devices = []
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/devices".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)

    try:
        devices = parse_meraki_response(r, list)
        logger.info("Found devices.", extra={"device_count": len(devices)})

        def add_device_status(device):
            device_serial = device["serial"]
            device_status = next((d for d in device_statuses if d["serial"] == device_serial), None)

            if device_status:
                device["status"] = device_status["status"]

            return device

        devices = [add_device_status(d) for d in devices]
    except:
        logger.warning("", exc_info=True, extra={"request_id": r.request_id})

    if script_args.sample:
        devices = devices[:40]

    logger.info("Updating and sending devices data...")
    sr.multiprocess(update_and_send_devices, devices)

@rest_wrapped
def meraki_loss_latency_history():
    logger.info("Getting and sending MX device loss and latency...")

    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/uplinksLossAndLatency".format(org_id)
    # Going back 5 minutes in the past since the docs require at least 2 minutes.
    t1 = int(sr.start_time - sr.start_time % 60) - 5 * 60
    # Maximum span is 5 minutes.
    t0 = t1 - 5 * 60
    params = {
        "t0": t0,
        "t1": t1,
    }
    r = s.get(meraki_url, headers=meraki_headers, params=params)
    meta = {
        "request_id": r.request_id,
    }

    try:
        device_stats = parse_meraki_response(r, list)
        m = meta.copy()
        m["device_stats_count"] = len(device_stats)
        logger.info("Found device stats.", extra=m)

        data = ""

        for device_stat in device_stats:
            stats = device_stat["timeSeries"]

            for stat in stats:
                d = device_stat.copy()
                del d["timeSeries"]
                stat.update(d)
                stat["splunk_rest"] = {
                    "session_id": sr.session_id,
                    "request_id": r.request_id,
                }

                event = {
                    # 2019-12-05T07:38:40Z
                    "time": datetime.strptime(stat["ts"], "%Y-%m-%dT%H:%M:%SZ").timestamp(),
                    "index": index,
                    "sourcetype": "meraki_api_device_loss_and_latency",
                    "source": __file__,
                    "event": stat,
                }

                data += json.dumps(event)

        if data:
            logger.debug("Sending device loss and latency data to Splunk...", extra=meta)
            s.post(hec_url, headers=hec_headers, data=data)
        else:
            logger.debug("No device loss and latency data to send to Splunk.", extra=meta)
    except:
        logger.exception("", extra=meta)


if __name__ == "__main__":
    sr.arg_parser.add_argument("-l", "--loss", help="only get the loss and latency history stats", action="store_true")
    script_args = sr.arg_parser.parse_args()

    logger = logging.getLogger("splunk_rest.splunk_rest")
    s = sr.retry_session()

    meraki_headers = sr.config["meraki_api"]["headers"]
    org_id = sr.config["meraki_api"]["org_id"]
    repeat = sr.config["meraki_api"]["repeat"]
    index = "main" if script_args.test else sr.config["meraki_api"]["index"]

    hec_url = sr.config["hec"]["url"]
    hec_headers = sr.config["hec"]["headers"]

    if script_args.loss:
        meraki_loss_latency_history()
    else:
        meraki_api()
