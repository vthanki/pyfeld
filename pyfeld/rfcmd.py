#!/usr/bin/env python
from __future__ import unicode_literals

import json
import sys
import urllib
from time import sleep

from pyfeld.settings import Settings
from pyfeld.upnpCommand import UpnpCommand
from pyfeld.getRaumfeld import RaumfeldDeviceSettings
from pyfeld.zonesHandler import ZonesHandler
from pyfeld.didlInfo import DidlInfo

quick_access = dict()
raumfeld_host_device = None


def get_raumfeld_infrastructure():
    global quick_access, raumfeld_host_device
    try:
        s = open(Settings.home_directory()+"/data.json", 'r').read()
        quick_access = json.loads(s)
        """sanitize"""
        for zone in quick_access['zones']:
            if not 'rooms' in zone:
                zone['rooms'] = None
            if not 'udn' in zone:
                zone['udn'] = None
        raumfeld_host_device = RaumfeldDeviceSettings(quick_access['host'])
    except Exception as err:
        print("get_raumfeld_infrastructure: Exception: {0}".format(err))
        return None


'''
most stuff is already in the zone handler, this needs some tidy up
'''


def get_room_udn(room_name):
    global quick_access
    for zone in quick_access['zones']:
        if zone['rooms'] is not None:
            for room in zone['rooms']:
                if room['name'] == room_name:
                    return room['udn']
    return None


def get_room_zone_index(room_name):
    global quick_access
    index = 0
    for zone in quick_access['zones']:
        if zone['rooms'] is not None:
            for room in zone['rooms']:
                if room['name'] == room_name:
                    return index
        index += 1
    return -1


def usage(argv):
    print("Usage: " + argv[0] + " [OPTIONS] [COMMAND] {args}")
    print("Version: 0.3")
    print("OPTIONS: ")
    print("  -j,--json               use json as output format, default is plain text lines")
    print("  -d,--discover           Discover again (will be fast if host didn't change)")
    print("  -z,--zone #             Specify zone index (use info to get a list), default 0 = first")
    print("  -r,--zonewithroom name  Specify zone index by using room name")
    print("  -m,--mediaserver #      Specify media server, default 0 = first")
    print("  -v,--verbose            Increase verbosity (use twice for more)")

    print("COMMANDS: (some commands return xml)")
    print("  browse path              Browse for media append /* for recursive")
    print("  play browseitem          Play item in zone i.e. play '0/My Music/Albums/TheAlbumTitle'")
    print("  stop|prev|next           Control currently playing items in zone")
    print("  currentsong              show current song info")
    print("  volume #                 Set volume of zone")
    print("  getvolume                Get volume of zone")
    print("  position                 Get position info of zone")
    print("  seek #                   Seek to a specific position")
    print("  standby state {room(s)}  Set a room into standby state=on/off/auto")
    print("SIMPLE INFO: (return lists of easily parsable text)")
    print("  rooms                    Show list of rooms ordererd alphabetically")
    print("  zoneinfo                 Show info on zone")
    print("  zones                    Show list of zones, unassigned room is skipped")
    print("  info                     Show list of zones and rooms")
    print("#MACRO OPERATIONS")
    print("  wait condition           wait for condition (expression) [volume, position, duration, title, artist] i.e. volume < 5 or position==120 ")
    print("  fade time vols vole      fade volume from vols to vole in time seconds ")
    print("#ZONE MANAGEMENT (will automatically discover after operating)")
    print("  createzone {room(s)}     create zone with list of rooms (space seperated)")
    print("  addtozone {room(s)}      add rooms to existing zone")
    print("  drop {room(s)}           drop rooms from it's zone")


def build_dlna_play_container(udn, server_type, path):
    s = "dlna-playcontainer://" + urllib.parse.quote(udn)
    s += "?"
    s += 'sid=' + urllib.parse.quote(server_type)
    s += '&cid=' + urllib.parse.quote(path)
    s += '&md=0'
    return s


def build_dlna_play_single(udn, server_type, path):
    s = "dlna-playsingle://" + urllib.parse.quote(udn)
    s += "?"
    s += 'sid=' + urllib.parse.quote(server_type)
    s += '&iid=' + urllib.parse.quote(path)
    return s


def get_rooms(verbose):
    global quick_access
    result = ""
    room_list = []
    for zone in quick_access['zones']:
        for room in zone['rooms']:
            if room is not None:
                room_name = room['name']
                if verbose:
                    room_name += ":"+room['location']
                room_list.append(room_name)
    room_list.sort()
    for r in room_list:
        result += r + "\n"
    return result


def get_didl_extract(didl_result, format="plain"):
    didlinfo = DidlInfo(didl_result, True)
    items = didlinfo.get_items()
    if format == 'json':
        return json.dumps(items, sort_keys=True, indent=2)
    else:
        result = ""
        result += items['artist'] + "\n"
        result += items['title'] + "\n"
        result += items['album'] + "\n"
        result += items['ressampleFrequency'] + "\n"
        result += items['ressourceType'] + "\n"
        result += items['resbitrate'] + "\n"
        result += items['rfsourceID'] + "\n"
    return result


def get_specific_zoneinfo(uc):
    results = uc.get_position_info()
    result = ""
    if 'AbsTime' in results:
        result += results['AbsTime'] + "\n"
    else:
        result += "\n"
    if 'TrackDuration' in results:
        result += results['TrackDuration'] + "\n"
    else:
        result += "\n"
    if 'TrackMetaData' in results:
        result += get_didl_extract(results['TrackMetaData'])
    return result


def get_info(verbose):
    i = 0
    result = ""
    for media_server in quick_access['mediaserver']:
        if verbose >= 1:
            result += ("Mediaserver #{0} : {1}\n".format(i, media_server['udn']))
        else:
            result += ("Mediaserver #{0}\n".format(i))
        i += 1
    i = 0
    for zone in quick_access['zones']:
        if verbose == 2:
            result += ("Zone #{0} : {1} : {2} -> {3}\n".format(i, zone['name'], str(zone['udn']), zone['host']))
        elif verbose == 1:
            result += ("Zone #{0} : {1} : {2}\n".format(i, zone['name'], str(zone['udn'])))
        else:
            result += ("Zone #{0} : {1}\n".format(i, zone['name']))
        if zone['rooms'] is not None:
            for room in zone['rooms']:
                if verbose == 2:
                    result += ("\tRoom '{0}' : {1} -> {2}\n".format(room['name'], room['udn'], room['location']))
                elif verbose == 1:
                        result += ("\tRoom '{0}' : {1}\n".format(room['name'], room['udn']))
                else:
                    result += ("\tRoom '{0}'\n".format(room['name']))
        i += 1
    return result


def get_zone_info():
    result = ""
    for zone in quick_access['zones']:
        if zone['rooms'] is not None:
            if zone['name'] != "unassigned room":
                result += zone['name']
                result += '\n'
    return result


def timecode_to_seconds(tc):
    components = tc.split(':')
    return int(components[0]) * 3600 + int(components[1]) * 60 + int(components[2])


#unsused variables are used in the evil eval code
def wait_operation(uc, condition):
    while True:
        result = uc.get_volume()
        volume = int(result['CurrentVolume'])
        results = uc.get_position_info()

        try:
            didlinfo = DidlInfo(results['TrackMetaData'])
            items = didlinfo.get_items()
            #print(items)
            title = items['title']
            artist = items['artist']
        except:
            pass

        track = -1
        if 'Track' in results:
            track = int(results['Track'])
        duration = -1
        if 'TrackDuration' in results:
            duration = timecode_to_seconds(results['TrackDuration'])
        position = -1
        if 'AbsTime' in results:
            position = timecode_to_seconds(results['AbsTime'])
        #print(volume, duration, position)
        eval_result = eval(condition)
        if eval_result:
            break
        sleep(1)
    return condition


def fade_operation(uc, time, volume_start, volume_end):
    t = 0
    while t < time:
        volume_now = volume_start+(volume_end-volume_start)*t/time
        uc.set_volume(volume_now)
        sleep(1)
        t += 1
    uc.set_volume(volume_end)
    return "done"


def discover():
    zones_handler = ZonesHandler()
    if not zones_handler.reprocess():
        local_ip = RaumfeldDeviceSettings.get_local_ip_address()
        zones_handler.search_nmap_range(local_ip + "/24")
        zones_handler.publish_state()
    get_raumfeld_infrastructure()


def run_main():
    global quick_access
    argv = sys.argv
    verbose = 0
    if len(sys.argv) < 2:
        usage(sys.argv)
        sys.exit(2)
    zoneIndex = 0
    mediaIndex = 0
    room = ""
    format = "plain"

    argpos = 1

    get_raumfeld_infrastructure()

    while sys.argv[argpos].startswith('-'):
        if sys.argv[argpos].startswith('--'):
            option = sys.argv[argpos][2:]
        else:
            option = sys.argv[argpos]
        argpos += 1
        if option == 'verbose' or option == '-v':
            verbose += 1
        elif option == 'help' or option == '-h':
            usage(sys.argv)
            sys.exit(2)
        elif option == 'json' or option == '-j':
            format = "json"
        elif option == 'discover' or option == '-d':
            discover()
            if argpos == len(sys.argv):
                print("done")
                sys.exit(0)
        elif option == 'zone' or option == '-z':
            zoneIndex = int(sys.argv[argpos])
            argpos += 1
        elif option == 'zonewithroom' or option == '-r':
            zoneIndex = get_room_zone_index(sys.argv[argpos])
            if zoneIndex == -1:
                print("ERROR: room with name '{0}' not found".format(sys.argv[argpos]))
                print("Available rooms are to be found here:\n" + get_info(verbose))
                exit(-1)
            argpos += 1
        elif option == 'mediaserver' or option == '-m':
            mediaIndex = int(sys.argv[argpos])
            argpos += 1
        else:
            print("unknown option --{0}".format(option))
            usage(sys.argv)
            sys.exit(2)

    uc = UpnpCommand(quick_access['zones'][zoneIndex]['host'])
    uc_media = UpnpCommand(quick_access['mediaserver'][mediaIndex]['location'])
    operation = sys.argv[argpos]
    argpos += 1
    result = None
    if operation == 'play':
        udn = quick_access['mediaserver'][mediaIndex]['udn']
        transport_data = dict()
        result = uc_media.browsechildren(argv[argpos])
        if result is None:
            result = uc_media.browse(argv[argpos])
            transport_data['CurrentURI'] = build_dlna_play_single(udn, "urn:upnp-org:serviceId:ContentDirectory", argv[argpos])
        else:
            transport_data['CurrentURI'] = build_dlna_play_container(udn, "urn:upnp-org:serviceId:ContentDirectory",
                                                                     argv[argpos])
        #print(transport_data['CurrentURI'])
        transport_data['CurrentURIMetaData'] = '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" xmlns:raumfeld="urn:schemas-raumfeld-com:meta-data/raumfeld"><container></container></DIDL-Lite>'
        uc.set_transport_uri(transport_data)
    elif operation == 'stop':
        result = uc.stop()
    elif operation == 'next':
        result = uc.next()
    elif operation == 'prev':
        result = uc.previous()
    elif operation == 'volume' or operation == 'setvolume':
        result = uc.set_volume(argv[argpos])
    elif operation == 'getvolume':
        result = uc.get_volume()
    elif operation == 'standby':
        state = argv[argpos]
        argpos += 1
        while argpos < len(sys.argv):
            udn = get_room_udn(argv[argpos])
            if udn is None:
                print("unknown room "+argv[argpos])
            else:
                raumfeld_host_device.set_room_standby(str(udn), state)
            argpos += 1
    elif operation == 'position':
        results = uc.get_position_info()
        result = dict()
        if 'TrackDuration' in results:
            result['TrackDuration'] = results['TrackDuration']
        if 'AbsTime' in results:
            result['AbsTime'] = results['AbsTime']
        if 'TrackMetaData' in results:
            result['TrackMetaData'] = results['TrackMetaData']
    elif operation == 'seek':
        #check argv[argpos] if contains :
        result = uc.seek(argv[argpos])
    elif operation == 'wait':
        result = wait_operation(uc, argv[argpos])
    elif operation == 'fade':
        result = fade_operation(uc, int(argv[argpos]), int(argv[argpos+1]), int(argv[argpos+2]))
    elif operation == 'createzone':
        rooms = set()
        result = "zone creation adding rooms:\n"
        while argpos < len(sys.argv):
            udn = get_room_udn(argv[argpos])
            result += "{0}'\n".format(str(udn))
            rooms.add(str(udn))
            argpos += 1
        raumfeld_host_device.create_zone_with_rooms(rooms)
        discover()
    elif operation == 'addtozone':
        zone_udn = quick_access['zones'][zoneIndex]['udn']
        rooms = set()
        result = "zone creation adding rooms:\n"
        while argpos < len(sys.argv):
            udn = get_room_udn(argv[argpos])
            result += "{0}'\n".format(str(udn))
            rooms.add(str(udn))
            argpos += 1
        raumfeld_host_device.add_rooms_to_zone(zone_udn, rooms)
        discover()
    elif operation == 'drop':
        result = "drop rooms from zone:\n"
        while argpos < len(sys.argv):
            udn = get_room_udn(argv[argpos])
            result += str(raumfeld_host_device.drop_room(str(udn)))
            argpos += 1
        discover()
    elif operation == 'browse':
        if argv[argpos].endswith('/*'):
            result = uc_media.browse_recursive_children(argv[argpos][:-2], format, 10)
        else:
            result = uc_media.browse_recursive_children(argv[argpos], format, 0)
    elif operation == 'browseinfo':
        results = uc_media.browse(argv[argpos])
        result = get_didl_extract(results['Result'], format)
    elif operation == 'search':
        result = uc_media.search(argv[argpos], argv[argpos+1], format)
    elif operation == 'rooms':
        result = get_rooms(verbose)
        result = result[:-1]
    elif operation == 'zones':
        result = get_zone_info()
        result = result[:-1]
    elif operation == 'zoneinfo':
        result = get_specific_zoneinfo(uc)
        result = result[:-1]
    elif operation == 'info':
        result = get_info(verbose)
        result = result[:-1]
    else:
        usage(sys.argv)

    sys.stdout.write(result)
#    pp = pprint.PrettyPrinter(indent=4, width=160)
#    pp.pprint(result)

if __name__ == "__main__":
    run_main()
