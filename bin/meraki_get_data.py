#!/usr/bin/env python
# hobbes3

import logging
import json
import splunk_rest.splunk_rest as sr
from splunk_rest.splunk_rest import rest_wrapped

@rest_wrapped
def meraki_api():
    def is_good_meraki_response(r, var_type):
        meta = {
            "request_id": r.request_id,
            "var_type": str(var_type),
        }

        try:
            r_json = r.json()
            if "errors" not in r_json and isinstance(r_json, var_type):
                return True
            else:
                m = meta.copy()
                m["json"] = r_json
                logger.warning("Bad response!", extra=m)
        except json.decoder.JSONDecodeError:
            m = meta.copy()
            m["text"] = r.text
            logger.warning("Not a valid json response!", extra=m)
        except:
            logger.exception("", exta=meta)
        else:
            return False

    def update_and_send_devices(device):
        network_id = device["networkId"]
        device_serial = device["serial"]
        device_model = device["model"]

        device["tags"] = device["tags"].split() if device.get("tags") else None

        device_status = next((d for d in device_statuses if d["serial"] == device_serial), None)

        meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/uplink".format(network_id, device_serial)
        r = s.get(meraki_url, headers=meraki_headers)

        try:
            if not is_good_meraki_response(r, list):
                return

            uplinks = r.json()

            for i, uplink in enumerate(uplinks):
                # Convert values like "Wan 1" to "wan1"
                uplinks[i]["interface"] = uplink["interface"].lower().replace(" ", "")

            device["uplinks"] = uplinks

            if device_status:
                device["status"] = device_status["status"]

            if device_model.startswith("MX"):
                meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/performance".format(network_id, device_serial)
                rr = s.get(meraki_url, headers=meraki_headers)

                try:
                    if not is_good_meraki_response(rr, dict):
                        return

                    device_perf = rr.json()
                    device.update(device_perf)
                except:
                    logger.exception("", extra={"request_id": rr.request_id})

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

            logger.debug("Sending device data to Splunk...", extra={"device_serial": device_serial, "network_id": network_id})
            s.post(hec_url, headers=hec_headers, data=data)
        except:
            logger.exception("", extra={"request_id": r.request_id})

    def get_and_send_device_loss_and_latency(mx_device):
        network_id = mx_device["networkId"]
        device_serial = mx_device["serial"]

        meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/lossAndLatencyHistory".format(network_id, device_serial)
        # Snap time to the current hour.
        t1 = int(sr.start_time - sr.start_time % repeat)
        t0 = t1 - repeat
        resolution = 60
        params = {
            "t0": t0,
            "t1": t1,
            "resolution": resolution,
            # Hardcoded
            "ip": "8.8.8.8",
        }

        data = ""

        for uplink in ["wan1", "wan2"]:
            params["uplink"] = uplink

            r = s.get(meraki_url, headers=meraki_headers, params=params)

            try:
                if not is_good_meraki_response(r, list):
                    return

                stats = r.json()
                device = {
                    "splunk_rest": {
                        "session_id": sr.session_id,
                        "request_id": r.request_id,
                    },
                    "networkId": network_id,
                    "deviceSerial": device_serial,
                    "stats": stats,
                }

                event = {
                    "index": index,
                    "sourcetype": "meraki_api_device_loss_and_latency",
                    "source": __file__,
                    "event": device,
                }

                data += json.dumps(event)
            except:
                logger.exception("", extra={"request_id": r.request_id})

        if data:
            logger.debug("Sending device loss and latency data to Splunk...", extra={"device_serial": device_serial, "network_id": network_id})
            s.post(hec_url, headers=hec_headers, data=data)
        else:
            logger.debug("No device loss and latency data to send to Splunk.", extra={"device_serial": device_serial, "network_id": network_id})

    def get_and_send_clients(device):
        network_id = device["networkId"]
        device_serial = device["serial"]

        meraki_url = "https://api.meraki.com/api/v0/devices/{}/clients".format(device_serial)
        params = {
            "timespan": sr.config["meraki_api"]["repeat"]
        }

        r = s.get(meraki_url, headers=meraki_headers, params=params)

        try:
            if not is_good_meraki_response(r, list):
                return

            clients = r.json()
            logger.debug("Found clients.", extra={"client_count": len(clients), "device_serial": device_serial})

            data = ""

            for client in clients:
                client["splunk_rest"] = {
                    "session_id": sr.session_id,
                    "request_id": r.request_id,
                }
                client["networkId"] = network_id
                client["deviceSerial"] = device_serial

                event = {
                    "index": index,
                    "sourcetype": "meraki_api_client",
                    "source": __file__,
                    "event": client,
                }

                data += json.dumps(event)

            if data:
                logger.debug("Sending client data to Splunk...", extra={"device_serial": device_serial})
                s.post(hec_url, headers=hec_headers, data=data)
            else:
                logger.debug("No client data to send to Splunk.", extra={"device_serial": device_serial})
        except:
            logger.exception("", extra={"request_id": r.request_id})

    # Start of meraki_api()
    logger.info("Getting networks...")
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)
    try:
        if not is_good_meraki_response(r, list):
            return

        networks = r.json()
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
        logger.exception("", extra={"request_id": r.request_id})

    # Currently this is the only way to get uplink ip, which is needed for per-device /lossAndLatencyHistory later.
    logger.info("Getting device statuses...")
    device_statuses = []
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/deviceStatuses".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)
    try:
        if not is_good_meraki_response(r, list):
            return

        device_statuses = r.json()
        logger.info("Found device statuses.", extra={"device_status_count": len(device_statuses)})
    except:
        logger.exception("", extra={"request_id": r.request_id})

    if debug:
        networks = networks[:20]

    logger.info("Getting devices...")
    devices = []
    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/devices".format(org_id)
    r = s.get(meraki_url, headers=meraki_headers)
    try:
        if not is_good_meraki_response(r, list):
            return

        devices = r.json()
        logger.info("Found devices.", extra={"device_count": len(devices)})
    except:
        logger.exception("", extra={"request_id": r.request_id})

    logger.info("Updating and sending devices data...")
    sr.multiprocess(update_and_send_devices, devices)

    mx_devices = [d for d in devices if d["model"].startswith("MX")]

    if debug:
        mx_devices = mx_devices[:20]

    logger.info("Getting and sending device loss and latency data per device...")
    sr.multiprocess(get_and_send_device_loss_and_latency, mx_devices)

    if debug:
        devices = devices[:20]

    logger.info("Getting and sending client data per device...")
    sr.multiprocess(get_and_send_clients, devices)

if __name__ == "__main__":
    logger = logging.getLogger("splunk_rest.splunk_rest")
    s = sr.retry_session()

    debug = sr.config["general"]["debug"]

    meraki_headers = sr.config["meraki_api"]["headers"]
    org_id = sr.config["meraki_api"]["org_id"]
    index = sr.config["meraki_api"]["index"]
    repeat = sr.config["meraki_api"]["repeat"]

    hec_url = sr.config["hec"]["url"]
    hec_headers = sr.config["hec"]["headers"]

    meraki_api()
