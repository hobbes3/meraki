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

def is_good_meraki_response(response, var_type):
    if response and "errors" not in response and isinstance(response, var_type):
        return True

    logger.warning("Bad response: {}".format(response))
    return False

def get_data(url, headers=None, params=None, give_up=None):
    return get_post_data(url, method="GET", headers=headers, params=params, give_up=give_up)

def post_data(url, headers=None, params=None, data=None, give_up=None):
    return get_post_data(url, method="POST", headers=headers, params=params, data=data, give_up=give_up)

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
            logger.info("Try #{}, total error #{}: {} {} - Success.".format(count_try+1, count_error.value, method, r.url))
            break

    if method=="GET":
        if r.text:
            return r.json()
        else:
            logger.warning("Empty response!")

def get_and_send_devices(network_id):
    meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices".format(network_id)

    devices = get_data(meraki_url, headers=meraki_headers, give_up=False)
    logger.debug("Found {} device(s) for network {}.".format(len(devices), network_id))

    data = ""

    if is_good_meraki_response(devices, list):
        for device in devices:
            device_serial = device["serial"]
            device_model = device["model"]

            device_list.append({
                "network_id": network_id,
                "device_serial": device_serial,
                "model": device_model,
            })

            device["tags"] = device["tags"].split() if device.get("tags") else None

            device_status = next((d for d in device_statuses if d["serial"] == device_serial), None)

            meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/uplink".format(network_id, device_serial)
            uplinks = get_data(meraki_url, headers=meraki_headers, give_up=False)

            if is_good_meraki_response(uplinks, list):
                for i, uplink in enumerate(uplinks):
                    # Convert values like "Wan 1" to "wan1"
                    uplinks[i]["interface"] = uplink["interface"].lower().replace(" ", "")

                device["uplinks"] = uplinks

            if device_status:
                device["status"] = device_status["status"]

            if device_model.startswith("MX"):
                meraki_url = "https://api.meraki.com/api/v0/networks/{}/devices/{}/performance".format(network_id, device_serial)
                device_perf = get_data(meraki_url, headers=meraki_headers, give_up=False)

                if is_good_meraki_response(device_perf, dict):
                    device.update(device_perf)

            event = {
                "index": "meraki_api",
                "sourcetype": "meraki_api_device",
                "source": "http:get_data.py",
                "event": device,
            }

            logger.debug(event)
            data += json.dumps(event)

    if data:
        logger.info("Sending device data to Splunk for network {}.".format(network_id))
        post_data(hec_url, headers=hec_headers, data=data, give_up=False)
    else:
        logger.warning("No device data to send to Splunk for network {}.".format(network_id))

def get_and_send_device_loss_and_latency(mx_device):
    network_id = mx_device["network_id"]
    device_serial = mx_device["device_serial"]

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

        stats = get_data(meraki_url, headers=meraki_headers, params=params, give_up=True)

        if is_good_meraki_response(stats, list):
            device = {
                "networkId": network_id,
                "deviceSerial": device_serial,
                "params": params,
                "stats": stats,
            }

            event = {
                "index": "meraki_api",
                "sourcetype": "meraki_api_device_loss_and_latency",
                "source": "http:get_data.py",
                "event": device,
            }

            logger.debug(event)
            data += json.dumps(event)

    if data:
        logger.info("Sending device loss and latency data to Splunk for network {} and device {}.".format(device_serial, network_id))
        post_data(hec_url, headers=hec_headers, data=data, give_up=False)
    else:
        logger.warning("No device loss and latency data to send to Splunk for network {} and device {}.".format(device_serial, network_id))

def get_and_send_clients(device):
    network_id = device["network_id"]
    device_serial = device["device_serial"]

    meraki_url = "https://api.meraki.com/api/v0/devices/{}/clients".format(device_serial)
    params = {
        "timespan": REPEAT
    }

    clients = get_data(meraki_url, headers=meraki_headers, params=params)
    logger.debug("Found {} client(s) for device {}.".format(len(clients), device_serial))

    data = ""

    if is_good_meraki_response(clients, list):
        for client in clients:
            client["networkId"] = network_id
            client["deviceSerial"] = device_serial
            client["params"] = params

            event = {
                "index": "meraki_api",
                "sourcetype": "meraki_api_client",
                "source": "http:get_data.py",
                "event": client
            }

            logger.debug(event)
            data += json.dumps(event)

    if data:
        logger.info("Sending client data to Splunk for device {}.".format(device_serial))
        post_data(hec_url, headers=hec_headers, data=data, give_up=False)
    else:
        logger.warning("No client data to send to Splunk for device {}.".format(device_serial))

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

    logger.info("===START OF SCRIPT===")

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

    pool = Pool(THREADS)

    try:
        print("Press ctrl-c to cancel at any time.")

        logger.info("Getting networks...")
        print("Getting networks...")
        meraki_url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(ORG_ID)
        networks = get_data(meraki_url, headers=meraki_headers, give_up=False)
        logger.debug("Found {} network(s).".format(len(networks)))
        print("Found {} network(s)!".format(len(networks)))

        data = ""
        network_list = []
        device_list = []

        if is_good_meraki_response(networks, list):
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

            logger.info("Sending network data to Splunk.")
            print("Sending network data to Splunk...")
            post_data(hec_url, headers=hec_headers, data=data, give_up=False)

        # Currently this is the only way to get uplink ip, which is needed for per-device /lossAndLatencyHistory later.
        logger.info("Getting device status(es)...")
        print("Getting device status(es)...")
        meraki_url = "https://api.meraki.com/api/v0/organizations/{}/deviceStatuses".format(ORG_ID)
        device_statuses = get_data(meraki_url, headers=meraki_headers, give_up=False)
        logger.debug("Found {} device status(es).".format(len(device_statuses)))
        print("Found {} device status(es)!".format(len(device_statuses)))

        # DEBUG
        #network_list = network_list[:25]

        logger.info("Getting and sending device data per network...")
        print("Getting and sending device data per network...")
        for _ in tqdm(pool.imap_unordered(get_and_send_devices, network_list), total=len(network_list)):
            pass

        mx_device_list = [d for d in device_list if d["model"].startswith("MX")]

        # DEBUG
        #mx_device_list = mx_device_list[:25]

        logger.info("Getting and sending device loss and latency data per device...")
        print("Getting and sending device loss and latency data per device...")
        for _ in tqdm(pool.imap_unordered(get_and_send_device_loss_and_latency, mx_device_list), total=len(mx_device_list)):
            pass

        # DEBUG
        #device_list = device_list[:50]

        logger.info("Getting and sending client data per device...")
        print("Getting and sending client data per device...")
        for _ in tqdm(pool.imap_unordered(get_and_send_clients, device_list), total=len(device_list)):
            pass

        pool.close()
        pool.join()
    except KeyboardInterrupt:
        print("\nCaught KeyboardInterrupt! Cleaning up and terminating workers. Please wait...")
        logger.warning("Caught KeyboardInterrupt!")
        pool.terminate()
        pool.join()
        gracefully_exit()
    except:
        logger.fatal("{}. Script exiting!".format(sys.exc_info()[0]))
        pool.terminate()
        pool.join()
        gracefully_exit()

    logger.info("DONE. Total elapsed seconds: {}.".format(time.time() - start_time))
    print("Done!")
