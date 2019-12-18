#!/usr/bin/env python
# hobbes3

import logging
import json
import re
import splunk_rest.splunk_rest as sr
from splunk_rest.splunk_rest import splunk_rest, try_response
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

def update_devices(device):
    network_id = device["networkId"]
    device_serial = device["serial"]
    device_model = device["model"]

    device["tags"] = device["tags"].split() if device.get("tags") else None

    meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/uplink".format(network_id, device_serial)
    r = s.get(meraki_url, headers=meraki_headers)

    @try_response
    def send_devices(r):
        meta = {
            "request_id": r.request_id,
            "network_id": network_id,
            "device_serial": device_serial,
        }
        uplinks = parse_meraki_response(r, list)

        for i, uplink in enumerate(uplinks):
            # Convert values like "Wan 1" to "wan1"
            uplinks[i]["interface"] = uplink["interface"].lower().replace(" ", "")

        device["uplinks"] = uplinks
        device["splunk_rest"] = {
            "session_id": sr.session_id,
            "request_id": r.request_id,
        }

        @try_response
        def append_device_perf(rr):
            device_perf = parse_meraki_response(rr, dict)
            device.update(device_perf)
            device["splunk_rest"]["request_id_2"] = rr.request_id

        if device_model.startswith("MX"):
            meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/performance".format(network_id, device_serial)
            logger.debug("Getting device performance per MX device...", extra=meta)
            rr = s.get(meraki_url, headers=meraki_headers)
            append_device_perf(rr, extra={"request_id": r.request_id, "request_id_2": rr.request_id})

        event = {
            "index": index,
            "sourcetype": "meraki_api_device",
            "source": __file__,
            "event": device,
        }

        data = json.dumps(event)

        logger.debug("Sending device data to Splunk...", extra=meta)
        s.post(hec_url, headers=hec_headers, data=data)

    send_devices(r)

def get_clients(network):
    network_id = network["id"]

    meraki_url = "https://api.meraki.com/api/v0/networks/{}/clients".format(network_id)
    startingAfter = "a000000"

    data = ""

    @try_response
    def get_client_data(r):
        clients = parse_meraki_response(r, list)
        m = meta.copy()
        m["client_count"] = len(clients)
        logger.debug("Got clients.", extra=m)

        client_data = ""

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

            client_data += json.dumps(event)

        return client_data

    while startingAfter:
        params = {
            "perPage": 1000,
            "timespan": repeat,
        }
        r = s.get(meraki_url, headers=meraki_headers, params=params)
        meta = {
            "request_id": r.request_id,
            "network_id": network_id,
        }

        link_header = r.headers["Link"]

        if match := re.search("startingAfter=(\w+)>; rel=next$", link_header):
            startingAfter = match.group(1)
            m = meta.copy()
            m["startingAfter"] = startingAfter
            logger.debug("Found next startingAfter param.", extra=m)
        else:
            startingAfter = False
            logger.debug("Didn't find next startingAfter param. Finishing.", extra=meta)

        data += get_client_data(r)

    if data:
        logger.debug("Sending client data to Splunk...", extra={"network_id": network_id})
        s.post(hec_url, headers=hec_headers, data=data)
    else:
        logger.debug("No client data to send to Splunk.", extra={"network_id": network_id})

@splunk_rest
def meraki_api():
    logger.info("Getting networks...")
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)

    @try_response
    def get_and_send_networks(r):
        meta = {
            "request_id": r.request_id,
        }

        networks = parse_meraki_response(r, list)
        m = meta.copy()
        m["network_count"] = len(networks)
        logger.debug("Got networks.", extra=m)

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

        logger.info("Sending network data to Splunk...", extra=meta)
        s.post(hec_url, headers=hec_headers, data=data)

        return networks

    networks = get_and_send_networks(r)

    if script_args.sample:
        networks = networks[:20]

    logger.info("Getting and sending client data per network...")
    sr.multiprocess(get_clients, networks)

    # Currently this is the only way to get uplink ip, which is needed for per-device /lossAndLatencyHistory later.
    logger.info("Getting device statuses...")
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/deviceStatuses".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)

    @try_response
    def get_device_statuses(r):
        device_statuses = parse_meraki_response(r, list)
        meta = {
            "request_id": r.request_id,
            "device_status_count": len(device_statuses),
        }
        logger.info("Got device statuses.", extra=meta)

        return device_statuses

    device_statuses = get_device_statuses(r)

    logger.info("Getting devices...")
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/devices".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)

    @try_response
    def get_devices(r):
        devices = parse_meraki_response(r, list)
        meta = {
            "request_id": r.request_id,
            "device_count": len(devices),
        }
        logger.info("Got devices.", extra=meta)

        def add_device_status(device):
            device_serial = device["serial"]
            device_status = next((d for d in device_statuses if d["serial"] == device_serial), None)

            if device_status:
                device["status"] = device_status["status"]

            return device

        devices_with_status = [add_device_status(d) for d in devices]

        return devices_with_status

    devices = get_devices(r)

    if script_args.sample:
        devices = devices[:40]

    logger.info("Updating and sending devices data...")
    sr.multiprocess(update_devices, devices)

@splunk_rest
def meraki_loss_latency_history():
    logger.info("Getting and sending MX device loss and latency...")
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/uplinksLossAndLatency".format(org_id)
    # Going back at least 2 minutes in the past as required by the API doc.
    t1 = int(sr.start_time) - 2 * 60
    # Maximum span is 5 minutes.
    t0 = t1 - 5 * 60
    params = {
        "t0": t0,
        "t1": t1,
    }
    r = s.get(meraki_url, headers=meraki_headers, params=params)

    @try_response
    def send_loss_latency_history(r):
        meta = {
            "request_id": r.request_id,
        }
        device_stats = parse_meraki_response(r, list)
        m = meta.copy()
        m["device_stats_count"] = len(device_stats)
        logger.info("Got device stats.", extra=m)

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

    send_loss_latency_history(r)

if __name__ == "__main__":
    sr.arg_parser.add_argument("--loss", action="store_true", help="only get the loss and latency history stats")
    script_args = sr.get_script_args()

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
