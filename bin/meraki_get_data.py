#!/usr/bin/env python
# hobbes3

import logging
import json
import re
import pprint
import splunk_rest.splunk_rest as sr
from splunk_rest.splunk_rest import splunk_rest, try_response
from datetime import datetime

@splunk_rest
def meraki_org():
    logger.info("Getting organizations...")
    meraki_url = "https://api.meraki.com/api/v0/organizations"
    r = s.get(meraki_url, headers=meraki_headers)

    @try_response
    def print_orgs(r):
        pp = pprint.PrettyPrinter()
        pp.pprint(r.json())

    orgs = print_orgs(r)

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

@try_response
def get_startingAfter(r, meta=None):
    if meta:
        meta["request_id"] = r.request_id
    else:
        meta = {
            "request_id": r.request_id
        }

    link_header = r.headers.get("Link", None)

    if not link_header:
        logger.debug("No Link key in header. Finishing.", extra=meta)
        return None

    if match := re.search("startingAfter=(\w+)>; rel=next$", link_header):
        startingAfter = match.group(1)
        m = meta.copy()
        m["link_header"] = link_header
        logger.debug("Found next startingAfter in Link.", extra=m)
        return startingAfter
    else:
        logger.debug("Didn't find next startingAfter in Link. Finishing.", extra=meta)
        return None

@splunk_rest
def meraki_api(org_id):
    logger.info("Getting networks...", extra={"org_id": org_id})
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)

    networks = get_and_send_networks(r, org_id)

    if not networks:
        logger.error("No networks found!")
        return

    #networks = [n for n in networks if n["id"]=="L_663717995083730966"]

    logger.info("Getting and sending client data per network...")
    sr.multiprocess(get_clients, networks)

    # Currently this is the only way to get uplink ip, which is needed for per-device /lossAndLatencyHistory later.
    logger.info("Getting device statuses...")
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/deviceStatuses".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)

    device_statuses = get_device_statuses(r)

    logger.info("Getting devices...")
    devices = []
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/devices".format(org_id)
    startingAfter = True
    while startingAfter:
        params = {
            "perPage": 1000,
            "timespan": repeat,
        }
        if isinstance(startingAfter, str):
            params["startingAfter"] = startingAfter

        r = s.get(meraki_url, headers=meraki_headers, params=params)
        devices += get_devices(r, networks, device_statuses) or []
        if script_args.sample:
            logger.debug("Limiting devices to 40 or less.")
            devices = devices[:40]
            break

        startingAfter = get_startingAfter(r)

    logger.info("Updating and sending devices data...")
    sr.multiprocess(update_devices, devices)

@try_response
def get_and_send_networks(r, org_id):
    meta = {
        "request_id": r.request_id,
    }

    networks = parse_meraki_response(r, list)
    m = meta.copy()
    m["network_count"] = len(networks)
    logger.debug("Got networks.", extra=m)

    data = ""

    if networks:
        def match_tags_regexes(network, regexes):
            tags = network["tags"] or [""]
            network_id = network["id"]

            for tag in tags:
                for regex in regexes:
                    if re.match(regex, tag):
                        return True

            m = meta.copy()
            m.update({
                "network_id": network["id"],
                "regexes": regexes,
                "tags": tags,
            })

            logger.debug("Network skipped since it doesn't match regex.", extra=m)

            return False

        for i, network in enumerate(networks):
            # Improved network tags so they become multivalues in Splunk instead of a space-delimited string
            networks[i]["tags"] = network["tags"].split() if network.get("tags") else None

        if network_tag_regexes:
            networks = [n for n in networks if match_tags_regexes(n, network_tag_regexes)]

        if script_args.sample:
            logger.debug("Limiting networks to 20 or less.")
            networks = networks[:20]

        # Sending data to Splunk via HEC in batch mode.
        for network in networks:
            network["organizationId"] = org_id
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

def get_clients(network):
    network_id = network["id"]
    network_type = network["type"]

    meta = {
        "network_id": network_id,
    }

    if network_type == "systems manager":
        m = meta.copy()
        m["network_type"] = network_type
        logger.debug("Network skipped for getting clients since it's a systems manager network.", extra=m)
        return

    meraki_url = "https://api.meraki.com/api/v0/networks/{}/clients".format(network_id)
    data = ""
    startingAfter = True
    while startingAfter:
        params = {
            "perPage": 1000,
            "timespan": repeat,
        }
        if isinstance(startingAfter, str):
            params["startingAfter"] = startingAfter

        r = s.get(meraki_url, headers=meraki_headers, params=params)

        data += get_client_data(r, org_id, network_id) or ""
        startingAfter = get_startingAfter(r, meta)

    if data:
        logger.debug("Sending client data to Splunk...", extra=meta)
        s.post(hec_url, headers=hec_headers, data=data)
    else:
        logger.debug("No client data to send to Splunk.", extra=meta)

@try_response
def get_client_data(r, org_id, network_id):
    meta = {
        "request_id": r.request_id,
        "network_id": network_id,
    }

    clients = parse_meraki_response(r, list)
    m = meta.copy()
    m["client_count"] = len(clients)
    logger.debug("Got clients.", extra=m)

    data = ""

    for client in clients:
        client["splunk_rest"] = {
            "session_id": sr.session_id,
            "request_id": r.request_id,
        }
        client["networkId"] = network_id
        client["organizationId"] = org_id

        event = {
            "index": index,
            "sourcetype": "meraki_api_client",
            "source": __file__,
            "event": client,
        }

        data += json.dumps(event)

    return data

@try_response
def get_device_statuses(r):
    device_statuses = parse_meraki_response(r, list)
    meta = {
        "request_id": r.request_id,
        "device_status_count": len(device_statuses),
    }
    logger.info("Got device statuses.", extra=meta)

    return device_statuses

@try_response
def get_devices(r, networks, device_statuses):
    devices = parse_meraki_response(r, list)
    meta = {
        "request_id": r.request_id,
    }
    m = meta.copy()
    m["device_count"] = len(devices)
    logger.info("Got devices.", extra=m)

    def is_in_networks(device):
        device_network_id = device["networkId"]
        for network in networks:
            if device_network_id == network["id"]:
                return True

        m = meta.copy()
        m["network_id"] = device_network_id
        logger.debug("Device skipped since it's not part of the filtered networks.", extra=m)
        return False

    if network_tag_regexes:
        devices = [d for d in devices if is_in_networks(d)]
        m = meta.copy()
        m["device_count"] = len(devices)
        logger.debug("Filtered devices.", extra=m)

    def add_device_status(device):
        device_serial = device["serial"]
        device_status = next((d for d in device_statuses if d["serial"] == device_serial), None)

        if device_status:
            device["status"] = device_status["status"]

        return device

    devices_with_status = [add_device_status(d) for d in devices]

    return devices_with_status

def update_devices(device):
    network_id = device["networkId"]
    device_serial = device["serial"]
    device_model = device["model"]

    device["tags"] = device["tags"].split() if device.get("tags") else None

    meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/uplink".format(network_id, device_serial)
    r = s.get(meraki_url, headers=meraki_headers)

    send_devices(r, device, network_id, device_serial, device_model)

@try_response
def send_devices(r, device, network_id, device_serial, device_model):
    meta = {
        "request_id": r.request_id,
        "network_id": network_id,
        "device_serial": device_serial,
    }
    uplinks = parse_meraki_response(r, list)

    for i, uplink in enumerate(uplinks):
        # Convert values like "Wan 1" to "wan1"
        uplinks[i]["interface"] = uplink["interface"].lower().replace(" ", "")

    device["organizationId"] = org_id
    device["uplinks"] = uplinks
    device["splunk_rest"] = {
        "session_id": sr.session_id,
        "request_id": r.request_id,
    }

    @try_response
    def get_device_perf(rr):
        device_perf = parse_meraki_response(rr, dict)
        return device_perf

    if device_model.startswith("MX"):
        meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/performance".format(network_id, device_serial)
        logger.debug("Getting device performance per MX device...", extra=meta)
        rr = s.get(meraki_url, headers=meraki_headers)
        device["splunk_rest"]["request_id_2"] = rr.request_id
        device_perf = get_device_perf(rr) or {"perfScore": "Failed to get."}
        device.update(device_perf)

    event = {
        "index": index,
        "sourcetype": "meraki_api_device",
        "source": __file__,
        "event": device,
    }

    data = json.dumps(event)

    logger.debug("Sending device data to Splunk...", extra=meta)
    s.post(hec_url, headers=hec_headers, data=data)

@splunk_rest
def meraki_loss_latency_history(org_id):
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

    send_loss_latency_history(r, org_id)

@try_response
def send_loss_latency_history(r, org_id):
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
            stat["organizationId"] = org_id
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

if __name__ == "__main__":
    sr.arg_parser.add_argument("--loss", action="store_true", help="only get the loss and latency history stats")
    sr.arg_parser.add_argument("--org", action="store_true", help="only list the organizations (needed for getting the all other data)")
    script_args = sr.get_script_args()

    logger = logging.getLogger("splunk_rest.splunk_rest")
    s = sr.retry_session()

    index = "main" if script_args.test else sr.config["meraki_api"]["index"]
    orgs = sr.config["meraki_api"]["orgs"]
    repeat = sr.config["meraki_api"]["repeat"]
    meraki_headers = sr.config["meraki_api"]["headers"]
    network_tag_regexes = sr.config["meraki_api"]["network_tag_regexes"]

    hec_url = sr.config["hec"]["url"]
    hec_headers = sr.config["hec"]["headers"]

    if script_args.org:
        meraki_org()
    else:
        for org_id in orgs:
            print("Working on org_id={}.".format(org_id))
            if script_args.loss:
                meraki_loss_latency_history(org_id)
            else:
                meraki_api(org_id)
