#!/usr/bin/env python3

import mimetypes

import errno
import json
import logging
import os

from socket import *
import datetime
from datetime import timedelta
from alarmClock import AlarmClocks, AlarmClock
from getRaumfeld import RaumfeldDeviceSettings, HostDevice
from intermezzo import Intermezzo
from logRfStatistics import LogRfStatistics
from magicMoments import MagicMoments, MagicMoment

from http.server import HTTPServer, urllib
from http.server import BaseHTTPRequestHandler
from cgi import parse_header, parse_multipart
from browseMedia import get_browse_list, split_browse_path, browse_item_info
from daemon3x import Daemon
from mediaStore import MediaStore
from zonesHandler import *
import requests

"""
Todo (lots):
- xrf is way to big
- create path (xrf/page= cmd= session= etc.)
- cleanup singletons (zoneshandler, alarmhandler etc.) and create a global manager
- exceptions handler -> write to standard system log with automatic inspect
- create more tests for most stuff (xml parsing, file saving etc.)
- refresh after zone change (with notification handling in SubscriptionHandler)

Feature completion:
- alarm
  + add/remove
  + check evaluate
  + use browse path
  + check playing

- sleep
  + add/remove
  + evaluate
  + stop and sleep

- loop
  + setposition
  + evaluate

- intermezzo
  + create good concept (think of queue manipulation)

- wikinator
  + cleanup
  + filter
  + sort
  + remove
  + star items
  + play items (if not info from radio station)

- insights
  + special stuff for internal usage

- magic moment
  + add
  + remove

- browse
  + albumart (eventually alternative?)

- global zoneinfo in menu or side
  + stop/start
  mini info


urn:raumfeld-com:serviceId:ConfigService
<Event xmlns="urn:schemas-raumfeld-com:metadata-1-0/CS"><InstanceID val="0">
<Rev18713 val="/Preferences/ZoneConfig/Rooms"/>
</InstanceID>
</Event>
"""

looper = None
magic_moments = MagicMoments("settings/magicmoments.json")
intermezzo = Intermezzo("settings/intermezzo.json")
zones_handler = ZonesHandler()
media_store = MediaStore("settings/starredmedia.json")

#rewrite with queue and notifications
needs_to_reload_zone_config = False

class SessionSettings:

    currentzone = "uuid:15233052-230d-42c7-be8c-ab9654d47625"
    selectedmusic = "0"

    def save_settings(self):
        pass

    def load_settings(self):
        pass


class EventListenerAtom:
    def __init__(self, sid, timeout, net_location, service, udn):
        self.sid = sid
        self.net_location = net_location
        self.service = service
        self.time_created = datetime.now()
        self.timeout_at = self.time_created + timedelta(0, int(timeout))
        self.timeout = timeout
        self.udn = udn

class SubscriptionHandler:

    def __init__(self, zones_handler):
        self.zones_handler = zones_handler
        self.subscription_interval = 300
        self.subscriptions = dict()

    def __is_subscribed(self, nl, service, udn):
        t_now = datetime.now()
        for sid, atom in self.subscriptions.items():
            if atom.service == service and atom.net_location == nl and udn == atom.udn:
                if atom.timeout_at < t_now: #this subscription expires soon
                    return False
                return True
        return False


    def __subscribe_service_list(self, local_ip, udn, upnp_service):
        try:
            for upnp in upnp_service.services_list:
                nl = upnp_service.network_location
                if not self.__is_subscribed(nl, upnp['eventSubURL'], udn):
                    self.create_new_subscription(local_ip, 28080, nl, upnp['eventSubURL'], udn)
        except Exception as e:
            print("__subscribe_service_list error {0}".format(e))

    def subscription_thread(self, delay_value):
        local_ip = get_local_ip_address()
        while True:
            sleep(15)
            for media_server in zones_handler.media_servers:
                self.__subscribe_service_list(local_ip, media_server.udn, media_server.upnp_service)
            for config_dev in zones_handler.config_device:
                self.__subscribe_service_list(local_ip, config_dev.udn, config_dev.upnp_service)
            for rf_dev in zones_handler.raumfeld_device:
                self.__subscribe_service_list(local_ip, rf_dev.udn, rf_dev.upnp_service)

            for zone in zones_handler.get_active_zones():
                try:
                    if zone.services is None:
                        continue
                    for upnp_service in zone.services.services_list:
                        nl = zone.services.network_location
                        if not self.__is_subscribed(nl, upnp_service['eventSubURL'], media_server.udn):
                            self.create_new_subscription(local_ip, 28080, nl, upnp_service['eventSubURL'], zone.get_udn() )
                    if zone.rooms is None:
                        continue
                    for room in zone.rooms:
                        for upnp_service in room.upnp_service.services_list:
                            nl = room.upnp_service.get_network_location()
                            if not self.__is_subscribed(nl, upnp_service['eventSubURL'], room.get_udn()):
                                self.create_new_subscription(local_ip, 28080, nl, upnp_service['eventSubURL'], room.get_udn())
                except Exception as e:
                    print("subscription error {0}".format(e))
            sleep(delay_value-5)

    def renew_subscriptions(self):
        pass

    def create_new_subscription(self, local_ip, port, net_location, service, udn):

        headers = {"Host": net_location,
                   "Callback": "<http://" + local_ip + ":" + str(port) + "/"+udn[5:]+">",
                   "NT": "upnp:event",
                   "Timeout": "Second-" + str(self.subscription_interval),
                   "Accept-Encoding": "gzip, deflate",
                   "User-Agent": "xrf/1.0",
                   "Connection": "Keep-Alive",
                   }
        print("SUBSCRIBE http://"+net_location+service)
        response = requests.request('SUBSCRIBE', "http://"+net_location+service, headers=headers, data="")
        sid = response.headers['sid']
        seconds_search = re.search("Second-([0-9]+)", response.headers['timeout'], re.IGNORECASE)
        if seconds_search:
            timeout = seconds_search.group(1)
        else:
            timeout = "300"

        print("subscription: " + net_location+service + ":" + str(response))
        self.subscriptions[sid] = EventListenerAtom(sid, timeout, net_location, service, udn)


class ActionState:
    def __init__(self):
        self.state = "inactive"


class SleepAction(ActionState):

    def __init__(self, zone_index, stop_time, fade_time):
        super().__init__()
        self.zone_index = zone_index
        self.stop_time = stop_time
        self.fade_time = fade_time

    def get_settings(self):
        values = dict()
        values['zone_index'] = self.zone_index
        values['stop_time'] = self.stop_time
        values['fade_time'] = self.fade_time
        return values


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


class ActiveActionsInZone:
    def __init__(self, zone_name):
        self.zone_name = zone_name
        self.action = None

    def set_action(self, action):
        self.action = action


class ZoneControl(threading.Thread):

    def __init__(self, interval, func, *args, **kwargs):
        threading.Thread.__init__(self)

        self.interval = interval  # seconds between calls
        self.func = func          # function to call
        self.args = args          # optional positional argument(s) for call
        self.kwargs = kwargs      # optional keyword argument(s) for call
        self.runable = True
        self.zone = None

    def run(self):
        global log_stats, media_store
        try:
            os.mkdir('settings', 0o777)
        except:
            pass
        log_stats = LogRfStatistics('settings/stats.sqlite3')
        media_store = MediaStore('settings/starred.json')
        while self.runable:
            self.func(*self.args, **self.kwargs)
            sleep(self.interval)

    def stop(self):
        self.runable = False

    # check if action is ok to add, certain actions overwrite other actions
    # depending on whether they happen in the same zone or same time span

    # alarm will check if the required zone (rooms in zone) exists,
    #if not it will be created
    def force_zone_creation(self):
        pass

    @staticmethod
    def alarm_play(alarm):
        param = dict()

        param["hasroom"] = alarm.settings['room']
        param["cmd"] = "media"
#        media = media_store.get_store(alarm.settings['song'])
#        zones_handler.set_media(media, param)

        param = dict()
        param["cmd"] = "volume"
        param["hasroom"] = alarm.settings['room']
        param["value"] = [alarm.settings['finalvolume'], ]
        zones_handler.set("volume", param)  # not so nice parameter passing, kwargs???
        param = dict()
        param["hasroom"] = alarm.settings['room']
        param["cmd"] = "play"
        zones_handler.do("play", param)
        print("ALARM!!!!!!")

    #contains the actions on zones like fade and check seek positions
    zone_actions = dict()
    lastmin = -1  # serve fade stuff
    # check if minute changed
    @staticmethod
    def serve_tick():
        if ZoneControl.zone_actions:
            pass

        now = datetime.now()
        if now.second % 10 == 0:
            pass
        if looper is not None:
            looper.handle_position()

        alarm_clocks.check_time(now)

alarm_clocks = AlarmClocks(ZoneControl.alarm_play, "settings/alarm.json")


def time_as_json():
    now = datetime.now()
    dt_s = dict()
    dt_s['second'] = now.second
    dt_s['hour'] = now.hour
    dt_s['minute'] = now.minute
    dt_s['day'] = now.day
    dt_s['month'] = now.month
    dt_s['year'] = now.year
    return json.dumps(dt_s, sort_keys=True, indent=2)


def add_alarm(param):
    return {"add_alarm": "ok"}


def do_sleep(param):
    return {"sleep": "ok"}


def remove_sleep(param):
    return {"sleep": "ok"}


def do_magic(param):
    return {"sleep": "ok"}


def do_loop(param):
    return {"sleep": "ok"}


rewrite_pages = [  # const
        ['^/(.*)(html|ico|js|ttf|svg|woff|eot|otf|css|less|map).*$', './web/\\1\\2'],
        ['^/$', './web/index.html']
]


class CreatePage(object):

    @staticmethod
    def __get_template(filename):
        with open(filename, "rb") as f:
            r = f.read()
        return r.decode("utf-8")

    def __init__(self):
        self.map_name = {
            "alarm": "Alarm Clock"
            , "browse": "Browse Media"
            , "info": "eXtended Raumfeld Info"
            , "insights": "Raumfeld Insights"
            , "intermezzo": "Intermezzo"
            , "looper": "Looper <i class=\"fa fa-refresh fa-spin\"></i>"
            , "magic": "Magic Moments"
            , "sleep": "Sleep Timer"
            , "wikinator": "Wikinator"
            , "zones": "Manage Zones"
        }

        self.dispatch_map = {
            "alarm": self.__page_alarm
            , "browse": self.__page_browse
            , "info": self.__page_info
            , "insights": self.__page_insights
            , "intermezzo": self.__page_intermezzo
            , "looper": self.__page_looper
            , "magic": self.__page_magic
            , "sleep": self.__page_sleep
            , "wikinator": self.__page_wikinator
            , "zones": self.__page_zones
        }

    def __page_alarm(self, param):
        template = self.__get_template("web/templates/alarm-entry.html")
        result = self.__get_template("web/templates/alarm-edit.html")
        index = 0
        for alarm in alarm_clocks.get_alarm_list():
            alarm.settings['index'] = str(index)
            result += template.format(**alarm.settings)
            index += 1
        return result


    def __page_info(self, param):
        result = ""
        dict_param = {'room': 'hello world'}
        template = self.__get_template("web/templates/info.html")
        result += template.format(**dict_param)
        return result

    def __page_intermezzo(self, param):
        result = ""
        dict_param = {'room': 'hello world'}
        template = self.__get_template("web/templates/intermezzo.html")
        result += template.format(**dict_param)
        return result

    def __page_zones(self, param):
        result = ""
        dict_param = {'room': 'hello world'}
        dict_param['zonelist'] = self.__room_list()
        template = self.__get_template("web/templates/zones.html")
        result += template.format(**dict_param)
        return result

    def __page_browse(self, param):
        result = ""
        if 'path' in param:
            path = param['path'][0]
            SessionSettings.selectedmusic = path
        else:
            path = SessionSettings.selectedmusic
        dict_param = {"path": split_browse_path(path),
                      'medialist': get_browse_list(zones_handler, media_store, path)}
        template = self.__get_template("web/templates/browse.html")
        result += template.format(**dict_param)
        return result

    def __page_looper(self, param):
        result = ""
        dict_param = {'room': 'hello world'}
        dict_param['zonelist'] = self.__room_list()

        template = self.__get_template("web/templates/looper.html")
        result += template.format(**dict_param)
        return result

    def __page_magic(self, param):
        result = ""
        dict_param = {'room': 'hello world'}
        template = self.__get_template("web/templates/magic.html")
        result += template.format(**dict_param)
        return result

    def __page_sleep(self, param):
        result = ""
        dict_param = {'room': 'hello world'}
        template = self.__get_template("web/templates/sleep.html")
        result += template.format(**dict_param)
        return result

    def __page_insights(self, param):
        result = ""
        dict_param = {'hostip': zones_handler.found_protocol_ip}
        template = self.__get_template("web/templates/insights.html")
        result += template.format(**dict_param)
        return result

    def __page_wikinator(self, param):
        template = self.__get_template("web/templates/wikinator-entry.html")
        result = self.__get_template("web/templates/wikinator.html")
        entries = log_stats.get_rows_history(0, 50)
        for item in entries:
            result += template.format(**item)
        return result

    def __room_list(self):
        zone_entry = self.__get_template("web/templates/zone-list.html")
        list_entry = self.__get_template("web/templates/room-list.html")
        zone_inset = ""

        for zone in zones_handler.active_zones:
            zone_dict = dict()
            zone_dict['name'] = zone.get_friendly_name()
            room_inset = ""
            for room in zone.rooms:
                room_dict = dict()
                room_dict['name'] = room.get_name()
                room_dict['udn'] = room.get_udn()
                room_dict['renderer_udn'] = room.get_renderer_udn()
                room_dict['volume'] = str(room.get_volume())
                room_move_list = ""
                for addtozone in zones_handler.active_zones:
                    if str(addtozone.udn) != str(zone.udn) and addtozone.udn is not None:
                        room_move_list += '<li><a href="/xrf?page=zones&cmd=moveroom' \
                                          '&roomudn=' + room.get_udn() + \
                                          '&zoneudn=' + str(addtozone.udn) + \
                                          '">Move to ' + \
                                          addtozone.get_friendly_name() + \
                                          '</a></li>'
                if len(room_move_list):
                    room_move_list += '<li class="divider"></li>'
                if zone.udn is not None:
                    room_move_list += '<li><a href="/xrf?page=zones&cmd=removeroom' \
                                      '&roomudn=' + room.get_udn() + \
                                      '">Just remove</a></li>'
                if len(room_move_list):
                    room_move_list += '<li class="divider"></li>'

                room_move_list += '<li><a href="/xrf?page=zones&cmd=newzoneforroom' \
                                  '&roomudn=' + room.get_udn() + \
                                  '">To new room</a></li>'
                room_dict['roommodify'] = room_move_list
                room_inset += list_entry.format(**room_dict)

            zone_dict['roomlist'] = room_inset
            zone_dict['udn'] = str(zone.udn)
            zone_dict['media'] = zone.media
            zone_dict['media-title'] = zone.get_current_title()
            zone_dict['media-title'] = zone.get_current_title()
            zone_dict['media-album'] = zone.get_current_album()
            zone_dict['media-artist'] = zone.get_current_artist()
            zone_dict['position'] = zone.position
            zone_dict['transport'] = zone.state_variables.get_state('TransportState')
            zone_dict['playing'] = zone.state_variables.get_state('CurrentPlayMode')
            zone_dict['volume'] = zone.state_variables.get_state('Volume')
            if str(zone.udn) == SessionSettings.currentzone:
                zone_dict['checkedzone'] = "fa-check-square-o"
            else:
                zone_dict['checkedzone'] = "fa-square-o"
            zone_inset += zone_entry.format(**zone_dict)
        return zone_inset

    def create(self, page, param):
        try:
            pages = dict()
            main_page = self.__get_template("web/templates/main-page-container.html")
            pages['servertime'] = datetime.strftime(datetime.now(), '%H:%M:%S')
            tmpl = self.__get_template("web/templates/navigation-bar-header.html")

            pages['navigation-bar-header'] = tmpl.format(**pages)
            tmpl = self.__get_template("web/templates/navigation-bar-side-collapse.html")
            pages['zonelist'] = self.__room_list()

            pages['navigation-bar-side-collapse'] = tmpl.format(**pages)
            pages['navigation-bar-top-links'] = self.__get_template("web/templates/navigation-bar-top-links.html")

            try:
                pages['main-page-content'] = self.dispatch_map[page](param)
            except Exception as e:
                #could template this...
                pages['main-page-content'] = '<div class="panel-group"><div class="panel panel-danger">'
                pages['main-page-content'] += '<div class="panel-heading">Infamous 500</div>'
                pages['main-page-content'] += '<div class="panel-body">'
                pages['main-page-content'] += "all kaputt because: {0}".format(e) + '</div></div>'
            pages['pagename'] = self.map_name[page]
            if 'refresh' in param:
                pages['autorefresh'] = '<meta http-equiv="refresh" content="'+param['refresh']+'; URL=/xrf?page=zones">'
            else:
                pages['autorefresh'] = ""
            completed_page = main_page.format(**pages)
            return bytearray(completed_page, 'UTF-8')
        except Exception as e:
            return bytearray("error in creating page {0}".format(e), "UTF-8")


page_maker = CreatePage()


def system_info(param):
    output = json.dumps(zones_handler.get_zones_as_dict(0), sort_keys=True, indent=4)
    return output

class JSONRequestHandler (BaseHTTPRequestHandler):

    verbose = False

    def log_message(self, format, *args):

        if JSONRequestHandler.verbose:
            print("JSONRequestHandler %s - - [%s] %s\n" %
                             (self.address_string(),
                              self.log_date_time_string(),
                              format % args))



    '''    def __init__(self):
        super().__init__()
        self.verbose = False
    '''

    def page_not_found(self):
        self.send_response(404)
        self.send_header("Content-type", "application/json")
        output = '{"errors":[{"status": 404, "message": "page not found"}]}'
        self.wfile.write(bytearray(output, 'UTF-8'))

    def handle_set_query(self, param):
        try:
            output = '{"error":"possible"}'
            cmd = param['cmd'][0]
            if cmd == 'sleep':
                stop_time = param['stoptime'][0]
                fade_time = param['fadetime'][0]
                zone_index = zones_handler.get_request_zone(param)
                sleepAction = SleepAction(zone_index, stop_time, fade_time)
                output = json.dumps(sleepAction.get_settings())
            elif cmd in ['media']:
                media = media_store.get_store(int(param['value'][0]))
                output = zones_handler.set_media(media, param)
            elif cmd in ['volume', 'vol']:
                res = zones_handler.set(cmd, param)
                output = json.dumps(res)
            elif cmd in ['alarm']:
                res = add_alarm(cmd, param)
                output = json.dumps(res)
            else:
                self.page_not_found()
                return
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.wfile.write(bytearray(output, 'UTF-8'))
        except Exception as e:
            print("handle_set_query error {0}".format(e))

    def handle_remove_query(self, param):
        try:
            output = '{"error":"possible"}'
            cmd = param['cmd'][0]
            if cmd == 'sleep':
                pass
            elif cmd in ['media']:
                pass
            elif cmd in ['alarm']:
                pass
            elif cmd in ['magic']:
                pass
            else:
                self.page_not_found()
                return
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.wfile.write(bytearray(output, 'UTF-8'))
        except Exception as e:
            print("handle_set_query error {0}".format(e))

    def handle_get_query(self, param):
        try:
            cmd = param['cmd'][0]
            if cmd == 'zones':
                output = json.dumps(zones_handler.get_zones_as_dict(1), sort_keys=True, indent=4)
            elif cmd in ['volume', 'media', 'position']:
                res = zones_handler.get(cmd, param)
                output = json.dumps(res, sort_keys=True, indent=4)
            elif cmd == 'alarms':
                output = alarm_clocks.get_json()
            elif cmd == 'magicmoments':
                output = magic_moments.get_json()
            elif cmd == 'timedate':
                output = time_as_json()
            elif cmd == 'sleep':
                output = do_sleep(param)
            elif cmd == 'loop':
                output = do_loop(param)
            elif cmd == 'magic':
                output = do_magic(param)
            elif cmd == 'medialist':
                output = json.dumps(media_store.get_nice_media_list(), sort_keys=True, indent=4)
            elif cmd == 'info':
                output = system_info(param)
            else:
                self.page_not_found()
                return
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.wfile.write(bytearray(output, 'UTF-8'))

        except Exception as e:
            print("handle_get_query error {0}".format(e))

    def handle_do_query(self, param):
        try:
            output = "{}"
            cmd = param['cmd'][0]
            if cmd in ['play', 'pause', 'stop', 'seek', 'seekfwd', 'seekback', 'fade', 'next', 'prev']:
                res = zones_handler.do(cmd, param)
                output = json.dumps(res)
            elif cmd == 'storemedia':
                media_store.store_media(zones_handler.get_current_media(param))
                result = {"items": media_store.count}
                output = json.dumps(result, sort_keys=True, indent=4)
            else:
                self.page_not_found()
                return
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.wfile.write(bytearray(output, 'UTF-8'))
        except Exception as e:
            print("handle_do_query error {0}".format(e))

    def handle_push_query(self, param):
        try:
            cmd = param['cmd'][0]
            if cmd in ['music']:
                output = json.dumps(cmd)
            else:
                self.page_not_found()
                return
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.wfile.write(bytearray(output, 'UTF-8'))
        except Exception as e:
            print("handle_do_query error {0}".format(e))

    def handle_xrf_query(self, param):
        try:
            if 'playpath' in param:
                playpath = param['playpath'][0]
            if 'cmd' in param:
                if param['cmd'][0] == 'play' or param['cmd'][0] == 'playsingle':
                    for server in zones_handler.media_servers:
                        udn = server.udn
                        break
                    transport_data = dict()

                    if param['cmd'][0] == 'play':
                        transport_data['CurrentURI'] = build_dlna_play_container(udn,
                                                                                 "urn:upnp-org:serviceId:ContentDirectory",
                                                                                 playpath)
                    else:
                        transport_data['CurrentURI'] = build_dlna_play_single(udn,
                                                                              "urn:upnp-org:serviceId:ContentDirectory",
                                                                              playpath)
                    print(transport_data['CurrentURI'])
                    transport_data[
                        'CurrentURIMetaData'] = '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" xmlns:raumfeld="urn:schemas-raumfeld-com:meta-data/raumfeld"><container></container></DIDL-Lite>'
                    for zone in zones_handler.active_zones:
                        if str(zone.udn) == SessionSettings.currentzone:
                            zone.upnpcmd.set_transport_uri(transport_data)
                elif param['cmd'][0] == 'tag':
                    dict_obj = browse_item_info(zones_handler, playpath)
                    media_store.add(dict_obj)
                elif param['cmd'][0] == 'untag':
                    media_store.remove(playpath)
                elif param['cmd'][0] == 'selectzone':
                    SessionSettings.currentzone = param['newzone'][0]
                elif param['cmd'][0] == 'moveroom':
                    HostDevice.get().add_rooms_to_zone(param['zoneudn'][0], param['roomudn'])  # pass rooms as list
                    param['refresh'] = "3"
                elif param['cmd'][0] == 'newzoneforroom':
                    HostDevice.get().add_rooms_to_zone("", param['roomudn'])  # pass rooms as list
                    param['refresh'] = "3"
                elif param['cmd'][0] == 'removeroom':
                    HostDevice.get().drop_room(param['roomudn'][0])
                    param['refresh'] = "3"

            page = param['page'][0]
            output = page_maker.create(page, param)
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.wfile.write(output)
        except Exception as e:
            print("handle_do_query error {0}".format(e))

    def do_GET(self):
        global zones_handler
        try:
            for pair in rewrite_pages:
                key = pair[0]
                replacement = pair[1]
                if re.match(key, self.path):
                    path = re.sub(key, replacement, self.path)
                    try:
                        print(path)
                        output = open(path, 'rb').read()
                        self.send_response(200)
                        guessed_mime_type = mimetypes.guess_type(path, strict=False)
                        print(path, guessed_mime_type)
                        self.send_header("Content-type", guessed_mime_type)
                        self.wfile.write(output)
                        return
                    except Exception as e:
                        print("Exception {0}".format(e))
                        pass
        except:
            pass
        try:
            if self.path == '/':
                try:
                    q = dict()
                    q['page'] = ["alarm"]
                    self.handle_xrf_query(q)
                    return
                except Exception as e:
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    output = "Rephrase your request, this is 404<br/>you asked for [" + str(self.path)+"]"
                    self.wfile.write(bytearray(output, 'UTF-8'))
                    return
            else:
                r = self.path.split('?')
                if len(r) == 2:
                    q = urllib.parse.parse_qs(r[1])
                    if r[0] == '/set':
                        self.handle_set_query(q)
                    elif r[0] == '/get':
                        self.handle_get_query(q)
                    elif r[0] == '/do':
                        self.handle_do_query(q)
                    elif r[0] == '/push':
                        self.handle_push_query(q)
                    elif r[0] == '/xrf':
                        self.handle_xrf_query(q)
                    else:
                        self.page_not_found()
                    return
            output = open("./html/" + self.path[1:], 'r').read()
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.wfile.write(b"\n")
            self.wfile.write(bytearray(output, 'UTF-8'))
        except Exception as e:
            output = "{'error': 'Could not find file " + self.path[1:] + ".json'" + "}"
            self.send_response(404)
            self.send_header("Content-type", "text/html")
            output = "Rephrase your request, this is 404<br/>you asked for [" + str(self.path)+"]"
            self.wfile.write(bytearray(output, 'UTF-8'))
            if self.verbose:
                print("Exception in do_POST {0}".format(e))


    def do_POST(self):
        global zones_handler
        try:
            ctype, pdict = parse_header(self.headers['content-type'])
            if ctype == 'multipart/form-data':
                postvars = parse_multipart(self.rfile, pdict)
            elif ctype == 'application/x-www-form-urlencoded':
                length = int(self.headers['content-length'])
                postvars = urllib.parse.parse_qs(
                    self.rfile.read(length),
                    keep_blank_values=1)
            else:
                postvars = {}
            return postvars
        except Exception as e:
            if self.verbose:
                print("Exception in do_POST {0}".format(e))


    def do_NOTIFY(self):
        global needs_to_reload_zone_config
        content_length = int(self.headers['content-length'])
        notification = self.rfile.read(content_length)
        result = minidom.parseString(notification.decode('UTF-8'))
        #print(result.toprettyxml())
        notification_content = XmlHelper.xml_extract_dict(result,
                                                          ['LastChange',
                                                           'Revision',
                                                           'SystemUpdateID',
                                                           'BufferFilled'])
        if 'LastChange' in notification_content:
            if '/Preferences/ZoneConfig/Rooms' in notification_content['LastChange']:
                needs_to_reload_zone_config = True
            last_change = minidom.parseString(notification_content['LastChange'])
            zones_handler.set_subscription_values("uuid:" + self.path[1:], last_change)
            print(last_change.toprettyxml())
        self.send_response(200)
        self.end_headers()


class ListenToTheUPNPWorld:

    def __init__(self):
        self.subscription_interval = 300
        self.subscriptions = dict()

    def renew_subscriptions(self):
        pass

    def create_new_subscription(self, local_ip, port, net_location, service, udn):

        headers = {"Host": net_location,
                        "Callback": "<http://" + local_ip + ":" + str(port) + "/"+udn[5:]+">",
                        "NT": "upnp:event",
                        "Timeout": "Second-" + str(self.subscription_interval),
                        "Accept-Encoding": "gzip, deflate",
                        "User-Agent": "xrf/1.0",
                        "Connection": "Keep-Alive",
                    }
        response = requests.request('SUBSCRIBE', "http://"+net_location+service, headers=headers, data="")
        sid = response.headers['sid']
        timeout = response.headers['timeout']
        print("subscription: " + net_location+service + ":" + str(response))
        self.subscriptions[sid] = EventListenerAtom(sid, timeout, net_location, service)


upnpEars = ListenToTheUPNPWorld()


def run_server(port):
    try:
        print("Starting json server on port {0}".format(port))
        json_server = HTTPServer(("", port), JSONRequestHandler)
        json_server.serve_forever()
    except Exception as e:
        print("run_Server error:"+str(e))
        syslog.syslog("run_Server error:"+str(e))
    sys.exit(-1)


def get_local_ip_address():
    s = socket(AF_INET, SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    return s.getsockname()[0]


def run_discovery(tsleep):
    global needs_to_reload_zone_config
    try:

        local_ip = get_local_ip_address()
        zones_handler.reprocess()

        while True:
            #zones_handler.search_gssdp_service("RenderingControl", "http://")
            try:
                ''' we could change strategy by reading first the quickaccess file and look if the host has changed
                '''
                zones_handler.search_nmap_range(local_ip+"/24")
                zones_handler.publish_state()
            except Exception as e:
                print("search nmap error {0}".format(e))
                pass

            #just recheck several times
            sleep(2)
            idle_cnt = tsleep/10
            for i in range(0, 10):
                idle_cnt -= 1
                if idle_cnt == 0:
                    idle_cnt = tsleep/10
                    needs_to_reload_zone_config = True
                if needs_to_reload_zone_config:
                    print("Need to reload config")
                    zones_handler.reprocess()
                    needs_to_reload_zone_config = False
                    media_store.save_if_needed()
                sleep(1)
    except Exception as e:
        print("run_discovery error:"+str(e))
        syslog.syslog("run_discovery error:" + str(e))
    sys.exit(-1)


class RFDaemon(Daemon):

    def set_system_path(self, path):
        self.system_path = path

    def set_service_port(self, port):
        self.service_port = port

    def run(self):

        try:
            subscription_handler = SubscriptionHandler(zones_handler)
            threads = []
            t = threading.Thread(target=subscription_handler.subscription_thread, args=(30,))
            threads.append(t)
            t.start()

            t = threading.Thread(target=run_discovery, args=(600,))
            threads.append(t)
            t.start()

            t = threading.Thread(target=run_server, args=(self.service_port,))
            threads.append(t)
            t.start()

            t = ZoneControl(1, ZoneControl.serve_tick)
            threads.append(t)
            t.start()

            print("started")
            for t in threads:
                t.join()
        except Exception as e:
            syslog.syslog("search gssdp_service failing:"+str(e))


def init_logging():
    logging.basicConfig(filename='/var/rf-daemon.log', level=logging.DEBUG)

if __name__ == "__main__":
    syspath = "/raumfeld/ap"
    try:
        os.mkdir("/raumfeld")
        os.mkdir(syspath)
    except:
        pass

    if not os.path.isdir(syspath):
        #todo: exchange with filebased socket
        syslog.syslog("can't create systempath " + syspath)
        sys.exit(errno.ENOENT)

    if len(sys.argv) >= 2:
        daemon = RFDaemon('/tmp/rf_daemon.pid')
        daemon.set_system_path(syspath)
        daemon.set_service_port(28080)
        if 'start' == sys.argv[1]:
            syslog.syslog('RF-Daemon started')
            daemon.start()
            init_logging()
        elif 'stop' == sys.argv[1]:
            syslog.syslog('RF-Daemon stopped')
            init_logging()
            daemon.stop()
        elif 'restart' == sys.argv[1]:
            syslog.syslog('RF-Daemon restarted')
            init_logging()
            daemon.restart()
        elif 'test' == sys.argv[1]:
            daemon.run()
        elif 'info' == sys.argv[1]:
            zones_handler.search_nmap_range(get_local_ip_address()+"/24")
            zones_handler.publish_state()
        else:
            print("Unknown command {0}".format(sys.argv[1]))
            sys.exit(-2)
        sys.exit(0)
    else:
        print("usage: %s start|stop|restart|test|info" % sys.argv[0])
        sys.exit(2)


'''

connectRoomToZone

Description

Puts the room with the given roomUDN in the zone with the zoneUDN.
Outputs

XML containing the zone and its children like getZonesJob does.
Optional Parameter

zoneUDN: The udn of the zone to connect the room to. If zone udn is empty, a new zone is created
Optional Parameter

roomUDN: The udn of the room that has to be put into that zone. If empty, all available rooms (rooms that have active renderers) are put into the zone.

connectRoomsToZone

Description

Puts the rooms with the given roomUDNs in the zone with the zoneUDN.
Outputs

XML containing the zone and its children like getZonesJob does.
Optional Parameter

zoneUDN: The udn of the zone to connect the rooms to. If zone udn is empty, a new zone is created
Optional Parameter

roomUDNs: A comma-separated list of UDNs of the rooms that have to be put into that zone. If empty, all available rooms (rooms that have active renderers) are put into the zone



'''
