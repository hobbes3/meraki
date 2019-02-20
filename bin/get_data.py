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
from multiprocessing import RawValue, Lock
from pathlib import Path

from settings import *

# https://stackoverflow.com/a/47562583/1150923
class Counter(object):
    def __init__(self, initval=0):
        self.val = RawValue('i', initval)
        self.lock = Lock()

    def increment(self):
        with self.lock:
            self.val.value += 1

    @property
    def value(self):
        return self.val.value

def gracefully_exit():
    with lock:
        logger.info("INCOMPLETE. Total elapsed seconds: {}.".format(time.time() - start_time))
        os._exit(1)

def get_post_data(url, method="GET", headers=None, params=None, data=None, give_up=True):
    count_try = 0

    sleep = uniform(1, 5)
    logger.debug("Sleeping for {} second(s).".format(sleep))
    # Sleep first, otherwise too many requests may kick off initially.
    time.sleep(sleep)

    while True:
        if count_error.value > TOTAL_ERROR_LIMIT:
            logger.error("Over {} total errors. Script exiting!".format(count_total_errors))
            gracefully_exit()

        try:
            if method == "GET":
                r = requests.get(url, headers=headers, params=params, verify=False, timeout=TIMEOUT)
            else:
                r = requests.post(url, headers=headers, params=params, data=data, verify=False, timeout=TIMEOUT)

            r.raise_for_status()
        except (requests.exceptions.HTTPError, requests.exceptions.Timeout, requests.exceptions.TooManyRedirects, requests.exceptions.RequestException) as e:
            if str(r.status_code)[:1] == "4":
                logger.error("Try #{}, total error #{}: {} {} - {}. Skipping!".format(count_try+1, count_error.value, method, url, str(e)))
                break

            if give_up and count_try > len(TRY_SLEEP)-1:
                logger.error("Try #{}, total error #{}: {} {} - {}. Giving up!".format(count_try+1, count_error.value, method, url, str(e)))
                break

            count_error.increment()
            try_sleep = TRY_SLEEP[min(count_try, len(TRY_SLEEP)-1)]
            logger.error("Try #{}, total error #{}: {} {} - {}. Sleeping for {} second(s).".format(count_try+1, count_error.value, method, url, str(e), try_sleep))
            time.sleep(try_sleep)
            count_try += 1
            pass
        except:
            logger.fatal("{}. Script exiting!".format(sys.exc_info()[0]))
            gracefully_exit()
        else:
            logger.info("Try #{}, total error #{}: {} {} - Success.".format(count_try+1, count_error.value, method, url))
            break

    if method=="GET":
        return r.json()

def get_and_send_devices(network_id):
    meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices".format(network_id)

    logger.info("Getting devices via {}.".format(meraki_url))
    devices = get_post_data(meraki_url, headers=meraki_headers, give_up=False)
    logger.debug("Found {} device(s) for network {}.".format(len(devices), network_id))

    data = ""

    if isinstance(devices, list):
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
        logger.info("Sending device data to Splunk for network {}.".format(hec_url, network_id))
        get_post_data(hec_url, method="POST", headers=hec_headers, data=data, give_up=False)

def get_and_send_clients(device):
    network_id = device["network_id"]
    device_serial = device["device_serial"]

    meraki_url = "https://api.meraki.com/api/v0/devices/{}/clients".format(device_serial)
    params = {
        "timespan": 3600
    }

    logger.info("Getting clients via {}.".format(meraki_url))
    clients = get_post_data(meraki_url, headers=meraki_headers, params=params)
    logger.debug("Found {} client(s) for device {}.".format(len(clients), device_serial))

    data = ""

    if isinstance(clients, list):
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
        logger.info("Sending client data to Splunk for device {}.".format(hec_url, device_serial))
        get_post_data(hec_url, method="POST", headers=hec_headers, data=data, give_up=False)

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
    count_error = Counter(0)
    lock = Lock()

    meraki_headers = {
        "X-Cisco-Meraki-API-Key": API_KEY,
        "Content-Type": "application/json",
    }

    hec_headers = {
        "Authorization": "Splunk " + HEC_TOKEN
    }

    hec_url = HEC_URL + "/services/collector"

    meraki_url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(ORG_ID)

    pool = Pool(THREADS)

    try:
        print("Press ctrl-c to cancel at any time.")

        logger.info("Getting networks via {}.".format(meraki_url))
        print("Getting networks...")
        networks = get_post_data(meraki_url, headers=meraki_headers, give_up=False)
        logger.debug("Found {} network(s).".format(len(networks)))
        print("Found {} network(s)!".format(len(networks)))

        data = ""
        network_list = []
        device_list = []

        if isinstance(networks, list):
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
            print("Sending network data to Splunk...")
            get_post_data(hec_url, method="POST", headers=hec_headers, data=data, give_up=False)

        # DEBUG
        #network_list = network_list[:50]

        print("Getting and sending devices per network...")
        for _ in tqdm(pool.imap_unordered(get_and_send_devices, network_list), total=len(network_list)):
            pass

        # DEBUG
        #device_list = device_list[:5]

        print("Getting and sending clients per device...")
        for _ in tqdm(pool.imap_unordered(get_and_send_clients, device_list), total=len(device_list)):
            pass

        pool.close()
        pool.join()
    except KeyboardInterrupt:
        print("\nCaught KeyboardInterrupt! Terminating workers. Please wait...")
        logger.warning("Caught KeyboardInterrupt!")
        pool.terminate()
        pool.join()
        gracefully_exit()

    logger.info("DONE. Total elapsed seconds: {}.".format(time.time() - start_time))
    print("Done!")
