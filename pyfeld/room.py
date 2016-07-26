from __future__ import unicode_literals

from pyfeld.upnpService import UpnpService

class Room:
    def __init__(self, udn, renderer_udn, name, location):
        self.name = name
        self.udn = udn
        self.renderer_udn = renderer_udn
        self.volume = 0
        self.mute = 0
        self.upnp_service = None
        self.location = location

    def set_volume(self, volume):
        self.volume = volume

    def set_name(self, name):
        self.name = name

    def get_name(self):
        return self.name

    def get_location(self):
        return self.location

    def get_udn(self):
        return self.udn

    def get_renderer_udn(self):
        return self.renderer_udn

    def get_volume(self):
        return self.volume

    def set_upnp_service(self, location):
        self.upnp_service = UpnpService()
        if location is not None:
            self.upnp_service.set_location(location)

    def set_event_update(self, udn, items_dict):
        assert(udn == self.udn)
        if 'Volume' in items_dict:
            self.volume = items_dict['Volume']
        if 'Mute' in items_dict:
            self.mute = items_dict['Mute']
