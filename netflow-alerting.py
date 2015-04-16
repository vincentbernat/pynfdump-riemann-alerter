#!/usr/bin/python
# -*- coding: utf-8 -*-
# Loic Lambiel ©
# License MIT

import sys
import datetime
import socket
import argparse
import shelve
import logging
import logging.handlers
from datetime import timedelta

try:
    import yaml
except ImportError:
    print "It looks like yaml module isn't installed. Please install it using pip install pyyaml"
    sys.exit(1)

try:
    from netaddr import all_matching_cidrs
except ImportError:
    print "It looks like netaddr module isn't installed. Please install it using pip install netaddr"
    sys.exit(1)

try:
    import pynfdump
except ImportError:
    print "It looks like pynfdump  module isn't installed. Please install it using pip install pynfdump"
    sys.exit(1)

try:
    import bernhard
except ImportError:
    print "It looks like riemann client (bernard) isn't installed. Please install it using pip install bernhard"
    sys.exit(1)
try:
    import GeoIP
except ImportError:
    print "It looks like GeoIP module isn't installed. Please install it using pip install geoip"
    pass

try:
    from raven import Client
except ImportError:
    print "It looks like raven (sentry) module isn't installed. Please install it using pip install raven"
    Client = None
    pass

logfile = "/var/log/netflow-alerting.log"
logging.basicConfig(format='%(asctime)s %(pathname)s %(levelname)s:%(message)s', level=logging.DEBUG, filename=logfile)
logging.getLogger().addHandler(logging.StreamHandler())


def main():
    parser = argparse.ArgumentParser(description='This program perform netflow nfdump queries and alert using riemann for any matched query and threshold. All configuration is done using a yaml configuration file netflow-alerting.yaml')
    parser.add_argument('-version', action='version', version='%(prog)s 0.3, Loic Lambiel exoscale')
    if Client is not None:
        parser.add_argument('-sentryapikey', help='Sentry API key', required=False, type=str, dest='sentryapikey')
    args = vars(parser.parse_args())
    return args


def sendalert(riemannhost, txt, service, state):

    client = bernhard.Client(host=riemannhost)
    host = socket.gethostname()

    client.send({'host': host,
                 'service': service,
                 'state': state,
                 'description': txt,
                 'tags': ['netflow-nfdump-alerting'],
                 'ttl': 600,
                 'metric': 1})

    logging.info('%s', txt)


def sendclear(riemannhost, service, state):

    client = bernhard.Client(host=riemannhost)
    host = socket.gethostname()

    client.send({'host': host,
                 'service': state,
                 'state': 'ok',
                 'tags': ['netflow-nfdump-alerting'],
                 'ttl': 3700,
                 'metric': 0})


def nfquery():

    logging.info('Script started')

    f = open('/etc/netflow-alerting.yaml')
    data = yaml.load(f)
    f.close()

    s = shelve.open('/tmp/netflow-alerting.db')

    profile = data["profile"]
    netflowpath = data["netflowpath"]
    sources = data["sources"]
    queries = data["queries"]
    riemannhost = data["riemannhost"]

    # start time is -5 minutes rounded to the the previous 5 minutes
    now = datetime.datetime.now()
    rounded = now - timedelta(minutes=now.minute % 5 + 5,
                              seconds=now.second,
                              microseconds=now.microsecond)
    starttime = rounded.strftime('%Y-%m-%d %H:%M')

    if GeoIP is not None:
        GEOIP_DB_PATH = data["geoip_db_path"]
        gi = GeoIP.open(GEOIP_DB_PATH, GeoIP.GEOIP_STANDARD)
    d = pynfdump.Dumper(netflowpath, profile=profile, sources=sources)
    d.set_where(start=starttime)
    for k, v in queries.items():
        nfquery = v["query"]
        nforderby = v["order"]
        stats = v["stats"]
        state = v["state"]
        if "threshold" in v["threshold"]:
            threshold = int(v["threshold"])
        if "ipwhitelist" in v:
            ipwhitelist = v["ipwhitelist"]

        logging.info('Performing query %s %s %s', nfquery, stats, nforderby)

        search = d.search(nfquery, statistics=stats, statistics_order=nforderby, limit=500)

        for r in search:
            if threshold:
                if int(r[nforderby]) >= threshold:
                    item = str(r[stats])
                    nb = r[nforderby]
                    whois = ''
                    if "ip" in stats:
                        country_code = gi.country_code_by_addr(item)
                        whois = "Whois: http://whois.domaintools.com/%s" % (item)
                        # Check if IP is whitelisted
                        if ipwhitelist:
                            ipwhitelistmatch = all_matching_cidrs(item, ipwhitelist)
                            if ipwhitelistmatch:
                                logging.info('IP %s is whitelisted (%s)', item, ipwhitelistmatch)
                                continue

                    txt = "Alert '%s' triggered matching query '%s' with %s %s for %s %s (%s) at time '%s'. Threshold is %s. %s" % (k, nfquery, nb, nforderby, stats, item, country_code, starttime, threshold, whois)
                    service = "netflow-alerting-%s-%s" % (stats, item)
                    sendalert(riemannhost, txt, service, state)

                    logging.info('%s', txt)

                    # We add the service in the persistence DB
                    s[service] = starttime
                else:
                    break

        logging.info('Query completed')

    # We remove any entry that have older timestamp and send a riemann ok event for that envent
    for k, v in s.iteritems():
        if v != starttime:
            del s[k]
            sendclear(riemannhost, service, k)
    s.close()

    logging.info('Script completed')

# main
if __name__ == "__main__":
    args = main()
    try:
        nfquery()
    except Exception:
        if args['sentryapikey'] is None:
            raise
        else:
            client = Client(dsn=args['sentryapikey'])
            client.captureException()
