#!/usr/bin/env python
# hobbes3

import requests
import os
import sys
import time
import json
import logging
import logging.handlers
from random import uniform
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from tqdm import tqdm
from multiprocessing.dummy import Pool
from pathlib import Path

from settings import *

if __name__ == "__main__":
    start_time = time.time()

    setting_file = Path(os.path.dirname(os.path.realpath(__file__)) + "/settings.py")

    if not os.path.exists(setting_file):
        sys.exit("The config file, settings.py, doesn't exist! Please copy, edit, and rename default_settings.py to settings.py.")

    logger = logging.getLogger("logger")
    logger.setLevel(logging.DEBUG)
    handler = logging.handlers.RotatingFileHandler(GET_DATA_LOG_PATH, maxBytes=LOG_ROTATION_BYTES, backupCount=LOG_ROTATION_LIMIT)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-7s] (%(threadName)-10s) %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)

    print("Log file at {}.".format(GET_DATA_LOG_PATH))

    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    meraki_headers = {
        "X-Cisco-Meraki-API-Key": API_KEY,
        "Content-Type": "application/json",
    }

    hec_headers = {
        "Authorization": "Splunk " + HEC_TOKEN
    }

    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(ORG_ID)
    logger.info("Getting networks via {}.".format(meraki_url))

    r = requests.get(meraki_url, headers=meraki_headers, timeout=TIMEOUT)
    r.raise_for_status()
    networks = r.json()

    hec_url = HEC_URL + "/services/collector"

    data = ""
    network_list = []
    device_list = []

    logger.debug("Found {} network(s).".format(len(networks)))
    # Sending data to Splunk via HEC in batch mode.
    for network in networks:
        # Improved network tags so they become multivalues in Splunk instead of a space-delimited string
        network["tags"] = network["tags"].split() if network.get("tags") else None

        network_list.append(network["id"])

        event = {
            "index": "meraki_api",
            "sourcetype": "meraki_api_network",
            "source": "http:get_data.py",
            "event": network
        }

        logger.debug(event)

        data += json.dumps(event)

    logger.info("Sending network data to Splunk via {}.".format(hec_url))
    r = requests.post(hec_url, headers=hec_headers, data=data, verify=False, timeout=TIMEOUT)
    r.raise_for_status()

    def get_devices(network_id):
        sleep = uniform(1, 5)
        logger.debug("Sleeping for {} seconds.".format(sleep))
        time.sleep(sleep)

        meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices".format(network_id)

        logger.info("Getting devices via {}.".format(meraki_url))
        r = requests.get(meraki_url, headers=meraki_headers, timeout=TIMEOUT)
        r.raise_for_status()
        devices = r.json()

        data = ""

        logger.debug("Found {} device(s) for network {}.".format(len(devices), network_id))
        for device in devices:
            device["tags"] = device["tags"].split() if device.get("tags") else None

            device_list.append({
                "network_id": network_id,
                "device_serial": device["serial"]
            })

            event = {
                "index": "meraki_api",
                "sourcetype": "meraki_api_device",
                "source": "http:get_data.py",
                "event": device
            }
            logger.debug(event)

            data += json.dumps(event)

        if data:
            logger.info("Sending device data to Splunk via {} for network {}.".format(hec_url, network_id))
            r = requests.post(hec_url, headers=hec_headers, data=data, verify=False, timeout=TIMEOUT)
            r.raise_for_status()

    def get_clients(device):
        sleep = uniform(1, 5)
        logger.debug("Sleeping for {} seconds.".format(sleep))
        time.sleep(sleep)

        network_id = device["network_id"]
        device_serial = device["device_serial"]

        meraki_url = "https://api.meraki.com/api/v0/devices/{}/clients".format(device_serial)

        params = {
            "timespan": 3600
        }

        logger.info("Getting clients via {}.".format(meraki_url))
        r = requests.get(meraki_url, headers=meraki_headers, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        clients = r.json()

        data = ""

        logger.debug("Found {} client(s) for device {}.".format(len(clients), device_serial))
        for client in clients:
            client["networkId"] = network_id
            client["deviceSerial"] = device_serial

            event = {
                "index": "meraki_api",
                "sourcetype": "meraki_api_client",
                "source": "http:get_data.py",
                "event": client
            }
            logger.debug(event)

            data += json.dumps(event)

        if data:
            logger.info("Sending client data to Splunk via {} for device {}.".format(hec_url, device_serial))
            r = requests.post(hec_url, headers=hec_headers, data=data, verify=False, timeout=TIMEOUT)
            r.raise_for_status()

    pool = Pool(THREADS)

    # DEBUG
    #network_list = network_list[:100]

    print("Getting and sending devices per network.")

    for _ in tqdm(pool.imap_unordered(get_devices, network_list), total=len(network_list)):
        pass

    pool.close()
    pool.join()

    pool = Pool(THREADS)

    # DEBUG
    #device_list = device_list[:100]

    print("Getting and sending clients per device.")

    for _ in tqdm(pool.imap_unordered(get_clients, device_list), total=len(device_list)):
        pass

    pool.close()
    pool.join()
