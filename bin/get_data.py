#!/usr/bin/env python
# hobbes3

import requests
import os
import sys
import time
import json
import traceback
import logging
import logging.handlers
from random import uniform
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from requests.packages.urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from urllib.parse import urlencode
from tqdm import tqdm
from multiprocessing.dummy import Pool
from multiprocessing import Lock
from pathlib import Path

from settings import *

def is_good_meraki_response(r_json, var_type):
    if "errors" not in r_json and isinstance(r_json, var_type):
        return True

    logger.warning("Bad response: {}".format(r_json))
    return False

def get(session, url, params=None, headers=None, auth=None, data=None):
    return get_post("GET", session, url, params=params, headers=headers, auth=auth, data=data)

def post(session, url, params=None, headers=None, auth=None, data=None):
    return get_post("POST", session, url, params=params, headers=headers, auth=auth, data=data)

def get_post(method, session, url, params=None, headers=None, auth=None, data=None):
    TRUNCATE = 300

    t0 = time.time()
    r = None

    full_url = url + "?" + urlencode(params) if params else url

    try:
        logger.info("Trying to {} {}...".format(method, full_url))
        if data:
            msg = "POSTing {} bytes of data".format(len(data))
            if len(data) <= TRUNCATE:
                msg += ": {}".format(data)
                logger.info(msg)
            else:
                msg += " (truncated): {}...".format(data[:TRUNCATE])
                logger.info(msg)

        if method == "GET":
            r = session.get(url, params=params, headers=headers, auth=auth, data=data)
        elif method == "POST":
            r = session.post(url, params=params, headers=headers, auth=auth, data=data)
        else:
            logger.error("Unknown method {}!".format(method))
            return r
    except Exception:
        traceback.print_exc()
        logger.exception("An exception occured!")
    else:
        if r.status_code == 200:
            logger.info("{} {} eventually worked with status code 200.")
        else:
            logger.warning("{} {} eventually worked but with status code {}!".format(method, full_url, r.status_code))

        if r.text:
            if len(r.text) <= TRUNCATE:
                logger.debug("Response: {}".format(r.text))
            else:
                logger.debug("Response (truncated): {}...".format(r.text[:TRUNCATE]))
        else:
            logger.warning("Empty response!")

    finally:
        logger.info("Took {} seconds.".format(time.time() - t0))
        return r

def log_and_print(msg, level="info"):
    if level == "debug":
        logger.debug(msg)
    elif level == "warning":
        logger.warning(msg)
    else:
        logger.info(msg)

    print(msg)

def gracefully_exit():
    with lock:
        logger.info("INCOMPLETE. Total elapsed seconds: {}.".format(time.time() - start_time))
        os._exit(1)

def update_and_send_devices(device):
    network_id = device["networkId"]
    device_serial = device["serial"]
    device_model = device["model"]

    device["tags"] = device["tags"].split() if device.get("tags") else None

    device_status = next((d for d in device_statuses if d["serial"] == device_serial), None)

    meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/uplink".format(network_id, device_serial)
    r = get(s, meraki_url, headers=meraki_headers)

    if r.text:
        uplinks = r.json()

        if is_good_meraki_response(uplinks, list):
            for i, uplink in enumerate(uplinks):
                # Convert values like "Wan 1" to "wan1"
                uplinks[i]["interface"] = uplink["interface"].lower().replace(" ", "")

            device["uplinks"] = uplinks

        if device_status:
            device["status"] = device_status["status"]

        if device_model.startswith("MX"):
            meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/performance".format(network_id, device_serial)
            rr = get(s, meraki_url, headers=meraki_headers)

            if rr.text:
                device_perf = rr.json()

                if is_good_meraki_response(device_perf, dict):
                    device.update(device_perf)

        event = {
            "index": INDEX,
            "sourcetype": "meraki_api_device",
            "source": script_path,
            "event": device,
        }

        data = json.dumps(event)

        logger.info("Sending device {} data to Splunk for network {}.".format(device_serial, network_id))
        post(s, HTTP_URL, headers=HTTP_HEADERS, data=data)

def get_and_send_device_loss_and_latency(mx_device):
    network_id = mx_device["networkId"]
    device_serial = mx_device["serial"]

    meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/lossAndLatencyHistory".format(network_id, device_serial)
    # Snap time to the current hour.
    t1 = int(start_time - start_time % REPEAT)
    t0 = t1 - REPEAT
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

        r = get(s, meraki_url, headers=meraki_headers, params=params)

        if r.text:
            stats = r.json()

            if is_good_meraki_response(stats, list):
                device = {
                    "networkId": network_id,
                    "deviceSerial": device_serial,
                    "params": params,
                    "stats": stats,
                }

                event = {
                    "index": INDEX,
                    "sourcetype": "meraki_api_device_loss_and_latency",
                    "source": script_path,
                    "event": device,
                }

                data += json.dumps(event)

    if data:
        logger.info("Sending device loss and latency data to Splunk for network {} and device {}.".format(device_serial, network_id))
        post(s, HTTP_URL, headers=HTTP_HEADERS, data=data)
    else:
        logger.warning("No device loss and latency data to send to Splunk for network {} and device {}.".format(device_serial, network_id))

def get_and_send_clients(device):
    network_id = device["networkId"]
    device_serial = device["serial"]

    meraki_url = "https://api.meraki.com/api/v0/devices/{}/clients".format(device_serial)
    params = {
        "timespan": REPEAT
    }

    r = get(s, meraki_url, headers=meraki_headers, params=params)

    if r.text:
        clients = r.json()
        logger.debug("Found {} client(s) for device {}.".format(len(clients), device_serial))

        data = ""

        if is_good_meraki_response(clients, list):
            for client in clients:
                client["networkId"] = network_id
                client["deviceSerial"] = device_serial
                client["params"] = params

                event = {
                    "index": INDEX,
                    "sourcetype": "meraki_api_client",
                    "source": script_path,
                    "event": client,
                }

                data += json.dumps(event)

        if data:
            logger.info("Sending client data to Splunk for device {}.".format(device_serial))
            post(s, HTTP_URL, headers=HTTP_HEADERS, data=data)
        else:
            logger.warning("No client data to send to Splunk for device {}.".format(device_serial))

if __name__ == "__main__":
    start_time = time.time()

    setting_file = Path(os.path.dirname(os.path.realpath(__file__)) + "/settings.py")
    script_path = os.path.realpath(__file__)

    if not os.path.exists(setting_file):
        sys.exit("The config file, settings.py, doesn't exist! Please copy, edit, and rename default_settings.py to settings.py.")

    logger = logging.getLogger("logger")
    logger.setLevel(logging.DEBUG)
    handler = logging.handlers.RotatingFileHandler(GET_DATA_LOG_PATH, maxBytes=LOG_ROTATION_BYTES, backupCount=LOG_ROTATION_LIMIT)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-7s] (%(threadName)-10s) %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)

    print("Log file at {}.".format(GET_DATA_LOG_PATH))

    logger.info("===START OF SCRIPT===")

    lock = Lock()

    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    s = requests.Session()
    s.verify = False
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504], method_whitelist=frozenset(['GET', 'POST']))
    adapter = HTTPAdapter(max_retries=retries)
    s.mount('http://', adapter)
    s.mount('https://', adapter)

    meraki_headers = {
        "X-Cisco-Meraki-API-Key": API_KEY,
        "Content-Type": "application/json",
    }

    pool = Pool(THREADS)

    try:
        print("Press ctrl-c to cancel at any time.")

        log_and_print("Getting networks...")
        meraki_url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(ORG_ID)
        r = get(s, meraki_url, headers=meraki_headers)

        if r.text:
            networks = r.json()
            log_and_print("Found {} networks.".format(len(networks)), level="debug")

            data = ""

            if is_good_meraki_response(networks, list):
                # Sending data to Splunk via HEC in batch mode.
                for network in networks:
                    # Improved network tags so they become multivalues in Splunk instead of a space-delimited string
                    network["tags"] = network["tags"].split() if network.get("tags") else None

                    event = {
                        "index": INDEX,
                        "sourcetype": "meraki_api_network",
                        "source": script_path,
                        "event": network,
                    }

                    data += json.dumps(event)

                log_and_print("Sending network data to Splunk...")
                post(s, HTTP_URL, headers=HTTP_HEADERS, data=data)

        # Currently this is the only way to get uplink ip, which is needed for per-device /lossAndLatencyHistory later.
        log_and_print("Getting device statuses...")
        device_statuses = []
        meraki_url = "https://api.meraki.com/api/v0/organizations/{}/deviceStatuses".format(ORG_ID)
        r = get(s, meraki_url, headers=meraki_headers)
        if r.text:
            device_statuses = r.json()
            log_and_print("Found {} device statuses.".format(len(device_statuses)), level="debug")

        # DEBUG
        #networks = networks[:20]

        log_and_print("Getting devices...")
        devices = []
        meraki_url = "https://api.meraki.com/api/v0/organizations/{}/devices".format(ORG_ID)
        r = get(s, meraki_url, headers=meraki_headers)
        if r.text:
            devices = r.json()
            log_and_print("Found {} devices.".format(len(devices)), level="debug")

        log_and_print("Updating and sending devices data...")
        for _ in tqdm(pool.imap_unordered(update_and_send_devices, devices), total=len(devices)):
            pass

        mx_devices = [d for d in devices if d["model"].startswith("MX")]

        # DEBUG
        #mx_devices = mx_devices[:20]

        log_and_print("Getting and sending device loss and latency data per device...")
        for _ in tqdm(pool.imap_unordered(get_and_send_device_loss_and_latency, mx_devices), total=len(mx_devices)):
            pass

        # DEBUG
        #devices = devices[:20]

        log_and_print("Getting and sending client data per device...")
        for _ in tqdm(pool.imap_unordered(get_and_send_clients, devices), total=len(devices)):
            pass

        pool.close()
        pool.join()
    except KeyboardInterrupt:
        print("\nCaught KeyboardInterrupt! Cleaning up and terminating workers. Please wait...")
        logger.warning("Caught KeyboardInterrupt!")
        pool.terminate()
        pool.join()
        gracefully_exit()
    except Exception:
        logger.exception("An exception occured!")
        traceback.print_exc()
        pool.terminate()
        pool.join()
        gracefully_exit()

    log_and_print("DONE. Total elapsed seconds: {}.".format(time.time() - start_time))
