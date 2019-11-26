#!/usr/bin/env python
# hobbes3

import logging
import json
import splunk_rest.splunk_rest as sr
from splunk_rest.splunk_rest import rest_wrapped

def set_syslog_server(network):
    network_name = network["name"]
    network_id = network["id"]

    url = "https://api.meraki.com/api/v0/networks/{}/syslogServers".format(network_id)

    r = s.get(url, headers=headers)

    if r and r.text:
        servers = r.json()

        logger.debug("Current server list.", extra={"server_count": len(servers), "servers": servers})

        updated_servers = []
        for server in servers:
            if server["host"] == add_host and server["port"] == str(add_port):
                logger.debug("Found the same server and port as add_host and add_port. Skipping.", extra={"add_host": add_host, "add_port": add_port})
            elif server["host"] == add_host:
                logger.debug("Found the same server as add_host, but different port than add_port. Updating port.", extra={"add_host": add_host, "add_port": add_port})
            elif server["host"] == remove_host:
                logger.debug("Found the same server as remove_host. Removing.", extra={"remove_host": remove_host})
            else:
                logger.debug("Found a different server than add_host or remote_host. Keeping.", extra={"server_host": server["host"], "server_port": server["port"]})
                updated_servers.append(server)

            if add_host and add_port:
                updated_servers.append({
                    "host": add_host,
                    "port": add_port,
                    "roles": syslog_roles,
                })

            logger.debug("New server list.", extra={"servers": servers})

            data = {"servers": updated_servers}

            s.put(url, headers=headers, json=data)

@rest_wrapped
def meraki_set_syslog():
    logger.info("Getting networks...", extra={"org_id": org_id})
    url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(org_id)

    r = s.get(url, headers=headers)

    if r and r.text:
        networks = r.json()

        logger.info("Found networks.", extra={"network_count": len(networks)})

        if debug:
            networks = networks[:10]

        logger.info("Adding and removing syslog server for each network...", extra={"add_host": add_host, "add_port": add_port, "remove_host": remove_host})
        sr.multiprocess(set_syslog_server, networks)

if __name__ == "__main__":
    logger = logging.getLogger("splunk_rest.splunk_rest")
    s = sr.retry_session()

    debug = sr.config["general"]["debug"]

    headers = sr.config["meraki_api"]["headers"]
    org_id = sr.config["meraki_api"]["org_id"]
    add_host = sr.config["meraki_api"]["syslog"]["add_host"]
    add_port = sr.config["meraki_api"]["syslog"]["add_port"]
    remove_host = sr.config["meraki_api"]["syslog"]["remove_host"]

    hec_url = sr.config["hec"]["url"]
    hec_headers = sr.config["hec"]["headers"]

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

    meraki_set_syslog()
