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
from tqdm import tqdm
from multiprocessing.dummy import Pool
from multiprocessing import RawValue, Lock
from pathlib import Path

from settings import *

def gracefully_exit():
    with lock:
        logger.info("INCOMPLETE. Total elapsed seconds: {}.".format(time.time() - start_time))
        os._exit(1)

def is_good_meraki_response(response, var_type):
    if response and "errors" not in response and isinstance(response, var_type):
        return True

    logger.warning("Bad response: {}".format(response))
    return False

def set_syslog_server(network):
    sleep = uniform(1, 5)
    logger.debug("Sleeping for {} second(s).".format(sleep))
    # Sleep first, otherwise too many requests may kick off initially.
    time.sleep(sleep)

    network_name = network["name"]
    network_id = network["id"]

    url = "https://api.meraki.com/api/v0/networks/{}/syslogServers".format(network_id)

    r = requests.get(url, headers=headers)
    servers = r.json()

    if is_good_meraki_response(servers, list):
        logger.info("{} ({})".format(network_name, network_id))
        logger.debug("Existing servers: {}".format(servers))

        updated_servers = []
        for server in servers:
            if server["host"] == SYSLOG_HOST and server["port"] == str(SYSLOG_PORT):
                logger.info("Found same {}:{} syslog server as SYSLOG_HOST:SYSLOG_PORT. Skipping.".format(SYSLOG_HOST, SYSLOG_PORT))
            elif server["host"] == SYSLOG_HOST:
                logger.info("Found same {}:{} syslog server, but different port as SYSLOG_HOST:SYSLOG_PORT. Updating port to {}.".format(SYSLOG_HOST, SYSLOG_PORT, SYSLOG_PORT))
            elif server["host"] == REMOVE_SYSLOG_HOST:
                logger.info("Found same {} syslog server as REMOVE_SYSLOG_HOST. Removing.".format(REMOVE_SYSLOG_HOST))
            else:
                logger.info("Found {} syslog server. Keeping.".format(server["host"]))
                updated_servers.append(server)

        updated_servers.append({
            "host": SYSLOG_HOST,
            "port": SYSLOG_PORT,
            "roles": syslog_roles,
        })

        logger.debug("Updated servers: {}".format(updated_servers))

        data = {"servers": updated_servers}

        r = requests.put(url, headers=headers, json=data)
        status_code = r.status_code

        if status_code == 200:
            logger.info("Status code {}: Added {}:{} syslog with {}.".format(status_code, SYSLOG_HOST, SYSLOG_PORT, syslog_roles))
        else:
            logger.error("Status code {}: Not added {}:{} syslog with {}.".format(status_code, SYSLOG_HOST, SYSLOG_PORT, syslog_roles))

if __name__ == "__main__":
    start_time = time.time()

    setting_file = Path(os.path.dirname(os.path.realpath(__file__)) + "/settings.py")

    if not os.path.exists(setting_file):
        sys.exit("The config file, settings.py, doesn't exist! Please copy, edit, and rename default_settings.py to settings.py.")

    logger = logging.getLogger("logger")
    logger.setLevel(logging.DEBUG)
    handler = logging.handlers.RotatingFileHandler(SET_SYSLOG_SERVERS_LOG_PATH, maxBytes=LOG_ROTATION_BYTES, backupCount=LOG_ROTATION_LIMIT)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-7s] (%(threadName)-10s) %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)

    print("Log file at {}.".format(SET_SYSLOG_SERVERS_LOG_PATH))

    logger.info("===START OF SCRIPT===")

    lock = Lock()

    headers = {
        "X-Cisco-Meraki-API-Key": API_KEY,
        "Content-Type": "application/json",
    }

    pool = Pool(THREADS)

    try:
        print("Press ctrl-c to cancel at any time.")

        logger.info("Getting networks...")
        print("Getting networks...")
        url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(ORG_ID)

        r = requests.get(url, headers=headers)
        networks = r.json()

        logger.debug("Found {} network(s).".format(len(networks)))
        print("Found {} network(s)!".format(len(networks)))

        # DEBUG
        #networks = networks[:10]

        syslog_roles = [
            "Flows",
            "URLs",
            "Security events",
            "Appliance event log",
            #"Wireless event log",
            #"Air Marshal events",
            #"Switch event log",
            #"IDS alerts",
        ]

        logger.info("Setting {}:{} syslog server for each network...".format(SYSLOG_HOST, SYSLOG_PORT))
        print("Setting {}:{} syslog server for each network...".format(SYSLOG_HOST, SYSLOG_PORT))
        for _ in tqdm(pool.imap_unordered(set_syslog_server, networks), total=len(networks)):
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
        logger.exception("An exepection occured!")
        traceback.print_exc()
        pool.terminate()
        pool.join()
        gracefully_exit()

    logger.info("DONE. Total elapsed seconds: {}.".format(time.time() - start_time))
    print("Done!")
